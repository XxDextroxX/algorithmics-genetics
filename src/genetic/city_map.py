"""Grafo de la ciudad: nodos, aristas, semáforos, congestión dinámica y tráfico ambiental."""

from __future__ import annotations

import math
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field

from . import config as C

ORIGEN, DESTINO, INTERSECCION = "ORIGEN", "DESTINO", "INTERSECCION"
VERDE, AMBAR, ROJO, AUTO = "VERDE", "AMBAR", "ROJO", "AUTO"

HORIZONTAL, VERTICAL = 0, 1


@dataclass
class Node:
    id: int
    pos_x: float
    pos_y: float
    tipo: str = INTERSECCION
    col: int = 0
    row: int = 0

    @property
    def pos(self) -> tuple[float, float]:
        return (self.pos_x, self.pos_y)


@dataclass
class TrafficLight:
    """Semáforo de dos fases: los accesos horizontales y verticales alternan el verde."""

    nodo_id: int
    ciclo: float
    offset: float
    modo: str = AUTO  # AUTO | ROJO | VERDE (forzado vía API / clic)

    def _local(self, t: float) -> float:
        return (t + self.offset) % self.ciclo

    def estado(self, axis: int, t: float) -> str:
        if self.modo == ROJO:
            return ROJO
        if self.modo == VERDE:
            return VERDE
        half = self.ciclo / 2
        local = self._local(t)
        in_green = local < half if axis == HORIZONTAL else local >= half
        if not in_green:
            return ROJO
        remaining = (half - local) if axis == HORIZONTAL else (self.ciclo - local)
        return AMBAR if remaining < 5.0 else VERDE

    def wait_time(self, axis: int, t: float) -> float:
        """Segundos que espera un vehículo que llega en el instante t por ese eje."""
        if self.modo == VERDE:
            return 0.0
        if self.modo == ROJO:
            return C.FORCED_RED_PENALTY_S
        half = self.ciclo / 2
        local = self._local(t)
        if axis == HORIZONTAL:
            return 0.0 if local < half else self.ciclo - local
        return 0.0 if local >= half else half - local


@dataclass
class Edge:
    nodo_origen: int
    nodo_destino: int
    distancia: float
    limite_velocidad: float
    tiene_semaforo: bool = False
    tiene_pare: bool = False
    axis: int = HORIZONTAL
    avenida: bool = False
    puente: bool = False
    ancho: float = C.WIDTH_STREET
    congestion: float = 1.0
    congestion_target: float = 1.0
    jam_until: float = 0.0
    accidente: float = 0.0  # 0 = sin accidente; si no, factor impuesto

    @property
    def key(self) -> tuple[int, int]:
        return (self.nodo_origen, self.nodo_destino)

    @property
    def factor_trafico(self) -> float:
        return max(self.congestion, self.accidente)

    def travel_time(self) -> float:
        v = self.limite_velocidad / 3.6
        return self.distancia / (v / self.factor_trafico)


@dataclass
class Cell:
    """Manzana entre cuatro intersecciones (incluye anillo decorativo exterior)."""

    corners: list[tuple[float, float]]  # p00, p10, p11, p01
    kind: str  # buildings | park | plaza | water
    seed: int
    outer: bool = False


