"""Controlador de la simulación: reloj, ciclo de generaciones ("carreras") y comandos (API / UI)."""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass

from . import config as C
from .city_map import AUTO, ROJO, VERDE, AmbientTraffic, CityMap
from .genetic_algorithm import Individuo, Poblacion


@dataclass
class LogEntry:
    clock: float
    text: str
    color: tuple[int, int, int]
    source: str


@dataclass
class Pose:
    x: float
    y: float
    hx: float
    hy: float
    waiting: str = ""
    arrived: bool = False


def fmt_time(seconds: float) -> str:
    if seconds == float("inf"):
        return "--:--"
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def bus_pose(ind: Individuo, tau: float) -> Pose | None:
    tl = ind.timeline
    if not tl:
        return None
    if tau >= ind.tiempo:
        seg = tl[-1]
        p, q = seg.pts[-2] if len(seg.pts) > 1 else seg.pts[-1], seg.pts[-1]
        return Pose(q[0], q[1], *_heading(p, q), arrived=True)
    starts = getattr(ind, "_starts", None)
    if starts is None or len(starts) != len(tl):
        starts = [s.t0 for s in tl]
        ind._starts = starts  # type: ignore[attr-defined]
    i = max(0, bisect_right(starts, max(0.0, tau)) - 1)
    seg = tl[i]
    if seg.wait:
        prev = tl[i - 1] if i > 0 else None
        hx, hy = _heading(prev.pts[-2], prev.pts[-1]) if prev else (1.0, 0.0)
        return Pose(seg.pts[0][0], seg.pts[0][1], hx, hy, waiting=seg.reason)
    frac = 0.0 if seg.t1 <= seg.t0 else (tau - seg.t0) / (seg.t1 - seg.t0)
    target = max(0.0, min(1.0, frac)) * seg.cum[-1]
    j = max(1, min(len(seg.cum) - 1, bisect_right(seg.cum, target)))
    p, q = seg.pts[j - 1], seg.pts[j]
    seglen = seg.cum[j] - seg.cum[j - 1]
    k = 0.0 if seglen <= 0 else (target - seg.cum[j - 1]) / seglen
    hx, hy = _heading(p, q)
    return Pose(p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k, hx, hy)


def _heading(p, q) -> tuple[float, float]:
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = (dx * dx + dy * dy) ** 0.5
    return (1.0, 0.0) if L == 0 else (dx / L, dy / L)


