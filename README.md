# GenRoute · Algoritmo genético para tráfico urbano

Demo visual en **Pygame** + **FastAPI**. Una población de buses evoluciona con un algoritmo genético para encontrar la ruta más rápida de **ORIGEN** a **DESTINO** en una ciudad nocturna. La ciudad tiene semáforos, señales de PARE, olas de congestión, un río con tres puentes y accidentes que puedes provocar en vivo.

## Ejecutar

```bash
uv sync
uv run genetic                 # ventana casi del ancho del escritorio
uv run genetic --fullscreen    # pantalla completa (ideal para presentar)
uv run genetic --port 8001     # otro puerto para la API
```

Sin uv: `pip install -r requirements.txt` y luego `python -m genetic` (con `src/` en el `PYTHONPATH`).

Al arrancar:
- Ventana Pygame: la simulación.
- `http://127.0.0.1:8000/` es el panel de control web, que también sirve para manejar la demo desde otra pantalla o el celular.
- `http://127.0.0.1:8000/docs` es Swagger, con todos los endpoints.

## Qué se ve

| Elemento | Significado |
|---|---|
| Trazo amarillo neón con flujo animado | Mejor ruta de la generación actual |
| Bus amarillo brillante | Mejor individuo, el que llega primero |
| Buses cian → violeta translúcidos | Resto de la población, ordenada por fitness |
| Líneas magenta | Calles usadas por la población; más intensas cuanto más se usan |
| Barras verdes/ámbar/rojas en cruces | Semáforos de dos fases (horizontal/vertical) |
| Octágonos rojos | Señal de PARE (+3 s) |
| Carriles naranja → rojo | Congestión dinámica (`factor_trafico`) |
| Triángulo rojo con humo | Accidente |

Cada generación es una "carrera": los buses recorren su ruta en tiempo simulado, se detienen en los semáforos en rojo y en los PARE, y cuando llega el mejor el AG produce la siguiente generación. La gráfica inferior muestra el mejor tiempo, el promedio y el peor tiempo por generación. Los eventos aparecen como marcas verticales.

## Controles

| Acción | Efecto |
|---|---|
| Clic izquierdo en una calle | Provocar o despejar un accidente |
| Clic derecho en un semáforo | Ciclar AUTO → ROJO → VERDE |
| Shift + clic / Ctrl + clic en un nodo | Mover ORIGEN / DESTINO |
| `Espacio` | Pausa |
| `T` | Turbo: evoluciona una generación por frame, sin animar la carrera |
| `+` / `-` | Velocidad de simulación |
| `A` | Accidente aleatorio sobre la mejor ruta |
| `C` | Despejar accidentes |
| `R` | Reiniciar la población |
| `I` | Mostrar IDs de nodos (útiles para la API) |
| `L` | Mostrar u ocultar la leyenda de colores |
| `H` | Ayuda |
| `Esc` | Salir |

Al pasar el ratón sobre un nodo o una calle aparece su información: ID, velocidad, tráfico y tiempo del tramo.

## API

| Método | Ruta | Body de ejemplo |
|---|---|---|
| POST | `/api/accident` | `{"origen": 3, "destino": 4, "factor_trafico": 10.0}` |
| POST | `/api/accident/random` | — |
| POST | `/api/accident/clear` | `{"origen": 3, "destino": 4}` o sin body (todos) |
| POST | `/api/semaphore/toggle` | `{"nodo_id": 8, "estado": "ROJO"}` (`ROJO`/`VERDE`/`AUTO`) |
| POST | `/api/ga/params` | `{"mutation_rate": 0.3, "population_size": 60}` |
| POST | `/api/route` | `{"origen": 54, "destino": 53}` |
| POST | `/api/simulation` | `{"action": "turbo"}` (`pause`, `resume`, `turbo`, `normal`, `reset`, `faster`, `slower`) |
| GET | `/api/status` | Métricas de la generación actual |
| GET | `/api/map` | Nodos y aristas, con sus IDs |

```bash
curl -X POST localhost:8000/api/accident -H "Content-Type: application/json" \
     -d '{"origen": 62, "destino": 63, "factor_trafico": 12}'
```

FastAPI corre en un hilo secundario y solo encola comandos en una `queue.Queue`. Pygame, en el hilo principal, los aplica entre frames. Las métricas viajan en sentido contrario mediante una instantánea protegida con `Lock`.

## Algoritmo genético

- **Genoma**: lista de IDs de nodos, un camino continuo sin ciclos de ORIGEN a DESTINO.
- **Fitness**: `1 / tiempo_total`. `tiempo_tramo = distancia / (velocidad / factor_trafico)`, más las esperas por semáforo en rojo (según el instante real de llegada al cruce) y por PARE.
- **Población inicial**: caminatas aleatorias con retroceso, sesgadas suavemente hacia el destino.
- **Selección**: torneo con k = 3, más elitismo (2) e inmigración leve para mantener la diversidad.
- **Cruce**: se elige un nodo común a ambos padres y se intercambian los segmentos posteriores, eliminando después los bucles.
- **Mutación**: desde un nodo intermedio se recalcula un desvío aleatorio válido hasta el destino.

## Estructura

```
src/genetic/
├── main.py               # entrada: FastAPI (hilo) + bucle Pygame
├── city_map.py           # grafo, semáforos, congestión, tráfico ambiental
├── genetic_algorithm.py  # Individuo, Poblacion, cruce, mutación, fitness
├── api_routes.py         # endpoints, cola de eventos, servidor uvicorn
├── simulation.py         # reloj, carreras por generación, comandos
├── renderer.py           # mapa neón, buses, HUD y gráfica
├── web_panel.html        # panel de control web
└── config.py             # parámetros y paleta
```

## Presentación

`docs/presentacion.html` es un deck de 23 diapositivas sobre algoritmos genéticos, la lógica de la demo y casos de uso actuales. Ábrelo en el navegador: ← → para navegar, `F` para pantalla completa. Para exportarlo a PDF, usa Imprimir → Guardar como PDF.