@dataclass
class Street:
    """Calle no dirigida, solo para dibujar."""

    a: tuple[float, float]
    b: tuple[float, float]
    ancho: float
    avenida: bool
    puente: bool
    decorativa: bool
    nodes: tuple[int, int] | None = None


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point_segment_distance(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return _dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


class CityMap:
    def __init__(self, world_w: float, world_h: float, cols: int = C.GRID_COLS, seed: int = C.CITY_SEED):
        self.world_w = world_w
        self.world_h = world_h
        self.rng = random.Random(seed)
        self.nodes: dict[int, Node] = {}
        self.edges: dict[tuple[int, int], Edge] = {}
        self.adj: dict[int, list[int]] = defaultdict(list)
        self.lights: dict[int, TrafficLight] = {}
        self.cells: list[Cell] = []
        self.streets: list[Street] = []
        self.origin = 0
        self.destination = 0
        self._generate(cols)

    # ------------------------------------------------------------------ build
    def _generate(self, cols: int) -> None:
        rng = self.rng
        sx = self.world_w / cols
        rows = max(5, round(self.world_h / sx))
        sy = self.world_h / rows
        self.cols, self.rows, self.sx, self.sy = cols, rows, sx, sy
        self.spacing = (sx + sy) / 2

        # Rejilla extendida (incluye anillo virtual fuera del grafo) con jitter orgánico
        grid: dict[tuple[int, int], tuple[float, float]] = {}
        for r in range(-1, rows + 1):
            for c in range(-1, cols + 1):
                jx = rng.uniform(-0.13, 0.13) * sx
                jy = rng.uniform(-0.13, 0.13) * sy
                grid[(c, r)] = ((c + 0.5) * sx + jx, (r + 0.5) * sy + jy)
        self.grid = grid

        self.avenue_rows = {1, rows - 2}
        self.avenue_cols = {cols // 5, cols // 2 - 1, (4 * cols) // 5}
        self.river_col = int(cols * 0.58)  # río entre river_col y river_col+1
        self.avenue_cols.discard(self.river_col)
        self.avenue_cols.discard(self.river_col + 1)
        self.bridge_rows = {1, rows // 2, rows - 2}

        nid = lambda c, r: r * cols + c  # noqa: E731
        for r in range(rows):
            for c in range(cols):
                x, y = grid[(c, r)]
                self.nodes[nid(c, r)] = Node(nid(c, r), x, y, INTERSECCION, c, r)

        # Calles candidatas (no dirigidas) con su clase
        candidates: list[tuple[int, int, str, bool]] = []
        street_class: dict[int, str] = {}
        for r in range(rows):
            for c in range(cols - 1):
                if c == self.river_col and r not in self.bridge_rows:
                    continue
                kind = "avenue" if r in self.avenue_rows else street_class.setdefault(r, rng.choice(["street", "minor"]))
                candidates.append((nid(c, r), nid(c + 1, r), kind, c == self.river_col))
        col_class: dict[int, str] = {}
        for c in range(cols):
            for r in range(rows - 1):
                kind = "avenue" if c in self.avenue_cols else col_class.setdefault(c, rng.choice(["street", "minor", "street"]))
                candidates.append((nid(c, r), nid(c, r + 1), kind, False))

        undirected = {(a, b): (k, br) for a, b, k, br in candidates}
        # Eliminar algunas calles menores (plazas peatonales) manteniendo conectividad
        removable = [k for k, (kind, br) in undirected.items() if kind != "avenue" and not br]
        rng.shuffle(removable)
        target_removed = int(len(removable) * 0.08)
        removed = 0
        for key in removable:
            if removed >= target_removed:
                break
            trial = {k: v for k, v in undirected.items() if k != key}
            if self._connected_min_degree(trial, 2):
                undirected = trial
                removed += 1

        speeds = {"avenue": C.SPEED_AVENUE, "street": C.SPEED_STREET, "minor": C.SPEED_MINOR}
        widths = {"avenue": C.WIDTH_AVENUE, "street": C.WIDTH_STREET, "minor": C.WIDTH_MINOR}
        for (a, b), (kind, bridge) in undirected.items():
            na, nb = self.nodes[a], self.nodes[b]
            d = _dist(na.pos, nb.pos)
            for u, v in ((a, b), (b, a)):
                nu, nv = self.nodes[u], self.nodes[v]
                axis = HORIZONTAL if abs(nv.pos_x - nu.pos_x) >= abs(nv.pos_y - nu.pos_y) else VERTICAL
                base = rng.uniform(1.0, 1.25)
                e = Edge(u, v, d, speeds[kind], axis=axis, avenida=kind == "avenue", puente=bridge,
                         ancho=widths[kind], congestion=base, congestion_target=base)
                self.edges[(u, v)] = e
                self.adj[u].append(v)
            self.streets.append(Street(na.pos, nb.pos, widths[kind], kind == "avenue", bridge, False, (a, b)))

        # Calles decorativas hacia el anillo exterior (la ciudad "continúa")
        for r in range(-1, rows + 1):
            for c in range(-1, cols + 1):
                inside = 0 <= c < cols and 0 <= r < rows
                for dc, dr in ((1, 0), (0, 1)):
                    c2, r2 = c + dc, r + dr
                    if (c2, r2) not in grid:
                        continue
                    inside2 = 0 <= c2 < cols and 0 <= r2 < rows
                    if inside and inside2:
                        continue
                    if dc == 1 and c == self.river_col:
                        continue
                    if dr == 1 and c in self.avenue_cols or dc == 1 and r in self.avenue_rows:
                        w, av = C.WIDTH_AVENUE, True
                    else:
                        w, av = C.WIDTH_STREET, False
                    self.streets.append(Street(grid[(c, r)], grid[(c2, r2)], w, av, False, True))

        # Semáforos y señales de PARE
        for n in self.nodes.values():
            deg = len(self.adj[n.id])
            on_av_r, on_av_c = n.row in self.avenue_rows, n.col in self.avenue_cols
            if deg < 3:
                continue
            p = 1.0 if (on_av_r and on_av_c) else 0.38 if (on_av_r or on_av_c) else 0.16
            if rng.random() < p:
                self.lights[n.id] = TrafficLight(n.id, rng.uniform(*C.LIGHT_CYCLE_RANGE), rng.uniform(0, 80))
        for e in self.edges.values():
            v = self.nodes[e.nodo_destino]
            if e.nodo_destino in self.lights:
                e.tiene_semaforo = True
            elif len(self.adj[v.id]) >= 3:
                on_av = v.row in self.avenue_rows or v.col in self.avenue_cols
                if (not e.avenida and on_av) or rng.random() < 0.14:
                    e.tiene_pare = True

        # Manzanas
        park_cells = set()
        while len(park_cells) < 4:
            c, r = rng.randrange(0, cols - 1), rng.randrange(0, rows - 1)
            if c != self.river_col:
                park_cells.add((c, r))
        plaza_cells = set()
        while len(plaza_cells) < 3:
            c, r = rng.randrange(0, cols - 1), rng.randrange(0, rows - 1)
            if c != self.river_col and (c, r) not in park_cells:
                plaza_cells.add((c, r))
        for r in range(-1, rows):
            for c in range(-1, cols):
                corners = [grid[(c, r)], grid[(c + 1, r)], grid[(c + 1, r + 1)], grid[(c, r + 1)]]
                outer = not (0 <= c < cols - 1 and 0 <= r < rows - 1)
                if c == self.river_col:
                    kind = "water"
                elif (c, r) in park_cells:
                    kind = "park"
                elif (c, r) in plaza_cells:
                    kind = "plaza"
                elif outer and rng.random() < 0.12:
                    kind = "park"
                else:
                    kind = "buildings"
                self.cells.append(Cell(corners, kind, rng.randrange(1 << 30), outer))

        mid = rows // 2
        self.set_route(nid(0, mid), nid(cols - 1, max(0, mid - 1)))

    @staticmethod
    def _connected_min_degree(undirected: dict, min_deg: int) -> bool:
        adj: dict[int, list[int]] = defaultdict(list)
        for a, b in undirected:
            adj[a].append(b)
            adj[b].append(a)
        if any(len(v) < min_deg for v in adj.values()):
            return False
        start = next(iter(adj))
        seen = {start}
        q = deque([start])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        return len(seen) == len(adj)

    # --------------------------------------------------------------- queries
    def set_route(self, origin: int, destination: int) -> None:
        for n in self.nodes.values():
            n.tipo = INTERSECCION
        self.origin, self.destination = origin, destination
        self.nodes[origin].tipo = ORIGEN
        self.nodes[destination].tipo = DESTINO

    def edge(self, u: int, v: int) -> Edge:
        return self.edges[(u, v)]

    def dist_to_dest(self, n: int) -> float:
        return _dist(self.nodes[n].pos, self.nodes[self.destination].pos)

    def lane_offset(self, e: Edge) -> float:
        return e.ancho * 0.24

    def lane_points(self, e: Edge, trim: float = 11.0) -> tuple[tuple[float, float], tuple[float, float]]:
        """Inicio y fin del carril derecho (tráfico por la derecha), recortado en las intersecciones."""
        a, b = self.nodes[e.nodo_origen].pos, self.nodes[e.nodo_destino].pos
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        nx, ny = -uy, ux
        off = self.lane_offset(e)
        return ((a[0] + ux * trim + nx * off, a[1] + uy * trim + ny * off),
                (b[0] - ux * trim + nx * off, b[1] - uy * trim + ny * off))

    def nearest_node(self, p: tuple[float, float], max_d: float) -> int | None:
        best, bd = None, max_d
        for n in self.nodes.values():
            d = _dist(p, n.pos)
            if d < bd:
                best, bd = n.id, d
        return best

    def nearest_street(self, p: tuple[float, float], max_d: float) -> tuple[int, int] | None:
        best, bd = None, max_d
        for s in self.streets:
            if s.nodes is None:
                continue
            d = point_segment_distance(p, s.a, s.b)
            if d < bd:
                best, bd = s.nodes, d
        return best

    # ------------------------------------------------------- dynamic traffic
    def update_traffic(self, sim_dt: float, clock: float) -> None:
        rng = self.rng
        # Nuevos atascos aleatorios (olas de congestión)
        if rng.random() < sim_dt / 25.0:
            e = rng.choice(list(self.edges.values()))
            dur = rng.uniform(90, 260)
            for ee in self._neighborhood(e, 2):
                level = rng.uniform(2.6, 4.2) if ee is e else rng.uniform(1.5, 2.6)
                ee.congestion_target = max(ee.congestion_target, level)
                ee.jam_until = clock + dur * rng.uniform(0.7, 1.0)
        k = min(1.0, sim_dt / 18.0)
        for e in self.edges.values():
            if e.jam_until and clock > e.jam_until:
                e.jam_until = 0.0
                e.congestion_target = rng.uniform(1.0, 1.25)
            e.congestion += (e.congestion_target - e.congestion) * k

    def _neighborhood(self, e: Edge, depth: int) -> list[Edge]:
        out = [e]
        frontier = [e.nodo_destino]
        prev = e.nodo_origen
        for _ in range(depth - 1):
            nxt = []
            for u in frontier:
                for v in self.adj[u]:
                    if v != prev:
                        ee = self.edges.get((u, v))
                        if ee and ee not in out and self.rng.random() < 0.6:
                            out.append(ee)
                            nxt.append(v)
            frontier = nxt
        return out

    def set_accident(self, u: int, v: int, factor: float, both: bool = True) -> None:
        for key in ((u, v), (v, u)) if both else ((u, v),):
            if key in self.edges:
                self.edges[key].accidente = factor

    def clear_accidents(self, u: int | None = None, v: int | None = None) -> int:
        n = 0
        for e in self.edges.values():
            if e.accidente and (u is None or {e.nodo_origen, e.nodo_destino} == {u, v}):
                e.accidente = 0.0
                n += 1
        return n

    def accidents(self) -> list[Edge]:
        seen, out = set(), []
        for e in self.edges.values():
            if e.accidente:
                k = frozenset(e.key)
                if k not in seen:
                    seen.add(k)
                    out.append(e)
        return out

    def topology(self) -> dict:
        return {
            "nodes": [{"id": n.id, "pos_x": round(n.pos_x, 1), "pos_y": round(n.pos_y, 1), "tipo": n.tipo,
                       "semaforo": n.id in self.lights} for n in self.nodes.values()],
            "edges": [{"origen": e.nodo_origen, "destino": e.nodo_destino, "distancia": round(e.distancia, 1),
                       "limite_velocidad": e.limite_velocidad, "factor_trafico": round(e.factor_trafico, 2),
                       "tiene_semaforo": e.tiene_semaforo, "tiene_pare": e.tiene_pare,
                       "avenida": e.avenida, "puente": e.puente} for e in self.edges.values()],
        }


# ---------------------------------------------------------------------------
# Tráfico ambiental (decorativo): autos que respetan semáforos y forman colas
# ---------------------------------------------------------------------------
@dataclass
class Car:
    edge: tuple[int, int]
    s: float
    speed: float
    color: tuple[int, int, int]
    length: float = 9.0
    x: float = 0.0
    y: float = 0.0
    hx: float = 1.0
    hy: float = 0.0


@dataclass
class AmbientTraffic:
    city: CityMap
    count: int = C.AMBIENT_CARS
    cars: list[Car] = field(default_factory=list)
    lanes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        rng = self.city.rng
        for e in self.city.edges.values():
            a, b = self.city.lane_points(e, trim=9.0)
            L = _dist(a, b)
            self.lanes[e.key] = (a, b, L, ((b[0] - a[0]) / L, (b[1] - a[1]) / L))
        keys = list(self.city.edges)
        for _ in range(self.count):
            k = rng.choice(keys)
            self.cars.append(Car(k, rng.uniform(0, self.lanes[k][2]), rng.uniform(0.75, 1.1),
                                 rng.choice(C.CAR_COLORS), rng.choice([8.0, 9.0, 9.0, 12.0])))

    def update(self, real_dt: float, clock: float) -> None:
        city = self.city
        rng = city.rng
        dt = real_dt * C.AMBIENT_TIME_SCALE
        by_edge: dict[tuple[int, int], list[Car]] = defaultdict(list)
        for car in self.cars:
            by_edge[car.edge].append(car)
        for key, cars in by_edge.items():
            cars.sort(key=lambda c: -c.s)
            e = city.edges[key]
            a, b, L, (hx, hy) = self.lanes[key]
            light = city.lights.get(e.nodo_destino)
            red = light is not None and light.estado(e.axis, clock) != VERDE
            leader_s = None
            for car in cars:
                v = e.limite_velocidad / 3.6 / e.factor_trafico * car.speed
                limit = L - 1.0 if red else L + 50
                if leader_s is not None:
                    limit = min(limit, leader_s - car.length - 4.0)
                new_s = min(car.s + v * dt, limit)
                car.s = max(car.s, new_s)
                if car.s >= L and not red:
                    outs = city.adj[e.nodo_destino]
                    choices = [w for w in outs if w != e.nodo_origen] or outs
                    car.edge = (e.nodo_destino, rng.choice(choices))
                    car.s = 0.0
                    na, nb, _, (nhx, nhy) = self.lanes[car.edge]
                    car.x, car.y, car.hx, car.hy = na[0], na[1], nhx, nhy
                    continue
                s = min(car.s, L)
                car.x, car.y, car.hx, car.hy = a[0] + hx * s, a[1] + hy * s, hx, hy
                leader_s = car.s
