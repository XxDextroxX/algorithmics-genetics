"""Algoritmo genético: población de rutas (listas de nodos) de ORIGEN a DESTINO."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import config as C
from .city_map import CityMap


@dataclass
class Segment:
    """Tramo de la línea de tiempo de un bus: movimiento por una polilínea o espera."""

    t0: float
    t1: float
    pts: list[tuple[float, float]]
    cum: list[float]
    wait: bool = False
    reason: str = ""  # "ROJO" | "PARE" en esperas


@dataclass
class Individuo:
    ruta: list[int]
    tiempo: float = math.inf
    fitness: float = 0.0
    espera: float = 0.0
    timeline: list[Segment] = field(default_factory=list)

    def copy(self) -> "Individuo":
        return Individuo(list(self.ruta))


# ----------------------------------------------------------------- fitness
def evaluate(ind: Individuo, city: CityMap, t0: float) -> None:
    """Tiempo total = Σ distancia / (v / factor_tráfico) + penalizaciones (rojo + PARE).

    Los semáforos dependen del instante de llegada, así que la misma ruta puede valer
    distinto según cuándo salga: el AG se adapta al ciclo real de la ciudad.
    """
    t = 0.0
    espera = 0.0
    tl: list[Segment] = []
    prev_end = None
    ruta = ind.ruta
    for u, v in zip(ruta, ruta[1:]):
        e = city.edges[(u, v)]
        a, b = city.lane_points(e)
        pts = [prev_end, a, b] if prev_end else [a, b]
        cum = [0.0]
        for p, q in zip(pts, pts[1:]):
            cum.append(cum[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
        tt = e.travel_time()
        tl.append(Segment(t, t + tt, pts, cum))
        t += tt
        prev_end = b
        if v == city.destination:
            break
        wait, reason = 0.0, ""
        if e.tiene_pare:
            wait, reason = C.PARE_PENALTY_S, "PARE"
        light = city.lights.get(v)
        if light is not None:
            w = light.wait_time(e.axis, t0 + t)
            if w > 0:
                wait, reason = w, "ROJO"
        if wait > 0:
            tl.append(Segment(t, t + wait, [b], [0.0], True, reason))
            t += wait
            espera += wait
    ind.tiempo = t
    ind.fitness = 1.0 / t if t > 0 else 0.0
    ind.espera = espera
    ind.timeline = tl


# ---------------------------------------------------------------- operators
def random_walk(city: CityMap, start: int, rng: random.Random, forbidden: set[int] | None = None,
                greed: float | None = None) -> list[int] | None:
    """Caminata aleatoria sin ciclos (con retroceso) sesgada suavemente hacia el destino."""
    goal = city.destination
    greed = rng.uniform(0.0, 1.4) if greed is None else greed
    seen = set(forbidden or ()) | {start}
    path = [start]
    steps = 0
    while path[-1] != goal:
        steps += 1
        if steps > 4000:
            return None
        cur = path[-1]
        cands = [n for n in city.adj[cur] if n not in seen]
        if not cands:
            if len(path) == 1:
                return None
            path.pop()
            continue
        dc = city.dist_to_dest(cur)
        weights = [math.exp(greed * (dc - city.dist_to_dest(n)) / city.spacing) for n in cands]
        nxt = rng.choices(cands, weights)[0]
        path.append(nxt)
        seen.add(nxt)
    return path


def remove_loops(path: list[int]) -> list[int]:
    out: list[int] = []
    index: dict[int, int] = {}
    for n in path:
        if n in index:
            cut = index[n]
            for m in out[cut + 1:]:
                del index[m]
            out = out[:cut + 1]
        else:
            index[n] = len(out)
            out.append(n)
    return out


def crossover(p1: list[int], p2: list[int], rng: random.Random) -> tuple[list[int], list[int]]:
    """Cruce por nodo común: se intercambian los segmentos posteriores a una intersección compartida."""
    common = list(set(p1[1:-1]) & set(p2[1:-1]))
    if not common:
        return list(p1), list(p2)
    c = rng.choice(common)
    i1, i2 = p1.index(c), p2.index(c)
    return remove_loops(p1[:i1] + p2[i2:]), remove_loops(p2[:i2] + p1[i1:])


def mutate(path: list[int], city: CityMap, rng: random.Random) -> list[int]:
    """Desvío: desde un nodo intermedio se recalcula un camino aleatorio válido al destino."""
    if len(path) < 3:
        return path
    i = rng.randrange(1, len(path) - 1)
    tail = random_walk(city, path[i], rng, forbidden=set(path[:i]), greed=rng.uniform(0.4, 2.2))
    return path[:i] + tail if tail else path


# --------------------------------------------------------------- population
class Poblacion:
    def __init__(self, city: CityMap, params: dict | None = None, seed: int | None = None):
        self.city = city
        self.params = dict(C.GA_DEFAULTS, **(params or {}))
        self.rng = random.Random(seed)
        self.individuos: list[Individuo] = []
        self.generacion = 0
        self.history: list[tuple[float, float, float]] = []  # (mejor, promedio, peor)
        self.best_ever: float = math.inf

    @property
    def mejor(self) -> Individuo:
        return self.individuos[0]

    @property
    def peor(self) -> Individuo:
        return self.individuos[-1]

    def _random_individual(self) -> Individuo:
        while True:
            path = random_walk(self.city, self.city.origin, self.rng)
            if path:
                return Individuo(path)

    def initialize(self, t0: float) -> None:
        self.generacion = 0
        self.history.clear()
        self.best_ever = math.inf
        self.individuos = [self._random_individual() for _ in range(self.params["population_size"])]
        self._evaluate(t0)

    def _evaluate(self, t0: float) -> None:
        for ind in self.individuos:
            evaluate(ind, self.city, t0)
        self.individuos.sort(key=lambda i: i.tiempo)
        self.generacion += 1
        times = [i.tiempo for i in self.individuos]
        self.history.append((times[0], sum(times) / len(times), times[-1]))
        self.best_ever = min(self.best_ever, times[0])

    def _tournament(self) -> Individuo:
        k = min(self.params["tournament_k"], len(self.individuos))
        return min(self.rng.sample(self.individuos, k), key=lambda i: i.tiempo)

    def evolve(self, t0: float) -> None:
        p = self.params
        rng = self.rng
        n = p["population_size"]
        nxt = [ind.copy() for ind in self.individuos[: p["elitism"]]]
        while len(nxt) < n:
            a, b = self._tournament().ruta, self._tournament().ruta
            if rng.random() < p["crossover_rate"]:
                a, b = crossover(a, b, rng)
            for child in (a, b):
                if rng.random() < p["mutation_rate"]:
                    child = mutate(child, self.city, rng)
                if len(nxt) < n:
                    nxt.append(Individuo(list(child)))
        # Inmigración leve para mantener diversidad
        for i in range(max(1, n // 12)):
            nxt[-1 - i] = self._random_individual()
        self.individuos = nxt
        self._evaluate(t0)

    def diversity(self) -> float:
        return len({tuple(i.ruta) for i in self.individuos}) / max(1, len(self.individuos))
