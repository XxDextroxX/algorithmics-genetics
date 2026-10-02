"""FastAPI: control remoto de la simulación mediante una cola thread-safe hacia el hilo de Pygame."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import config as C

# Cola de comandos API -> Pygame (se procesa en cada frame del hilo principal)
event_queue: "queue.Queue[dict]" = queue.Queue()


class SharedState:
    """Instantánea de métricas escrita por Pygame y leída por FastAPI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict = {}
        self._topology: dict = {"nodes": [], "edges": []}
        self.edge_keys: set[tuple[int, int]] = set()
        self.node_ids: set[int] = set()
        self.light_ids: set[int] = set()

    def set_topology(self, topology: dict) -> None:
        with self._lock:
            self._topology = topology
            self.edge_keys = {(e["origen"], e["destino"]) for e in topology["edges"]}
            self.node_ids = {n["id"] for n in topology["nodes"]}
            self.light_ids = {n["id"] for n in topology["nodes"] if n["semaforo"]}

    def set_status(self, status: dict) -> None:
        with self._lock:
            self._status = status

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def topology(self) -> dict:
        with self._lock:
            return self._topology


shared = SharedState()


# ------------------------------------------------------------------ schemas
class AccidentIn(BaseModel):
    origen: int
    destino: int
    factor_trafico: float = Field(10.0, ge=1.0, le=100.0)
    bidireccional: bool = True


class ClearIn(BaseModel):
    origen: int | None = None
    destino: int | None = None


class SemaphoreIn(BaseModel):
    nodo_id: int
    estado: Literal["ROJO", "VERDE", "AUTO"]


class GAParamsIn(BaseModel):
    mutation_rate: float | None = Field(None, ge=0.0, le=1.0)
    population_size: int | None = Field(None, ge=6, le=200)
    crossover_rate: float | None = Field(None, ge=0.0, le=1.0)
    elitism: int | None = Field(None, ge=0, le=10)


class RouteIn(BaseModel):
    origen: int
    destino: int


class SimulationIn(BaseModel):
    action: Literal["pause", "resume", "turbo", "normal", "reset", "faster", "slower"]


def _queued(cmd: dict, msg: str) -> dict:
    event_queue.put(cmd)
    return {"ok": True, "queued": cmd["type"], "detalle": msg}


# ------------------------------------------------------------------- app
app = FastAPI(
    title="GenRoute · Control de Simulación",
    description="Manipula en tiempo real la ciudad y el algoritmo genético que corre en Pygame.",
    version="1.0.0",
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def control_panel() -> str:
    return (Path(__file__).parent / "web_panel.html").read_text(encoding="utf-8")


@app.get("/api/status", tags=["Monitoreo"])
def get_status() -> dict:
    """Métricas de la generación actual (mejor fitness, generación, rutas activas…)."""
    return shared.status()


@app.get("/api/map", tags=["Monitoreo"])
def get_map() -> dict:
    """Topología del grafo: nodos (con semáforo o no) y aristas con su factor de tráfico."""
    return shared.topology()


@app.post("/api/accident", tags=["Entorno"])
def post_accident(body: AccidentIn) -> dict:
    """Dispara congestión extrema / bloqueo en la calle origen → destino."""
    if (body.origen, body.destino) not in shared.edge_keys:
        raise HTTPException(404, f"No existe la calle {body.origen} -> {body.destino}. Consulta GET /api/map")
    return _queued({"type": "accident", **body.model_dump()},
                   f"Accidente en calle ({body.origen} -> {body.destino}) x{body.factor_trafico}")


@app.post("/api/accident/random", tags=["Entorno"])
def post_random_accident() -> dict:
    """Accidente en una calle aleatoria (preferentemente sobre la mejor ruta actual)."""
    return _queued({"type": "accident_random"}, "Accidente aleatorio")


@app.post("/api/accident/clear", tags=["Entorno"])
def post_clear(body: ClearIn | None = None) -> dict:
    """Despeja una calle concreta o, sin body, todos los accidentes."""
    body = body or ClearIn()
    return _queued({"type": "clear", **body.model_dump()}, "Despejar accidentes")


@app.post("/api/semaphore/toggle", tags=["Entorno"])
def post_semaphore(body: SemaphoreIn) -> dict:
    """Fuerza un semáforo a ROJO / VERDE o lo devuelve a su ciclo AUTO."""
    if body.nodo_id not in shared.light_ids:
        raise HTTPException(404, f"El nodo {body.nodo_id} no tiene semáforo. Nodos con semáforo: "
                                 f"{sorted(shared.light_ids)}")
    return _queued({"type": "semaphore", **body.model_dump()}, f"Semáforo {body.nodo_id} -> {body.estado}")


@app.post("/api/ga/params", tags=["Algoritmo genético"])
def post_params(body: GAParamsIn) -> dict:
    """Actualiza parámetros del AG al vuelo (aplican en la siguiente generación)."""
    params = body.model_dump(exclude_none=True)
    if not params:
        raise HTTPException(400, "Envía al menos un parámetro")
    return _queued({"type": "params", "params": params}, f"Parámetros: {params}")


@app.post("/api/route", tags=["Algoritmo genético"])
def post_route(body: RouteIn) -> dict:
    """Cambia ORIGEN y DESTINO y reinicia la población."""
    if body.origen not in shared.node_ids or body.destino not in shared.node_ids or body.origen == body.destino:
        raise HTTPException(400, "Nodos inválidos")
    return _queued({"type": "route", **body.model_dump()}, f"Ruta {body.origen} -> {body.destino}")


@app.post("/api/simulation", tags=["Simulación"])
def post_simulation(body: SimulationIn) -> dict:
    """pause | resume | turbo | normal | reset | faster | slower"""
    return _queued({"type": "sim", "action": body.action}, body.action)


# ---------------------------------------------------------------- server
class ApiServer:
    def __init__(self, host: str = C.API_HOST, port: int = C.API_PORT):
        self.host, self.port = host, port
        self.error: str | None = None
        cfg = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self._run, name="fastapi", daemon=True)

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:  # puerto ocupado, etc.: la demo sigue sin API
            self.error = str(exc) or exc.__class__.__name__

    @property
    def online(self) -> bool:
        return self.server.started and self.error is None

    def start(self) -> "ApiServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.should_exit = True