class Simulation:
    def __init__(self, world_w: float, world_h: float):
        self.city = CityMap(world_w, world_h)
        self.ga = Poblacion(self.city, seed=11)
        self.traffic = AmbientTraffic(self.city)
        self.clock = 0.0
        self.real_time = 0.0
        self.time_scale = C.TIME_SCALE_DEFAULT
        self.paused = False
        self.turbo = False
        self.log: deque[LogEntry] = deque(maxlen=9)
        self.toast: tuple[str, tuple[int, int, int], float] | None = None
        self.markers: list[tuple[int, tuple[int, int, int]]] = []  # (generación, color) para la gráfica
        self.gen_version = 0
        self.gen_t0 = 0.0
        self.hold: float | None = None
        self.restart_requested = False
        self.last_arrival_real = -10.0
        self.ga.initialize(self.clock)
        self._start_race()
        self.add_log("Simulación iniciada: población de "
                     f"{self.ga.params['population_size']} buses", C.ORIGIN, "SYS")

    # ----------------------------------------------------------------- state
    @property
    def tau(self) -> float:
        return self.clock - self.gen_t0

    def add_log(self, text: str, color, source: str = "UI", toast: bool = False) -> None:
        self.log.appendleft(LogEntry(self.clock, text, color, source))
        if toast:
            self.toast = (text, color, self.real_time)

    def _start_race(self) -> None:
        self.gen_t0 = self.clock
        self.hold = None
        self.gen_version += 1

    def _next_generation(self) -> None:
        prev_best = self.ga.best_ever
        self.ga.evolve(self.clock)
        if self.ga.mejor.tiempo < prev_best - 0.5 and self.ga.generacion > 2 and not self.turbo:
            self.add_log(f"Nueva mejor ruta: {fmt_time(self.ga.mejor.tiempo)} "
                         f"(gen {self.ga.generacion})", C.NEON_BEST, "AG")
        self._start_race()

    def update(self, real_dt: float) -> None:
        self.real_time += real_dt
        if self.paused:
            return
        sim_dt = real_dt * self.time_scale
        self.clock += sim_dt
        self.city.update_traffic(sim_dt, self.clock)
        self.traffic.update(real_dt, self.clock)

        if self.turbo:  # una generación por frame: rápido pero aún se ve evolucionar la gráfica
            self._next_generation()
            self.restart_requested = False
            return

        if self.restart_requested:
            self.restart_requested = False
            self._next_generation()
            return

        best_t = self.ga.mejor.tiempo
        if self.tau >= best_t:
            if self.hold is None:
                self.hold = 0.0
                self.last_arrival_real = self.real_time
            self.hold += real_dt
            if self.hold >= C.RACE_HOLD_S or self.tau >= self.ga.peor.tiempo:
                self._next_generation()

    def status(self, api_online: bool) -> dict:
        ga = self.ga
        best, worst = ga.mejor, ga.peor
        times = [i.tiempo for i in ga.individuos]
        return {
            "generacion": ga.generacion,
            "estado": "pausa" if self.paused else "turbo" if self.turbo else "corriendo",
            "reloj_simulado_s": round(self.clock, 1),
            "velocidad_x": self.time_scale,
            "mejor_tiempo_s": round(best.tiempo, 1),
            "peor_tiempo_s": round(worst.tiempo, 1),
            "promedio_s": round(sum(times) / len(times), 1),
            "mejor_fitness": best.fitness,
            "mejor_historico_s": round(ga.best_ever, 1),
            "mejor_ruta": best.ruta,
            "rutas_activas": len({tuple(i.ruta) for i in ga.individuos}),
            "diversidad": round(ga.diversity(), 3),
            "origen": self.city.origin,
            "destino": self.city.destination,
            "parametros": ga.params,
            "accidentes": [{"origen": e.nodo_origen, "destino": e.nodo_destino, "factor": e.accidente}
                           for e in self.city.accidents()],
            "semaforos_forzados": {n: l.modo for n, l in self.city.lights.items() if l.modo != AUTO},
            "eventos": [e.text for e in self.log],
            "api_online": api_online,
        }

    # -------------------------------------------------------------- commands
    def apply(self, cmd: dict, source: str = "API") -> None:
        kind = cmd.get("type")
        city = self.city
        if kind == "accident":
            u, v = cmd["origen"], cmd["destino"]
            if (u, v) not in city.edges:
                return
            f = float(cmd.get("factor_trafico", 10.0))
            city.set_accident(u, v, f, cmd.get("bidireccional", True))
            self.add_log(f"¡Accidente en calle ({u} -> {v})!  tráfico x{f:g}", C.ACCIDENT, source, toast=True)
            self.markers.append((self.ga.generacion, C.ACCIDENT))
            self.restart_requested = True
        elif kind == "accident_random":
            ruta = self.ga.mejor.ruta
            pairs = list(zip(ruta[1:-2], ruta[2:-1])) or list(zip(ruta, ruta[1:]))
            pairs = [p for p in pairs if not city.edges[p].accidente] or pairs
            u, v = city.rng.choice(pairs)
            self.apply({"type": "accident", "origen": u, "destino": v, "factor_trafico": 10.0}, source)
        elif kind == "clear":
            u, v = cmd.get("origen"), cmd.get("destino")
            n = city.clear_accidents(u, v) if u is not None and v is not None else city.clear_accidents()
            if n:
                where = f"calle ({u} -> {v})" if u is not None else "todas las calles"
                self.add_log(f"Vía despejada: {where}", C.DEST, source, toast=True)
                self.markers.append((self.ga.generacion, C.DEST))
                self.restart_requested = True
        elif kind == "semaphore":
            light = city.lights.get(cmd["nodo_id"])
            if light is None:
                return
            light.modo = cmd["estado"]
            color = {ROJO: C.LIGHT_RED, VERDE: C.LIGHT_GREEN}.get(light.modo, C.TEXT_DIM)
            label = "ciclo automático" if light.modo == AUTO else f"forzado a {light.modo}"
            self.add_log(f"Semáforo en nodo {light.nodo_id}: {label}", color, source, toast=True)
            self.markers.append((self.ga.generacion, color))
            self.restart_requested = True
        elif kind == "params":
            params = cmd["params"]
            self.ga.params.update(params)
            txt = ", ".join(f"{k}={v}" for k, v in params.items())
            self.add_log(f"Parámetros AG: {txt}", C.CHART_AVG, source, toast=True)
        elif kind == "route":
            u, v = cmd["origen"], cmd["destino"]
            if u == v or u not in city.nodes or v not in city.nodes:
                return
            city.set_route(u, v)
            self.reset_population()
            self.add_log(f"Nueva ruta: nodo {u} -> nodo {v}", C.ORIGIN, source, toast=True)
        elif kind == "sim":
            self._sim_action(cmd["action"], source)

    def reset_population(self) -> None:
        self.markers.clear()
        self.ga.initialize(self.clock)
        self._start_race()

    def _sim_action(self, action: str, source: str) -> None:
        steps = C.TIME_SCALE_STEPS
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
        elif action == "turbo":
            self.turbo = True
            self.add_log("Modo TURBO: evolución acelerada", C.LIGHT_AMBER, source)
        elif action == "normal":
            if self.turbo:
                self.turbo = False
                self._start_race()
                self.add_log(f"Modo carrera (gen {self.ga.generacion})", C.TEXT_DIM, source)
        elif action == "reset":
            self.reset_population()
            self.add_log("Población reiniciada", C.TEXT_DIM, source, toast=True)
        elif action in ("faster", "slower"):
            i = min(range(len(steps)), key=lambda k: abs(steps[k] - self.time_scale))
            i = min(len(steps) - 1, i + 1) if action == "faster" else max(0, i - 1)
            self.time_scale = steps[i]
