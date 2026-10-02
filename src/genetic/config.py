"""Constantes globales: tamaño del mundo, parámetros por defecto y paleta visual."""

# --- Mundo (unidades en metros) -------------------------------------------
WORLD_WIDTH_M = 2600.0
GRID_COLS = 18
CITY_SEED = 7

# Anchos de calle (m) según jerarquía vial
WIDTH_AVENUE = 24.0
WIDTH_STREET = 16.0
WIDTH_MINOR = 13.0

SPEED_AVENUE = 60  # km/h
SPEED_STREET = 40
SPEED_MINOR = 30

PARE_PENALTY_S = 3.0          # detención fija en señal de PARE
FORCED_RED_PENALTY_S = 120.0  # semáforo forzado a ROJO por la API
LIGHT_CYCLE_RANGE = (70.0, 100.0)

# --- Algoritmo genético ---------------------------------------------------
GA_DEFAULTS = {
    "population_size": 40,
    "mutation_rate": 0.2,
    "crossover_rate": 0.85,
    "tournament_k": 3,
    "elitism": 2,
}

# --- Simulación -----------------------------------------------------------
TIME_SCALE_DEFAULT = 70.0      # segundos simulados por segundo real
TIME_SCALE_STEPS = [15.0, 30.0, 50.0, 70.0, 100.0, 150.0, 220.0]
RACE_HOLD_S = 1.1              # segundos reales tras la llegada del mejor bus
AMBIENT_CARS = 170
AMBIENT_TIME_SCALE = 7.0       # velocidad cosmética del tráfico ambiental

API_HOST = "127.0.0.1"
API_PORT = 8000

# --- Paleta (noche neón) ----------------------------------------------------
BG = (9, 11, 22)
PANEL = (16, 20, 36)
PANEL_HI = (24, 30, 52)
PANEL_BORDER = (44, 54, 92)
TEXT = (226, 232, 255)
TEXT_DIM = (130, 142, 184)
TEXT_FAINT = (84, 94, 132)

GROUND = (27, 32, 54)
SIDEWALK = (38, 44, 70)
ASPHALT = (14, 17, 30)
ASPHALT_EDGE = (52, 60, 92)
LANE_MARK = (86, 96, 136)
AVENUE_MARK = (112, 104, 92)   # tono apagado para no confundir con la mejor ruta
ROOFS = [(46, 54, 88), (54, 60, 98), (40, 48, 80), (60, 56, 96), (50, 64, 96), (66, 64, 104)]
ROOF_SHADOW = (12, 14, 26)
WINDOW_WARM = (255, 206, 120)
WINDOW_COOL = (140, 206, 255)
PARK = (22, 58, 52)
PARK_PATH = (52, 86, 74)
TREE_DARK = (26, 86, 66)
TREE_LIGHT = (52, 132, 92)
PLAZA = (48, 50, 78)
WATER = (14, 40, 86)
WATER_DEEP = (10, 28, 66)
WATER_SHINE = (70, 140, 230)
BANK = (30, 66, 62)

NEON_BEST = (255, 226, 40)
NEON_BEST_CORE = (255, 250, 200)
BUS_FAST = (0, 230, 255)
BUS_SLOW = (176, 84, 255)
HEAT = (255, 54, 196)
ORIGIN = (64, 156, 255)
DEST = (50, 240, 130)
ACCIDENT = (255, 56, 84)
JAM_LOW = (255, 128, 30)     # congestión moderada (naranja)
JAM_HIGH = (235, 30, 60)     # atasco (rojo)
LIGHT_GREEN = (60, 255, 130)
LIGHT_AMBER = (255, 190, 40)
LIGHT_RED = (255, 56, 70)
CHART_AVG = (0, 210, 255)
CHART_WORST = (190, 80, 255)
CAR_COLORS = [(210, 216, 240), (120, 140, 200), (200, 90, 110), (90, 170, 200),
              (220, 190, 120), (150, 150, 170), (110, 200, 160)]
