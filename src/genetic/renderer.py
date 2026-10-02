"""Render Pygame: ciudad nocturna estilo neón, población de buses, HUD y gráfica evolutiva."""

from __future__ import annotations

import math
import random
from bisect import bisect_right

import pygame

from . import config as C
from .city_map import AMBAR, AUTO, ROJO, VERDE, CityMap
from .simulation import Simulation, bus_pose, fmt_time

Vec = tuple[float, float]


# ------------------------------------------------------------------ helpers
def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(c1, c2, t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (int(lerp(c1[0], c2[0], t)), int(lerp(c1[1], c2[1], t)), int(lerp(c1[2], c2[2], t)))


def shade(c, k: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(v * k))) for v in c)  # type: ignore[return-value]


def bilinear(q, u: float, v: float) -> Vec:
    p00, p10, p11, p01 = q
    tx, ty = lerp(p00[0], p10[0], u), lerp(p00[1], p10[1], u)
    bx, by = lerp(p01[0], p11[0], u), lerp(p01[1], p11[1], u)
    return (lerp(tx, bx, v), lerp(ty, by, v))


def dist(a: Vec, b: Vec) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def thick_line(surf, color, a: Vec, b: Vec, w: float) -> None:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L == 0:
        return
    nx, ny = -dy / L * w / 2, dx / L * w / 2
    pygame.draw.polygon(surf, color, [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                                      (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)])


def thick_polyline(surf, color, pts: list[Vec], w: float) -> None:
    if len(pts) < 2:
        return
    for a, b in zip(pts, pts[1:]):
        thick_line(surf, color, a, b, w)
    r = max(1, int(w / 2))
    for p in pts:
        pygame.draw.circle(surf, color, (int(p[0]), int(p[1])), r)


def rect_poly(cx: float, cy: float, hx: float, hy: float, L: float, W: float) -> list[Vec]:
    fx, fy = hx * L / 2, hy * L / 2
    sx, sy = -hy * W / 2, hx * W / 2
    return [(cx + fx + sx, cy + fy + sy), (cx + fx - sx, cy + fy - sy),
            (cx - fx - sx, cy - fy - sy), (cx - fx + sx, cy - fy + sy)]


def catmull(points: list[Vec], steps: int = 8) -> list[Vec]:
    out = []
    pts = [points[0]] + points + [points[-1]]
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in (0, 1)))
    out.append(points[-1])
    return out  # type: ignore[return-value]


def load_font(names: list[str], size: int, bold: bool = False) -> pygame.font.Font:
    for n in names:
        path = pygame.font.match_font(n, bold=bold)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, int(size * 1.3))


def compute_layout(W: int, H: int) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect]:
    top_h = max(64, min(92, int(H * 0.078)))
    bottom_h = max(138, min(210, int(H * 0.175)))
    m = 14
    top = pygame.Rect(m, 8, W - 2 * m, top_h - 8)
    map_rect = pygame.Rect(m, top_h + 6, W - 2 * m, H - top_h - bottom_h - 18)
    bottom = pygame.Rect(m, map_rect.bottom + 10, W - 2 * m, H - map_rect.bottom - 10 - 10)
    return top, map_rect, bottom


