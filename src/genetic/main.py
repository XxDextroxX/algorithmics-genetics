"""Punto de entrada: FastAPI en un hilo secundario y Pygame en el hilo principal."""

from __future__ import annotations

import argparse
import os
import queue
import sys

import pygame

from . import config as C
from .api_routes import ApiServer, event_queue, shared
from .city_map import AUTO, ROJO, VERDE
from .renderer import Renderer, compute_layout
from .simulation import Simulation

NEXT_LIGHT_MODE = {AUTO: ROJO, ROJO: VERDE, VERDE: AUTO}


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GenRoute: demo de algoritmo genético para tráfico urbano")
    p.add_argument("--fullscreen", action="store_true", help="pantalla completa")
    p.add_argument("--width", type=int, help="ancho de ventana (por defecto: casi todo el escritorio)")
    p.add_argument("--height", type=int, help="alto de ventana")
    p.add_argument("--port", type=int, default=C.API_PORT, help="puerto de la API (8000)")
    p.add_argument("--no-api", action="store_true", help="no levantar FastAPI")
    return p.parse_args(argv)


def run(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    _enable_dpi_awareness()
    os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
    pygame.init()
    desk_w, desk_h = pygame.display.get_desktop_sizes()[0]
    if args.fullscreen:
        W, H = desk_w, desk_h
        screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
    else:
        W = args.width or desk_w - 24
        H = args.height or desk_h - 96
        screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("GenRoute · Algoritmo genético para tráfico urbano")

    layout = compute_layout(W, H)
    map_rect = layout[1]
    world_h = C.WORLD_WIDTH_M * map_rect.h / map_rect.w
    sim = Simulation(C.WORLD_WIDTH_M, world_h)

    api = None if args.no_api else ApiServer(port=args.port).start()
    shared.set_topology(sim.city.topology())

    renderer = Renderer(screen, sim, api, layout)
    clock = pygame.time.Clock()
    status_t = topo_t = 0.0
    running = True
    api_reported = False

    while running:
        dt = min(clock.tick(60) / 1000.0, 0.05)
        mods = pygame.key.get_mods()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.MOUSEMOTION:
                renderer.mouse = ev.pos
            elif ev.type == pygame.MOUSEBUTTONDOWN and renderer.map_rect.collidepoint(ev.pos):
                _handle_click(ev, mods, sim, renderer)
            elif ev.type == pygame.KEYDOWN:
                running = _handle_key(ev, sim, renderer)

        while True:  # comandos llegados por FastAPI
            try:
                sim.apply(event_queue.get_nowait(), "API")
            except queue.Empty:
                break

        sim.update(dt)
        renderer.draw(dt)
        pygame.display.flip()

        status_t += dt
        topo_t += dt
        online = api is not None and api.online
        if api is not None and not api_reported and (online or api.error):
            api_reported = True
            if online:
                sim.add_log(f"API escuchando en http://{api.host}:{api.port}  (/docs)", C.CHART_AVG, "SYS")
            else:
                sim.add_log(f"API no disponible: {api.error}", C.ACCIDENT, "SYS")
        if status_t > 0.25:
            status_t = 0.0
            shared.set_status(sim.status(online))
        if topo_t > 2.0:
            topo_t = 0.0
            shared.set_topology(sim.city.topology())

    if api is not None:
        api.stop()
    pygame.quit()


def _handle_click(ev, mods, sim: Simulation, renderer: Renderer) -> None:
    city = sim.city
    wp = renderer.screen_to_world(ev.pos)
    node = city.nearest_node(wp, 16 / renderer.scale)
    if ev.button == 1 and mods & pygame.KMOD_SHIFT:
        if node is not None and node != city.destination:
            sim.apply({"type": "route", "origen": node, "destino": city.destination}, "UI")
    elif ev.button == 1 and mods & pygame.KMOD_CTRL:
        if node is not None and node != city.origin:
            sim.apply({"type": "route", "origen": city.origin, "destino": node}, "UI")
    elif ev.button == 1:
        key = city.nearest_street(wp, 12 / renderer.scale)
        if key is None:
            return
        u, v = key
        if city.edges[(u, v)].accidente:
            sim.apply({"type": "clear", "origen": u, "destino": v}, "UI")
        else:
            sim.apply({"type": "accident", "origen": u, "destino": v, "factor_trafico": 10.0}, "UI")
    elif ev.button == 3 and node is not None and node in city.lights:
        mode = NEXT_LIGHT_MODE[city.lights[node].modo]
        sim.apply({"type": "semaphore", "nodo_id": node, "estado": mode}, "UI")


def _handle_key(ev, sim: Simulation, renderer: Renderer) -> bool:
    k = ev.key
    act = lambda a: sim.apply({"type": "sim", "action": a}, "UI")  # noqa: E731
    if k == pygame.K_ESCAPE:
        if renderer.show_help:
            renderer.show_help = False
        else:
            return False
    elif k == pygame.K_SPACE:
        act("resume" if sim.paused else "pause")
    elif k == pygame.K_t:
        act("normal" if sim.turbo else "turbo")
    elif k in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
        act("faster")
    elif k in (pygame.K_MINUS, pygame.K_KP_MINUS):
        act("slower")
    elif k == pygame.K_a:
        sim.apply({"type": "accident_random"}, "UI")
    elif k == pygame.K_c:
        sim.apply({"type": "clear"}, "UI")
    elif k == pygame.K_r:
        act("reset")
    elif k == pygame.K_i:
        renderer.show_ids = not renderer.show_ids
    elif k == pygame.K_l:
        renderer.show_legend = not renderer.show_legend
    elif k == pygame.K_h:
        renderer.show_help = not renderer.show_help
    return True


if __name__ == "__main__":
    run()