# ----------------------------------------------------------------- renderer
class Renderer:
    def __init__(self, screen: pygame.Surface, sim: Simulation, api, layout):
        self.screen = screen
        self.sim = sim
        self.api = api
        self.top, self.map_rect, self.bottom = layout
        self.scale = self.map_rect.w / sim.city.world_w
        self.ui = max(0.8, min(1.35, screen.get_height() / 1000))
        self.t = 0.0
        self.show_ids = False
        self.show_help = False
        self.show_legend = True
        self.mouse: tuple[int, int] = (0, 0)

        ui = self.ui
        sans = ["segoeui", "helveticaneue", "helvetica", "arial", "dejavusans"]
        semi = ["segoeuisemibold", "seguisb", "segoeui", "arial"]
        mono = ["cascadiamono", "consolas", "menlo", "dejavusansmono", "couriernew"]
        self.f_title = load_font(semi, int(27 * ui), bold=True)
        self.f_sub = load_font(sans, int(13 * ui))
        self.f_val = load_font(semi, int(23 * ui), bold=True)
        self.f_lbl = load_font(semi, int(10.5 * ui), bold=True)
        self.f_txt = load_font(sans, int(13.5 * ui))
        self.f_txt_b = load_font(semi, int(13.5 * ui), bold=True)
        self.f_mono = load_font(mono, int(12 * ui))
        self.f_toast = load_font(semi, int(19 * ui), bold=True)
        self.f_tiny = load_font(semi, int(10 * ui), bold=True)

        self._text_cache: dict = {}
        self._glow_cache: dict = {}
        self.fx = pygame.Surface(self.map_rect.size, pygame.SRCALPHA)
        self.traffic_surf = pygame.Surface(self.map_rect.size, pygame.SRCALPHA)
        self.heat_surf = pygame.Surface(self.map_rect.size, pygame.SRCALPHA)
        self.best_surf = pygame.Surface(self.map_rect.size, pygame.SRCALPHA)
        self._traffic_t = -1.0
        self._gen_seen = -1
        self._gen_built_t = -1.0
        self.best_pts: list[Vec] = []
        self.best_cum: list[float] = [0.0]

        self._precompute_lights()
        self.background = self._build_background()

    # ---------------------------------------------------------- coordinates
    def L(self, p: Vec) -> Vec:
        """Mundo -> coordenadas locales del mapa."""
        return (p[0] * self.scale, p[1] * self.scale)

    def S(self, p: Vec) -> Vec:
        """Mundo -> pantalla."""
        return (self.map_rect.x + p[0] * self.scale, self.map_rect.y + p[1] * self.scale)

    def screen_to_world(self, pos: tuple[int, int]) -> Vec:
        return ((pos[0] - self.map_rect.x) / self.scale, (pos[1] - self.map_rect.y) / self.scale)

    # -------------------------------------------------------------- caches
    def text(self, font, s: str, color) -> pygame.Surface:
        key = (id(font), s, color)
        surf = self._text_cache.get(key)
        if surf is None:
            if len(self._text_cache) > 3000:
                self._text_cache.clear()
            surf = font.render(s, True, color)
            self._text_cache[key] = surf
        return surf

    def glow(self, radius: int, color, strength: float = 1.0) -> pygame.Surface:
        key = (radius, color, strength)
        g = self._glow_cache.get(key)
        if g is None:
            size = radius * 2 + 2
            g = pygame.Surface((size, size))
            g.fill((0, 0, 0))
            for r in range(radius, 0, -1):
                k = (1 - r / radius) ** 2 * strength
                pygame.draw.circle(g, tuple(min(255, int(ch * k)) for ch in color), (radius + 1, radius + 1), r)
            self._glow_cache[key] = g
        return g

    def blit_glow(self, pos: Vec, radius: int, color, strength: float = 1.0) -> None:
        g = self.glow(max(2, radius), color, strength)
        r = max(2, radius) + 1
        self.screen.blit(g, (pos[0] - r, pos[1] - r), special_flags=pygame.BLEND_RGB_ADD)

    # ------------------------------------------------------- static layers
    def _precompute_lights(self) -> None:
        city = self.sim.city
        self.light_bars = []  # (light, axis, p_screen_a, p_screen_b, center)
        for e in city.edges.values():
            light = city.lights.get(e.nodo_destino)
            if light is None:
                continue
            _, b = city.lane_points(e, trim=12.0)
            a0, b0 = city.nodes[e.nodo_origen].pos, city.nodes[e.nodo_destino].pos
            Lg = dist(a0, b0) or 1
            nx, ny = -(b0[1] - a0[1]) / Lg, (b0[0] - a0[0]) / Lg
            half = e.ancho * 0.24
            pa = self.S((b[0] + nx * half, b[1] + ny * half))
            pb = self.S((b[0] - nx * half, b[1] - ny * half))
            self.light_bars.append((light, e.axis, pa, pb, self.S(b)))

    def _build_background(self) -> pygame.Surface:
        SS = 2
        city = self.sim.city
        s = self.scale * SS
        w, h = self.map_rect.size
        surf = pygame.Surface((w * SS, h * SS))
        surf.fill(C.GROUND)
        P = lambda p: (p[0] * s, p[1] * s)  # noqa: E731

        for cell in city.cells:
            if cell.kind != "water":
                self._draw_cell(surf, cell, P, s)
        self._draw_river(surf, P, s, city)
        self._draw_streets(surf, P, s, city)

        bg = pygame.transform.smoothscale(surf, (w, h)).convert_alpha()
        # Viñeta: oscurece el anillo exterior para centrar la mirada
        vw, vh = 120, 60
        vig = pygame.Surface((vw, vh), pygame.SRCALPHA)
        for y in range(vh):
            for x in range(vw):
                dx, dy = abs((x + 0.5) / vw * 2 - 1), abs((y + 0.5) / vh * 2 - 1)
                d = (dx ** 5 + dy ** 5) ** 0.2
                a = max(0.0, min(1.0, (d - 0.80) / 0.20)) ** 1.4 * 200
                vig.set_at((x, y), (6, 7, 16, int(a)))
        bg.blit(pygame.transform.smoothscale(vig, (w, h)), (0, 0))
        mask = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=int(16 * self.ui))
        bg.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        return bg

    def _draw_cell(self, surf, cell, P, s) -> None:
        rng = random.Random(cell.seed)
        q = cell.corners
        wu = max(1.0, dist(q[0], q[1]))
        hv = max(1.0, dist(q[0], q[3]))

        def sub(u0, v0, u1, v1, shift=(0.0, 0.0)):
            return [(P(bilinear(q, u, v))[0] + shift[0], P(bilinear(q, u, v))[1] + shift[1])
                    for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]

        pygame.draw.polygon(surf, C.SIDEWALK, sub(0, 0, 1, 1))
        d = 16.0
        du, dv = d / wu, d / hv
        u0, v0, u1, v1 = du, dv, 1 - du, 1 - dv

        if cell.kind == "park":
            pygame.draw.polygon(surf, C.PARK, sub(u0, v0, u1, v1))
            for a, b in (((u0, v0), (u1, v1)), ((u0, v1), (u1, v0)), ((0.5, v0), (0.5, v1))):
                thick_line(surf, C.PARK_PATH, P(bilinear(q, *a)), P(bilinear(q, *b)), 2.6 * s)
            if rng.random() < 0.5:
                c = P(bilinear(q, rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)))
                rw, rh = rng.uniform(18, 30) * s, rng.uniform(12, 20) * s
                pygame.draw.ellipse(surf, C.WATER, (c[0] - rw, c[1] - rh, 2 * rw, 2 * rh))
                pygame.draw.ellipse(surf, C.WATER_SHINE, (c[0] - rw, c[1] - rh, 2 * rw, 2 * rh), max(1, int(s)))
            for _ in range(int(wu * hv / 520)):
                c = P(bilinear(q, rng.uniform(u0 + 0.03, u1 - 0.03), rng.uniform(v0 + 0.03, v1 - 0.03)))
                r = rng.uniform(3.5, 7.5) * s
                pygame.draw.circle(surf, C.ROOF_SHADOW, (c[0] + r * 0.35, c[1] + r * 0.45), r)
                pygame.draw.circle(surf, C.TREE_DARK, c, r)
                pygame.draw.circle(surf, C.TREE_LIGHT, (c[0] - r * 0.25, c[1] - r * 0.3), r * 0.55)
            return

        if cell.kind == "plaza":
            pygame.draw.polygon(surf, C.PLAZA, sub(u0, v0, u1, v1))
            for k in range(1, 6):
                t = k / 6
                thick_line(surf, shade(C.PLAZA, 1.15), P(bilinear(q, lerp(u0, u1, t), v0)),
                           P(bilinear(q, lerp(u0, u1, t), v1)), 0.6 * s)
                thick_line(surf, shade(C.PLAZA, 1.15), P(bilinear(q, u0, lerp(v0, v1, t))),
                           P(bilinear(q, u1, lerp(v0, v1, t))), 0.6 * s)
            c = P(bilinear(q, 0.5, 0.5))
            pygame.draw.circle(surf, shade(C.PLAZA, 1.35), c, 17 * s)
            pygame.draw.circle(surf, C.WATER, c, 13 * s)
            pygame.draw.circle(surf, C.WATER_SHINE, c, 5 * s)
            for k in range(4):
                cc = P(bilinear(q, (u0, u1)[k % 2] + (0.08 if k % 2 == 0 else -0.08),
                                (v0, v1)[k // 2] + (0.1 if k // 2 == 0 else -0.1)))
                pygame.draw.circle(surf, C.TREE_DARK, cc, 6 * s)
                pygame.draw.circle(surf, C.TREE_LIGHT, (cc[0] - 1.5 * s, cc[1] - 1.5 * s), 3.2 * s)
            return

        # Edificios: lotes con sombra (falsa altura), azotea y luces
        nu, nv = rng.choice([2, 2, 3]), rng.choice([2, 2, 3])
        us = [u0] + sorted(lerp(u0, u1, (i + rng.uniform(-0.18, 0.18)) / nu) for i in range(1, nu)) + [u1]
        vs = [v0] + sorted(lerp(v0, v1, (j + rng.uniform(-0.18, 0.18)) / nv) for j in range(1, nv)) + [v1]
        gu, gv = 2.4 / wu, 2.4 / hv
        for i in range(nu):
            for j in range(nv):
                a0, a1 = us[i] + gu, us[i + 1] - gu
                b0, b1 = vs[j] + gv, vs[j + 1] - gv
                if a1 <= a0 or b1 <= b0:
                    continue
                if rng.random() < 0.07:  # estacionamiento
                    pygame.draw.polygon(surf, (30, 34, 56), sub(a0, b0, a1, b1))
                    for k in range(1, 5):
                        t = lerp(a0, a1, k / 5)
                        thick_line(surf, (62, 68, 98), P(bilinear(q, t, b0)),
                                   P(bilinear(q, t, lerp(b0, b1, 0.35))), 0.5 * s)
                    continue
                height = rng.uniform(0.5, 2.4)
                off = height * 3.2 * s
                pygame.draw.polygon(surf, C.ROOF_SHADOW, sub(a0, b0, a1, b1, (off, off * 1.25)))
                base = rng.choice(C.ROOFS)
                roof = shade(base, 0.9 + height * 0.12)
                poly = sub(a0, b0, a1, b1)
                pygame.draw.polygon(surf, roof, poly)
                hi = shade(roof, 1.3)
                pygame.draw.lines(surf, hi, False, [poly[3], poly[0], poly[1]], max(1, int(0.7 * s)))
                pygame.draw.lines(surf, shade(roof, 0.7), False, [poly[1], poly[2], poly[3]], max(1, int(0.7 * s)))
                if rng.random() < 0.45:
                    c = P(bilinear(q, lerp(a0, a1, rng.uniform(0.3, 0.7)), lerp(b0, b1, rng.uniform(0.3, 0.7))))
                    k = rng.uniform(3, 6) * s
                    pygame.draw.rect(surf, shade(roof, 0.75), (c[0] - k, c[1] - k * 0.7, 2 * k, 1.4 * k))
                area = (a1 - a0) * wu * (b1 - b0) * hv
                for _ in range(int(area / rng.uniform(90, 260))):
                    c = P(bilinear(q, lerp(a0, a1, rng.uniform(0.1, 0.9)), lerp(b0, b1, rng.uniform(0.1, 0.9))))
                    col = C.WINDOW_WARM if rng.random() < 0.7 else C.WINDOW_COOL
                    col = shade(col, rng.uniform(0.45, 1.0))
                    k = 1.0 * s
                    pygame.draw.rect(surf, col, (c[0], c[1], k * 1.6, k))

    def _draw_river(self, surf, P, s, city: CityMap) -> None:
        rc = city.river_col
        rows = city.rows
        left = [city.grid[(rc, r)] for r in range(-1, rows + 1)]
        right = [city.grid[(rc + 1, r)] for r in range(-1, rows + 1)]
        L = catmull(left, 10)
        R = catmull(right, 10)
        self.river: list[tuple[float, float, float]] = []
        bank_l = [(x + 12, y) for x, y in L]
        bank_r = [(x - 12, y) for x, y in R]
        water_l = [(x + 26 + 5 * math.sin(y / 70), y) for x, y in L]
        water_r = [(x - 26 + 5 * math.sin(y / 63 + 1), y) for x, y in R]
        pygame.draw.polygon(surf, C.BANK, [P(p) for p in bank_l + bank_r[::-1]])
        pygame.draw.polygon(surf, C.WATER, [P(p) for p in water_l + water_r[::-1]])
        deep_l = [(lerp(a[0], b[0], 0.3), a[1]) for a, b in zip(water_l, water_r)]
        deep_r = [(lerp(a[0], b[0], 0.7), a[1]) for a, b in zip(water_l, water_r)]
        pygame.draw.polygon(surf, C.WATER_DEEP, [P(p) for p in deep_l + deep_r[::-1]])
        pygame.draw.lines(surf, shade(C.WATER_SHINE, 0.7), False, [P(p) for p in water_l], max(1, int(1.2 * s)))
        pygame.draw.lines(surf, shade(C.WATER_SHINE, 0.7), False, [P(p) for p in water_r], max(1, int(1.2 * s)))
        rng = random.Random(5)
        for (x0, y), (x1, _) in zip(bank_l, bank_r):
            if rng.random() < 0.35:
                for x in (x0 + 6, x1 - 6):
                    pygame.draw.circle(surf, C.TREE_DARK, P((x, y)), 4.5 * s)
                    pygame.draw.circle(surf, C.TREE_LIGHT, P((x - 1.2, y - 1.2)), 2.4 * s)
        for a, b in zip(water_l, water_r):
            self.river.append((a[1], a[0], b[0]))

    def _draw_streets(self, surf, P, s, city: CityMap) -> None:
        streets = city.streets
        for st in streets:
            if st.puente:
                thick_line(surf, (66, 72, 104), P(st.a), P(st.b), (st.ancho + 12) * s)
                for side in (-1, 1):
                    dx, dy = st.b[0] - st.a[0], st.b[1] - st.a[1]
                    Lg = math.hypot(dx, dy)
                    nx, ny = -dy / Lg * (st.ancho / 2 + 5) * side, dx / Lg * (st.ancho / 2 + 5) * side
                    thick_line(surf, (120, 128, 170), P((st.a[0] + nx, st.a[1] + ny)),
                               P((st.b[0] + nx, st.b[1] + ny)), 1.2 * s)
        for color, extra in ((C.ASPHALT_EDGE, 3.0), (C.ASPHALT, 0.0)):
            for st in streets:
                w = (st.ancho + extra) * s
                thick_line(surf, color, P(st.a), P(st.b), w)
                for p in (st.a, st.b):
                    pygame.draw.circle(surf, color, P(p), w / 2)

        for st in streets:
            dx, dy = st.b[0] - st.a[0], st.b[1] - st.a[1]
            Lg = math.hypot(dx, dy)
            if Lg < 40:
                continue
            ux, uy = dx / Lg, dy / Lg
            nx, ny = -uy, ux
            trim = 18.0
            a = (st.a[0] + ux * trim, st.a[1] + uy * trim)
            b = (st.b[0] - ux * trim, st.b[1] - uy * trim)
            if st.avenida:
                for o in (-0.9, 0.9):
                    thick_line(surf, C.AVENUE_MARK, P((a[0] + nx * o, a[1] + ny * o)),
                               P((b[0] + nx * o, b[1] + ny * o)), 0.7 * s)
            else:
                col = C.LANE_MARK if st.ancho >= C.WIDTH_STREET else shade(C.LANE_MARK, 0.75)
                d, n = 0.0, Lg - 2 * trim
                while d < n:
                    e = min(n, d + 6)
                    thick_line(surf, col, P((a[0] + ux * d, a[1] + uy * d)), P((a[0] + ux * e, a[1] + uy * e)), 0.6 * s)
                    d += 12
            # Pasos de cebra en intersecciones semaforizadas
            if st.nodes:
                for nid, sign, base in ((st.nodes[0], 1, st.a), (st.nodes[1], -1, st.b)):
                    if nid not in city.lights:
                        continue
                    cx, cy = base[0] + ux * 15 * sign, base[1] + uy * 15 * sign
                    count = max(3, int(st.ancho / 2.6))
                    for k in range(count):
                        o = -st.ancho / 2 + (k + 0.5) * st.ancho / count
                        px, py = cx + nx * o, cy + ny * o
                        thick_line(surf, (92, 100, 138), P((px - ux * 2.5, py - uy * 2.5)),
                                   P((px + ux * 2.5, py + uy * 2.5)), 1.3 * s)

        # Señales de PARE + línea de detención
        for e in city.edges.values():
            if not e.tiene_pare:
                continue
            a, b = city.lane_points(e, trim=12.0)
            Lg = dist(a, b) or 1
            ux, uy = (b[0] - a[0]) / Lg, (b[1] - a[1]) / Lg
            nx, ny = -uy, ux
            half = e.ancho * 0.24
            thick_line(surf, (190, 196, 220), P((b[0] + nx * half, b[1] + ny * half)),
                       P((b[0] - nx * half, b[1] - ny * half)), 1.2 * s)
            c = (b[0] + nx * (e.ancho * 0.5 + 4.5), b[1] + ny * (e.ancho * 0.5 + 4.5))
            r = 3.6
            octo = [P((c[0] + r * math.cos(math.pi / 8 + k * math.pi / 4), c[1] + r * math.sin(math.pi / 8 + k * math.pi / 4)))
                    for k in range(8)]
            pygame.draw.polygon(surf, (235, 235, 245), octo)
            octo_in = [P((c[0] + r * 0.78 * math.cos(math.pi / 8 + k * math.pi / 4),
                          c[1] + r * 0.78 * math.sin(math.pi / 8 + k * math.pi / 4))) for k in range(8)]
            pygame.draw.polygon(surf, (214, 34, 52), octo_in)

    # ------------------------------------------------------ dynamic layers
    def _refresh_traffic(self) -> None:
        if self.t - self._traffic_t < 0.2:
            return
        self._traffic_t = self.t
        surf = self.traffic_surf
        surf.fill((0, 0, 0, 0))
        city = self.sim.city
        for e in city.edges.values():
            f = e.factor_trafico
            if e.accidente:
                col, alpha = C.ACCIDENT, 210
            elif f > 1.4:
                k = min(1.0, (f - 1.4) / 2.6)
                col, alpha = mix(C.JAM_LOW, C.JAM_HIGH, k), int(120 + 100 * k)
            else:
                continue
            a, b = city.lane_points(e, trim=8.0)
            w = max(2, int(e.ancho * 0.44 * self.scale))
            pygame.draw.line(surf, (*col, alpha), self.L(a), self.L(b), w)

    def _refresh_generation(self) -> None:
        sim = self.sim
        if self._gen_seen == sim.gen_version:
            return
        if sim.turbo and self.t - self._gen_built_t < 0.12:
            return
        self._gen_seen = sim.gen_version
        self._gen_built_t = self.t
        city = sim.city
        pop = sim.ga.individuos
        n = len(pop)
        counts: dict[tuple[int, int], int] = {}
        for ind in pop:
            for u, v in zip(ind.ruta, ind.ruta[1:]):
                k = (u, v) if u < v else (v, u)
                counts[k] = counts.get(k, 0) + 1
        self.heat_surf.fill((0, 0, 0, 0))
        for (u, v), c in sorted(counts.items(), key=lambda kv: kv[1]):
            r = c / n
            alpha = int(22 + 120 * r ** 0.7)
            w = max(2, int(2 + 3 * r))
            pygame.draw.line(self.heat_surf, (*C.HEAT, alpha), self.L(city.nodes[u].pos), self.L(city.nodes[v].pos), w)

        pts: list[Vec] = []
        for seg in sim.ga.mejor.timeline:
            for p in seg.pts:
                lp = self.L(p)
                if not pts or dist(pts[-1], lp) > 0.5:
                    pts.append(lp)
        self.best_pts = pts
        self.best_cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            self.best_cum.append(self.best_cum[-1] + dist(a, b))
        bs = self.best_surf
        bs.fill((0, 0, 0, 0))
        for w, alpha in ((18, 22), (12, 45), (7, 110), (4, 235)):
            thick_polyline(bs, (*C.NEON_BEST, alpha), pts, w * self.ui)
        thick_polyline(bs, (*C.NEON_BEST_CORE, 255), pts, 1.6 * self.ui)

    def _point_on_best(self, d: float) -> Vec:
        cum, pts = self.best_cum, self.best_pts
        i = max(1, min(len(cum) - 1, bisect_right(cum, d)))
        seg = cum[i] - cum[i - 1]
        k = 0 if seg <= 0 else (d - cum[i - 1]) / seg
        a, b = pts[i - 1], pts[i]
        return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)

    # ----------------------------------------------------------------- draw
    def draw(self, dt: float) -> None:
        self.t += dt
        scr = self.screen
        scr.fill(C.BG)
        self._draw_map()
        self._draw_topbar()
        self._draw_bottom()
        self._draw_tooltip()
        if self.show_help:
            self._draw_help()

    def _draw_map(self) -> None:
        scr, mr, sim, city = self.screen, self.map_rect, self.sim, self.sim.city
        self._refresh_traffic()
        self._refresh_generation()
        scr.blit(self.background, mr.topleft)
        scr.set_clip(mr)
        scr.blit(self.traffic_surf, mr.topleft)
        scr.blit(self.heat_surf, mr.topleft)
        self._draw_water_sparkles()
        self._draw_cars()
        scr.blit(self.best_surf, mr.topleft)
        self._draw_best_flow()
        self._draw_lights()
        self._draw_population()
        self._draw_accidents()
        self._draw_endpoints()
        if self.show_ids:
            self._draw_ids()
        if self.show_legend:
            self._draw_legend()
        self._draw_toast()
        scr.set_clip(None)
        pygame.draw.rect(scr, C.PANEL_BORDER, mr, 1, border_radius=int(16 * self.ui))

    def _draw_water_sparkles(self) -> None:
        if not getattr(self, "river", None):
            return
        river = self.river
        ys = [r[0] for r in river]
        span = ys[-1] - ys[0]
        for k in range(46):
            ph = (k * 0.6180339 + self.t * (0.018 + (k % 5) * 0.004)) % 1.0
            y = ys[0] + ph * span
            i = max(1, min(len(river) - 1, bisect_right(ys, y)))
            _, xl, xr = river[i]
            lane = ((k * 0.37) % 1.0) * 0.8 + 0.1
            x = lerp(xl, xr, lane) + 4 * math.sin(self.t * 1.3 + k)
            p = self.S((x, y))
            tw = 0.5 + 0.5 * math.sin(self.t * 3 + k * 1.7)
            self.blit_glow(p, int(5 * self.ui), shade(C.WATER_SHINE, 0.55 + 0.45 * tw), 0.8)
            pygame.draw.line(self.screen, shade(C.WATER_SHINE, 0.6 + 0.4 * tw), (p[0] - 3, p[1]), (p[0] + 3, p[1]), 1)

    def _draw_cars(self) -> None:
        scr = self.screen
        sc = self.scale
        wcar = max(3.0, 5.4 * sc)
        for car in self.sim.traffic.cars:
            cx, cy = self.S((car.x, car.y))
            poly = rect_poly(cx, cy, car.hx, car.hy, max(5.0, car.length * 1.15 * sc), wcar)
            pygame.draw.polygon(scr, shade(car.color, 0.85), poly)
            fx, fy = cx + car.hx * car.length * sc * 0.75, cy + car.hy * car.length * sc * 0.75
            self.blit_glow((fx, fy), int(5 * self.ui), (150, 130, 80), 0.9)
            bx, by = cx - car.hx * car.length * sc * 0.5, cy - car.hy * car.length * sc * 0.5
            scr.set_at((int(bx), int(by)), (255, 60, 70))

    def _draw_best_flow(self) -> None:
        if len(self.best_pts) < 2:
            return
        total = self.best_cum[-1]
        spacing, dash = 26 * self.ui, 9 * self.ui
        phase = (self.t * 70 * self.ui) % spacing
        d = phase
        while d < total - dash:
            a, b = self._point_on_best(d), self._point_on_best(d + dash)
            pygame.draw.line(self.screen, (255, 255, 235),
                             (a[0] + self.map_rect.x, a[1] + self.map_rect.y),
                             (b[0] + self.map_rect.x, b[1] + self.map_rect.y), max(2, int(2.4 * self.ui)))
            d += spacing

    def _draw_lights(self) -> None:
        clock = self.sim.clock
        colors = {VERDE: C.LIGHT_GREEN, AMBAR: C.LIGHT_AMBER, ROJO: C.LIGHT_RED}
        w = max(2, int(3 * self.ui))
        for light, axis, pa, pb, c in self.light_bars:
            col = colors[light.estado(axis, clock)]
            pygame.draw.line(self.screen, col, pa, pb, w)
            self.blit_glow(c, int(9 * self.ui), col, 0.7)
        pulse = 0.5 + 0.5 * math.sin(self.t * 5)
        for nid, light in self.sim.city.lights.items():
            if light.modo == AUTO:
                continue
            col = C.LIGHT_RED if light.modo == ROJO else C.LIGHT_GREEN
            p = self.S(self.sim.city.nodes[nid].pos)
            r = int((17 + 4 * pulse) * self.ui)
            pygame.draw.circle(self.screen, col, p, r, max(2, int(2 * self.ui)))
            self.blit_glow(p, int(r * 1.6), col, 0.5 + 0.4 * pulse)
            tag = self.text(self.f_tiny, "FORZADO", col)
            self.screen.blit(tag, (p[0] - tag.get_width() / 2, p[1] + r + 2))

    def _draw_population(self) -> None:
        sim = self.sim
        if sim.turbo:
            return
        fx = self.fx
        fx.fill((0, 0, 0, 0))
        pop = sim.ga.individuos
        n = len(pop)
        tau = sim.tau
        sc = self.scale
        dtrail = 0.05 * sim.time_scale
        best_pose = None
        glows = []
        for rank in range(n - 1, -1, -1):
            ind = pop[rank]
            pose = bus_pose(ind, tau)
            if pose is None:
                continue
            if rank == 0:
                best_pose = pose
                continue
            if pose.arrived:
                continue
            col = mix(C.BUS_FAST, C.BUS_SLOW, rank / max(1, n - 1))
            prev = (pose.x * sc, pose.y * sc)
            for k in range(1, 7):
                pk = bus_pose(ind, tau - k * dtrail)
                if pk is None:
                    break
                cur = (pk.x * sc, pk.y * sc)
                pygame.draw.line(fx, (*col, int(110 * (1 - k / 7))), prev, cur, 2)
                prev = cur
            poly = rect_poly(pose.x * sc, pose.y * sc, pose.hx, pose.hy, max(12, 22 * sc), max(5, 8.5 * sc))
            pygame.draw.polygon(fx, (*col, 200), poly)
            glows.append((pose, col))
        # trazo del mejor bus
        if best_pose is not None:
            best = pop[0]
            prev = (best_pose.x * sc, best_pose.y * sc)
            for k in range(1, 10):
                pk = bus_pose(best, tau - k * dtrail)
                if pk is None:
                    break
                cur = (pk.x * sc, pk.y * sc)
                pygame.draw.line(fx, (*C.NEON_BEST, int(200 * (1 - k / 10))), prev, cur, 4)
                prev = cur
        mx, my = self.map_rect.topleft
        for pose, col in glows:
            self.blit_glow((mx + pose.x * sc, my + pose.y * sc), int(11 * self.ui), col, 0.45)
        self.screen.blit(fx, self.map_rect.topleft)

        if best_pose is not None:
            p = self.S((best_pose.x, best_pose.y))
            self.blit_glow(p, int(22 * self.ui), C.NEON_BEST, 0.9)
            poly = rect_poly(p[0], p[1], best_pose.hx, best_pose.hy, max(12, 27 * sc), max(6, 10.5 * sc))
            pygame.draw.polygon(self.screen, C.NEON_BEST, poly)
            pygame.draw.polygon(self.screen, (255, 255, 255), poly, 1)
            if best_pose.waiting:
                col = C.LIGHT_RED if best_pose.waiting == ROJO else (235, 235, 245)
                self._chip((p[0], p[1] - 16 * self.ui), best_pose.waiting, col, small=True)

    def _draw_accidents(self) -> None:
        city = self.sim.city
        for e in city.accidents():
            a, b = city.nodes[e.nodo_origen].pos, city.nodes[e.nodo_destino].pos
            m = self.S(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
            pulse = 0.5 + 0.5 * math.sin(self.t * 7)
            self.blit_glow(m, int((26 + 10 * pulse) * self.ui), C.ACCIDENT, 0.6 + 0.4 * pulse)
            for i in range(7):
                ph = (self.t * 0.5 + i / 7) % 1.0
                r = (3 + 9 * ph) * self.ui
                sx = m[0] + math.sin(i * 2.1 + self.t) * 6 * ph * self.ui
                sy = m[1] - (8 + 30 * ph) * self.ui
                g = int(110 * (1 - ph))
                surf = pygame.Surface((int(r * 2 + 2), int(r * 2 + 2)), pygame.SRCALPHA)
                pygame.draw.circle(surf, (120, 120, 140, g), (r + 1, r + 1), r)
                self.screen.blit(surf, (sx - r, sy - r))
            s = 11 * self.ui
            tri = [(m[0], m[1] - s), (m[0] + s, m[1] + s * 0.8), (m[0] - s, m[1] + s * 0.8)]
            pygame.draw.polygon(self.screen, mix(C.ACCIDENT, (255, 200, 60), pulse * 0.4), tri)
            pygame.draw.polygon(self.screen, (255, 255, 255), tri, max(1, int(1.5 * self.ui)))
            ex = self.text(self.f_txt_b, "!", (255, 255, 255))
            self.screen.blit(ex, (m[0] - ex.get_width() / 2, m[1] - ex.get_height() / 2 + s * 0.12))

    def _alpha_ring(self, center: Vec, r: float, color, alpha: int, width: int) -> None:
        size = int(r * 2 + width * 2 + 2)
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(surf, (*color, max(0, min(255, alpha))), (size / 2, size / 2), r, width)
        self.screen.blit(surf, (center[0] - size / 2, center[1] - size / 2))

    def _draw_endpoints(self) -> None:
        sim, city, ui = self.sim, self.sim.city, self.ui
        for nid, color, label in ((city.origin, C.ORIGIN, "ORIGEN"), (city.destination, C.DEST, "DESTINO")):
            p = self.S(city.nodes[nid].pos)
            for k in range(2):
                ph = (self.t * 0.7 + k * 0.5) % 1.0
                self._alpha_ring(p, (10 + 24 * ph) * ui, color, int(220 * (1 - ph)), max(2, int(2 * ui)))
            self.blit_glow(p, int(26 * ui), color, 0.9)
            pygame.draw.circle(self.screen, color, p, 9 * ui)
            pygame.draw.circle(self.screen, (255, 255, 255), p, 9 * ui, max(2, int(2 * ui)))
            pygame.draw.circle(self.screen, (255, 255, 255), p, 3 * ui)
            self._chip((p[0], p[1] - 24 * ui), f"{label} · {nid}", color)
        # Llegada del mejor bus: onda expansiva dorada
        since = sim.real_time - sim.last_arrival_real
        if 0 <= since < 1.0 and not sim.turbo:
            p = self.S(city.nodes[city.destination].pos)
            self._alpha_ring(p, (12 + 70 * since) * ui, C.NEON_BEST, int(255 * (1 - since)), max(2, int(4 * ui)))
            self.blit_glow(p, int((30 + 30 * since) * ui), C.NEON_BEST, 1.0 - since)

    def _chip(self, center: Vec, text: str, color, small: bool = False) -> None:
        font = self.f_tiny if small else self.f_lbl
        t = self.text(font, text, (10, 12, 22))
        pad_x, pad_y = (5, 1) if small else (8, 3)
        r = pygame.Rect(0, 0, t.get_width() + pad_x * 2, t.get_height() + pad_y * 2)
        r.center = (int(center[0]), int(center[1]))
        pygame.draw.rect(self.screen, color, r, border_radius=r.h // 2)
        self.screen.blit(t, (r.x + pad_x, r.y + pad_y))

    def _draw_ids(self) -> None:
        for n in self.sim.city.nodes.values():
            p = self.S(n.pos)
            t = self.text(self.f_mono, str(n.id), C.TEXT)
            r = t.get_rect(center=(int(p[0]), int(p[1])))
            pygame.draw.rect(self.screen, (8, 10, 20), r.inflate(6, 2), border_radius=4)
            self.screen.blit(t, r)

    def _draw_legend(self) -> None:
        """Leyenda de colores en la esquina inferior izquierda del mapa (tecla L)."""
        scr, ui, mr = self.screen, self.ui, self.map_rect
        items = [
            ("route", "Mejor ruta de la generación"),
            ("best", "Mejor bus (el más rápido)"),
            ("pop", "Población: rápido → lento"),
            ("heat", "Calles exploradas por la población"),
            ("jam", "Congestión: moderada → atasco"),
            ("acc", "Accidente"),
            ("light", "Semáforo: verde / ámbar / rojo"),
            ("stop", "Señal de PARE (+3 s)"),
            ("od", "Origen / Destino"),
        ]
        title = self.text(self.f_lbl, "LEYENDA  ·  L OCULTAR", C.TEXT_DIM)
        labels = [self.text(self.f_txt, t, C.TEXT) for _, t in items]
        lh = max(int(22 * ui), labels[0].get_height() + 4)
        pad, icon_w = int(12 * ui), int(36 * ui)
        w = pad * 2 + icon_w + 10 + max(l.get_width() for l in labels)
        h = pad * 2 + title.get_height() + 6 + lh * len(items)
        box = pygame.Rect(mr.x + 12, mr.bottom - h - 12, w, h)
        bg = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(bg, (*C.PANEL, 225), bg.get_rect(), border_radius=12)
        pygame.draw.rect(bg, (*C.PANEL_BORDER, 255), bg.get_rect(), 1, border_radius=12)
        scr.blit(bg, box.topleft)
        scr.blit(title, (box.x + pad, box.y + pad))
        y = box.y + pad + title.get_height() + 6
        for (kind, _), lab in zip(items, labels):
            cy = y + lh / 2
            x0, x1 = box.x + pad, box.x + pad + icon_w
            xm = (x0 + x1) / 2
            if kind == "route":
                self.blit_glow((xm, cy), int(12 * ui), C.NEON_BEST, 0.5)
                pygame.draw.line(scr, C.NEON_BEST, (x0, cy), (x1, cy), max(4, int(5 * ui)))
                pygame.draw.line(scr, C.NEON_BEST_CORE, (x0, cy), (x1, cy), 1)
            elif kind == "best":
                self.blit_glow((xm, cy), int(13 * ui), C.NEON_BEST, 0.8)
                poly = rect_poly(xm, cy, 1, 0, 18 * ui, 8 * ui)
                pygame.draw.polygon(scr, C.NEON_BEST, poly)
                pygame.draw.polygon(scr, (255, 255, 255), poly, 1)
            elif kind == "pop":
                for i, t in enumerate((0.0, 0.5, 1.0)):
                    poly = rect_poly(x0 + 6 * ui + i * 12 * ui, cy, 1, 0, 10 * ui, 6 * ui)
                    pygame.draw.polygon(scr, mix(C.BUS_FAST, C.BUS_SLOW, t), poly)
            elif kind == "heat":
                pygame.draw.line(scr, shade(C.HEAT, 0.8), (x0, cy), (x1, cy), max(3, int(3 * ui)))
            elif kind == "jam":
                lw = max(4, int(5 * ui))
                pygame.draw.line(scr, C.JAM_LOW, (x0, cy), (xm, cy), lw)
                pygame.draw.line(scr, C.JAM_HIGH, (xm, cy), (x1, cy), lw)
            elif kind == "acc":
                s = 8 * ui
                tri = [(xm, cy - s), (xm + s, cy + s * 0.8), (xm - s, cy + s * 0.8)]
                pygame.draw.polygon(scr, C.ACCIDENT, tri)
                pygame.draw.polygon(scr, (255, 255, 255), tri, 1)
            elif kind == "light":
                for i, col in enumerate((C.LIGHT_GREEN, C.LIGHT_AMBER, C.LIGHT_RED)):
                    bx = x0 + 6 * ui + i * 11 * ui
                    pygame.draw.line(scr, col, (bx, cy - 6 * ui), (bx, cy + 6 * ui), max(2, int(3 * ui)))
            elif kind == "stop":
                r = 7 * ui
                octo = [(xm + r * math.cos(math.pi / 8 + k * math.pi / 4), cy + r * math.sin(math.pi / 8 + k * math.pi / 4))
                        for k in range(8)]
                pygame.draw.polygon(scr, (235, 235, 245), octo)
                inner = [(xm + (p[0] - xm) * 0.78, cy + (p[1] - cy) * 0.78) for p in octo]
                pygame.draw.polygon(scr, (214, 34, 52), inner)
            elif kind == "od":
                for cx, col in ((x0 + 8 * ui, C.ORIGIN), (x1 - 8 * ui, C.DEST)):
                    pygame.draw.circle(scr, col, (cx, cy), 6 * ui)
                    pygame.draw.circle(scr, (255, 255, 255), (cx, cy), 6 * ui, 2)
            scr.blit(lab, (x1 + 10, cy - lab.get_height() / 2))
            y += lh

    def _draw_toast(self) -> None:
        sim = self.sim
        if not sim.toast:
            return
        text, color, t0 = sim.toast
        age = sim.real_time - t0
        dur = 3.4
        if age > dur:
            sim.toast = None
            return
        a = min(1.0, age / 0.25, (dur - age) / 0.5)
        t = self.text(self.f_toast, text, C.TEXT)
        w, h = t.get_width() + 64, t.get_height() + 22
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (*C.PANEL, 235), surf.get_rect(), border_radius=h // 2)
        pygame.draw.rect(surf, (*color, 255), surf.get_rect(), 2, border_radius=h // 2)
        pygame.draw.circle(surf, color, (h // 2 + 4, h // 2), 7)
        surf.blit(t, (h // 2 + 22, 11))
        surf.set_alpha(int(255 * a))
        y = self.map_rect.y + 18 - (1 - a) * 14
        self.screen.blit(surf, (self.map_rect.centerx - w / 2, y))

    # ----------------------------------------------------------------- HUD
    def _panel(self, r: pygame.Rect, accent=None) -> None:
        pygame.draw.rect(self.screen, C.PANEL, r, border_radius=12)
        pygame.draw.rect(self.screen, C.PANEL_BORDER, r, 1, border_radius=12)
        if accent:
            pygame.draw.rect(self.screen, accent, (r.x + 1, r.y + 10, 3, r.h - 20), border_radius=2)

    def _draw_logo(self, x: float, cy: float, h: float) -> None:
        for i in range(14):
            t = i / 13
            px = x + t * h * 0.9
            ph = t * math.pi * 2 + self.t * 2
            y1 = cy + math.sin(ph) * h * 0.32
            y2 = cy - math.sin(ph) * h * 0.32
            if i % 2 == 0:
                pygame.draw.line(self.screen, (60, 70, 110), (px, y1), (px, y2), 1)
            depth = (math.cos(ph) + 1) / 2
            pygame.draw.circle(self.screen, mix(shade(C.BUS_FAST, 0.5), C.BUS_FAST, depth), (px, y1), 2 + 1.5 * depth)
            pygame.draw.circle(self.screen, mix(shade(C.HEAT, 0.5), C.HEAT, 1 - depth), (px, y2), 2 + 1.5 * (1 - depth))

    def _draw_topbar(self) -> None:
        sim, ga, scr, r, ui = self.sim, self.sim.ga, self.screen, self.top, self.ui
        self._draw_logo(r.x + 6, r.centery, r.h * 0.9)
        tx = r.x + r.h * 0.9 + 22
        title = self.text(self.f_title, "GenRoute", C.TEXT)
        sub = self.text(self.f_sub, "Algoritmo genético · rutas de bus adaptativas", C.TEXT_DIM)
        th = title.get_height() + sub.get_height() - 4
        scr.blit(title, (tx, r.centery - th / 2 - 2))
        scr.blit(sub, (tx + 1, r.centery - th / 2 + title.get_height() - 6))

        hist = ga.history
        times = [i.tiempo for i in ga.individuos]
        avg_now = sum(times) / len(times)
        first = hist[0][1] if hist else avg_now
        mejora = max(0.0, (first - avg_now) / first * 100) if first else 0.0
        cards = [
            ("GENERACIÓN", f"{ga.generacion}", C.TEXT),
            ("MEJOR TIEMPO", fmt_time(ga.mejor.tiempo), C.NEON_BEST),
            ("PROMEDIO", fmt_time(sum(times) / len(times)), C.CHART_AVG),
            ("PEOR TIEMPO", fmt_time(ga.peor.tiempo), C.CHART_WORST),
            ("MEJORA PROMEDIO", f"{mejora:.0f}%", C.DEST),
            ("RUTAS ÚNICAS", f"{len({tuple(i.ruta) for i in ga.individuos})}/{len(times)}", C.TEXT),
        ]
        status_w = int(190 * ui)
        cw = int(150 * ui)
        gap = 10
        x = r.right - status_w - gap - len(cards) * (cw + gap)
        for label, val, col in cards:
            cr = pygame.Rect(x, r.y + 4, cw, r.h - 8)
            self._panel(cr, col)
            lt = self.text(self.f_lbl, label, C.TEXT_DIM)
            vt = self.text(self.f_val, val, col)
            block = lt.get_height() + vt.get_height() - 4
            scr.blit(lt, (cr.x + 14, cr.centery - block / 2))
            scr.blit(vt, (cr.x + 13, cr.centery - block / 2 + lt.get_height() - 4))
            x += cw + gap

        sr = pygame.Rect(r.right - status_w, r.y + 4, status_w, r.h - 8)
        self._panel(sr)
        if sim.paused:
            st, col = "PAUSA", C.LIGHT_AMBER
        elif sim.turbo:
            st, col = "TURBO", C.HEAT
        else:
            st, col = "EN CARRERA", C.DEST
        pulse = 0.5 + 0.5 * math.sin(self.t * 4)
        row1 = sr.y + sr.h * 0.32
        row2 = sr.y + sr.h * 0.70
        pygame.draw.circle(scr, col, (sr.x + 18, row1), 5)
        self.blit_glow((sr.x + 18, row1), 12, col, 0.4 + 0.5 * pulse)
        t1 = self.text(self.f_txt_b, st, col)
        scr.blit(t1, (sr.x + 30, row1 - t1.get_height() / 2))
        sp = self.text(self.f_mono, f"x{sim.time_scale:g}", C.TEXT_DIM)
        scr.blit(sp, (sr.right - sp.get_width() - 12, row1 - sp.get_height() / 2))
        online = self.api is not None and self.api.online
        acol = C.DEST if online else C.ACCIDENT
        pygame.draw.circle(scr, acol, (sr.x + 18, row2), 4)
        api_txt = f"API  {self.api.host}:{self.api.port}" if online else "API desconectada"
        t2 = self.text(self.f_mono, api_txt, C.TEXT_DIM if online else C.ACCIDENT)
        scr.blit(t2, (sr.x + 30, row2 - t2.get_height() / 2))

    def _draw_bottom(self) -> None:
        r = self.bottom
        gap = 12
        w1 = int(r.w * 0.30)
        w3 = int(r.w * 0.25)
        w2 = r.w - w1 - w3 - 2 * gap
        self._draw_log(pygame.Rect(r.x, r.y, w1, r.h))
        self._draw_chart(pygame.Rect(r.x + w1 + gap, r.y, w2, r.h))
        self._draw_params(pygame.Rect(r.x + w1 + w2 + 2 * gap, r.y, w3, r.h))

    def _header(self, r: pygame.Rect, title: str) -> int:
        t = self.text(self.f_lbl, title, C.TEXT_DIM)
        self.screen.blit(t, (r.x + 16, r.y + 12))
        return r.y + 12 + t.get_height() + 8

    def _draw_log(self, r: pygame.Rect) -> None:
        self._panel(r)
        y = self._header(r, "EVENTOS EN VIVO")
        lh = self.f_txt.get_height() + 3
        src_col = {"API": C.CHART_AVG, "UI": C.TEXT_FAINT, "AG": C.NEON_BEST, "SYS": C.ORIGIN}
        for i, ev in enumerate(self.sim.log):
            if y + lh > r.bottom - 8:
                break
            fade = 1.0 if i == 0 else max(0.45, 1 - i * 0.12)
            tm = self.text(self.f_mono, fmt_time(ev.clock), shade(C.TEXT_FAINT, fade))
            self.screen.blit(tm, (r.x + 16, y + 2))
            x = r.x + 16 + tm.get_width() + 10
            pygame.draw.circle(self.screen, shade(ev.color, fade), (x + 3, y + lh / 2), 4)
            x += 13
            tag = self.text(self.f_tiny, ev.source, src_col.get(ev.source, C.TEXT_DIM))
            tr = pygame.Rect(x, y + (lh - tag.get_height() - 2) / 2, tag.get_width() + 8, tag.get_height() + 2)
            pygame.draw.rect(self.screen, C.PANEL_HI, tr, border_radius=4)
            self.screen.blit(tag, (tr.x + 4, tr.y + 1))
            x = tr.right + 8
            txt = ev.text
            maxw = r.right - 12 - x
            surf = self.text(self.f_txt, txt, shade(C.TEXT, fade))
            while surf.get_width() > maxw and len(txt) > 4:
                txt = txt[:-2]
                surf = self.text(self.f_txt, txt + "…", shade(C.TEXT, fade))
            self.screen.blit(surf, (x, y))
            y += lh

    def _draw_chart(self, r: pygame.Rect) -> None:
        self._panel(r)
        scr = self.screen
        y0 = self._header(r, "TIEMPO DE VIAJE POR GENERACIÓN")
        lx = r.right - 16
        for name, col in (("Peor", C.CHART_WORST), ("Promedio", C.CHART_AVG), ("Mejor", C.NEON_BEST)):
            t = self.text(self.f_tiny, name, C.TEXT_DIM)
            lx -= t.get_width()
            scr.blit(t, (lx, r.y + 13))
            lx -= 16
            pygame.draw.line(scr, col, (lx, r.y + 13 + t.get_height() / 2), (lx + 11, r.y + 13 + t.get_height() / 2), 3)
            lx -= 14

        hist = self.sim.ga.history[-90:]
        offset = len(self.sim.ga.history) - len(hist)
        area = pygame.Rect(r.x + 58, y0 + 2, r.w - 58 - 18, r.bottom - y0 - 22)
        if len(hist) < 1 or area.h < 20:
            return
        bests = [h[0] for h in hist]
        avgs = [h[1] for h in hist]
        lo = min(bests) * 0.92
        hi = max(max(avgs) * 1.12, lo + 30)
        n = max(len(hist) - 1, 1)

        def P(i, v):
            v = max(lo, min(hi, v))
            return (area.x + area.w * i / n, area.bottom - area.h * (v - lo) / (hi - lo))

        for k in range(4):
            v = lo + (hi - lo) * k / 3
            y = area.bottom - area.h * k / 3
            pygame.draw.line(scr, (30, 36, 62), (area.x, y), (area.right, y), 1)
            lab = self.text(self.f_mono, fmt_time(v), C.TEXT_FAINT)
            scr.blit(lab, (area.x - lab.get_width() - 8, y - lab.get_height() / 2))
        gl = self.text(self.f_mono, f"gen {offset + 1}", C.TEXT_FAINT)
        scr.blit(gl, (area.x, area.bottom + 4))
        gr = self.text(self.f_mono, f"gen {offset + len(hist)}", C.TEXT_FAINT)
        scr.blit(gr, (area.right - gr.get_width(), area.bottom + 4))

        for g, col in self.sim.markers:
            i = g - 1 - offset
            if 0 <= i < len(hist):
                x = area.x + area.w * i / n
                pygame.draw.line(scr, shade(col, 0.6), (x, area.y), (x, area.bottom), 1)
                pygame.draw.polygon(scr, col, [(x - 4, area.y), (x + 4, area.y), (x, area.y + 6)])

        if len(hist) >= 2:
            fill = pygame.Surface(area.size, pygame.SRCALPHA)
            pts = [P(i, b) for i, b in enumerate(bests)]
            poly = [(x - area.x, y - area.y) for x, y in pts] + [(area.w, area.h), (0, area.h)]
            pygame.draw.polygon(fill, (*C.NEON_BEST, 34), poly)
            scr.blit(fill, area.topleft)
            pygame.draw.lines(scr, shade(C.CHART_WORST, 0.85), False, [P(i, h[2]) for i, h in enumerate(hist)], 1)
            pygame.draw.lines(scr, C.CHART_AVG, False, [P(i, a) for i, a in enumerate(avgs)], 2)
            pygame.draw.lines(scr, C.NEON_BEST, False, pts, 3)
        last = P(len(hist) - 1, bests[-1])
        self.blit_glow(last, 14, C.NEON_BEST, 0.9)
        pygame.draw.circle(scr, (255, 255, 255), last, 3)

    def _draw_params(self, r: pygame.Rect) -> None:
        self._panel(r)
        scr = self.screen
        y = self._header(r, "PARÁMETROS DEL AG")
        p = self.sim.ga.params
        rows = [
            ("Población", f"{p['population_size']}", p["population_size"] / 100, C.BUS_FAST),
            ("Mutación", f"{p['mutation_rate']:.2f}", p["mutation_rate"], C.HEAT),
            ("Cruce", f"{p['crossover_rate']:.2f}", p["crossover_rate"], C.CHART_AVG),
            ("Elitismo", f"{p['elitism']}", p["elitism"] / 10, C.NEON_BEST),
        ]
        col_w = (r.w - 32 - 16) / 2
        lh = self.f_txt.get_height() + 12
        for i, (name, val, frac, col) in enumerate(rows):
            x = r.x + 16 + (i % 2) * (col_w + 16)
            yy = y + (i // 2) * lh
            scr.blit(self.text(self.f_txt, name, C.TEXT_DIM), (x, yy))
            vt = self.text(self.f_txt_b, val, C.TEXT)
            scr.blit(vt, (x + col_w - vt.get_width(), yy))
            by = yy + self.f_txt.get_height() + 2
            pygame.draw.rect(scr, C.PANEL_HI, (x, by, col_w, 4), border_radius=2)
            pygame.draw.rect(scr, col, (x, by, max(4, col_w * min(1, frac)), 4), border_radius=2)
        y += 2 * lh + 6
        hints = [("Clic", "accidente"), ("Clic der.", "semáforo"), ("Shift/Ctrl+Clic", "origen/destino"),
                 ("Espacio", "pausa"), ("T", "turbo"), ("A", "accidente aleatorio"), ("L", "leyenda"), ("H", "ayuda")]
        x = r.x + 16
        for key, desc in hints:
            kt = self.text(self.f_tiny, key, C.TEXT)
            dt_ = self.text(self.f_tiny, desc, C.TEXT_FAINT)
            w = kt.get_width() + 10 + 5 + dt_.get_width() + 12
            if x + w > r.right - 12:
                x = r.x + 16
                y += kt.get_height() + 10
            if y + kt.get_height() > r.bottom - 6:
                break
            kr = pygame.Rect(x, y, kt.get_width() + 10, kt.get_height() + 4)
            pygame.draw.rect(scr, C.PANEL_HI, kr, border_radius=4)
            pygame.draw.rect(scr, C.PANEL_BORDER, kr, 1, border_radius=4)
            scr.blit(kt, (x + 5, y + 2))
            scr.blit(dt_, (kr.right + 5, y + 2))
            x += w

    def _draw_tooltip(self) -> None:
        if not self.map_rect.collidepoint(self.mouse) or self.show_help:
            return
        city = self.sim.city
        wp = self.screen_to_world(self.mouse)
        lines: list[tuple[str, tuple]] = []
        nid = city.nearest_node(wp, 14 / self.scale)
        if nid is not None:
            n = city.nodes[nid]
            lines.append((f"Nodo {nid}  ·  {n.tipo.lower()}", C.TEXT))
            light = city.lights.get(nid)
            if light:
                lines.append((f"Semáforo {light.modo}  ·  ciclo {light.ciclo:.0f}s  (clic der.)", C.LIGHT_GREEN))
            lines.append(("Shift+clic: origen · Ctrl+clic: destino", C.TEXT_FAINT))
        else:
            key = city.nearest_street(wp, 12 / self.scale)
            if key is None:
                return
            u, v = key
            e1, e2 = city.edges[(u, v)], city.edges[(v, u)]
            kind = "Puente" if e1.puente else "Avenida" if e1.avenida else "Calle"
            lines.append((f"{kind} {u} ↔ {v}  ·  {e1.limite_velocidad:.0f} km/h  ·  {e1.distancia:.0f} m", C.TEXT))
            for e in (e1, e2):
                f = e.factor_trafico
                col = C.ACCIDENT if e.accidente else mix(C.DEST, C.LIGHT_RED, (f - 1) / 3)
                extra = "  ACCIDENTE" if e.accidente else ""
                lines.append((f"{e.nodo_origen} → {e.nodo_destino}: tráfico x{f:.1f}  ·  "
                               f"{e.travel_time():.0f}s{extra}", col))
            lines.append(("Clic: " + ("despejar" if e1.accidente else "provocar accidente"), C.TEXT_FAINT))
        surfs = [self.text(self.f_txt, s, c) for s, c in lines]
        w = max(s.get_width() for s in surfs) + 24
        h = sum(s.get_height() for s in surfs) + 16
        x, y = self.mouse[0] + 18, self.mouse[1] + 18
        sw, sh = self.screen.get_size()
        if x + w > sw - 8:
            x = self.mouse[0] - w - 12
        if y + h > sh - 8:
            y = self.mouse[1] - h - 12
        box = pygame.Rect(x, y, w, h)
        pygame.draw.rect(self.screen, (12, 15, 28), box, border_radius=10)
        pygame.draw.rect(self.screen, C.PANEL_BORDER, box, 1, border_radius=10)
        yy = y + 8
        for s in surfs:
            self.screen.blit(s, (x + 12, yy))
            yy += s.get_height()

    def _draw_help(self) -> None:
        sw, sh = self.screen.get_size()
        dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
        dim.fill((4, 6, 14, 200))
        self.screen.blit(dim, (0, 0))
        port = self.api.port if self.api else C.API_PORT
        sections = [
            ("RATÓN", [("Clic izq. en calle", "provocar / despejar accidente"),
                       ("Clic der. en semáforo", "ciclo AUTO → ROJO → VERDE"),
                       ("Shift + clic en nodo", "mover ORIGEN"),
                       ("Ctrl + clic en nodo", "mover DESTINO")]),
            ("TECLADO", [("Espacio", "pausa / reanudar"), ("T", "modo turbo (evolución sin animación)"),
                         ("+ / -", "velocidad de simulación"), ("A", "accidente en la mejor ruta"),
                         ("C", "despejar accidentes"), ("R", "reiniciar población"),
                         ("I", "mostrar IDs de nodos"), ("L", "mostrar / ocultar leyenda"), ("H / Esc", "cerrar ayuda · salir")]),
            ("API FASTAPI", [(f"http://127.0.0.1:{port}/", "panel de control web"),
                             (f"http://127.0.0.1:{port}/docs", "Swagger interactivo"),
                             ("POST /api/accident", '{"origen":3,"destino":4,"factor_trafico":10}'),
                             ("POST /api/semaphore/toggle", '{"nodo_id":5,"estado":"ROJO"}'),
                             ("POST /api/ga/params", '{"mutation_rate":0.3,"population_size":60}'),
                             ("GET  /api/status", "métricas de la generación actual")]),
        ]
        lh = self.f_txt.get_height() + 6
        total_h = sum(len(s[1]) * lh + 46 for s in sections) + 40
        w = int(760 * self.ui)
        box = pygame.Rect(0, 0, w, total_h)
        box.center = (sw // 2, sh // 2)
        self._panel(box)
        y = box.y + 22
        for title, items in sections:
            self.screen.blit(self.text(self.f_lbl, title, C.NEON_BEST), (box.x + 28, y))
            y += 26
            for k, d in items:
                self.screen.blit(self.text(self.f_txt_b, k, C.TEXT), (box.x + 28, y))
                self.screen.blit(self.text(self.f_txt, d, C.TEXT_DIM), (box.x + 28 + int(290 * self.ui), y))
                y += lh
            y += 20
