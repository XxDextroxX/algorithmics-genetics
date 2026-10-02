# Especificación Técnico-Funcional: Demo de Algoritmo Genético para Tráfico Urbano

## 🎯 Objetivo General
Desarrollar una aplicación en **Python** que combine un backend con **FastAPI** y una interfaz gráfica interactiva con **Pygame**. El sistema simulará una ciudad donde una población de autobuses utiliza **Algoritmos Genéticos (AG)** para encontrar la ruta óptima de un punto Origen a un punto Destino, adaptándose dinámicamente a semáforos, señales de PARE, congestión vehicular y accidentes/bloqueos en tiempo real.

---

## 🛠️ Stack Tecnológico
- **Lenguaje:** Python 3.10+
- **Interfaz Gráfica / Renderizado:** `Pygame`
- **Servidor API / Control Remoto:** `FastAPI` + `Uvicorn`
- **Concurrencia / Comunicación:** `threading` / `queue.Queue` (para comunicar FastAPI y Pygame de forma segura)

---

## 🏗️ 1. Arquitectura del Proyecto y Comunicación

La aplicación consta de dos módulos principales ejecutándose en paralelo:

1. **Hilo Principal (Main Thread - Pygame):** Renderiza el mapa, la población de buses, maneja los FPS y ejecuta el bucle evolutivo.
2. **Hilo Secundario (FastAPI Server):** Expone un servidor HTTP local para recibir comandos externos (p. ej., desde Postman, cURL o una UI Web) y encolar eventos de modificación del entorno.

```
+------------------------+                  +------------------------+
|    Cliente / Admin     |                  |   Pygame Event Loop    |
| (Postman / Browser UI) |                  |  (Simulación & Render) |
+-----------+------------+                  +-----------+------------+
            |                                           ^
            | HTTP POST /api/accident                   |
            v                                           |
+-----------+------------+      Thread Safe Queue       |
|     FastAPI Server     | ----------------------------->|
|   (Hilo Secundario)    |    (Procesa evento en frame) |
+------------------------+                              +------------------------+
```

---

## 🏙️ 2. Entorno y Representación de la Ciudad (Grafo)

La ciudad se modela como un **Grafo Dirigido/Ponderado**:

### A. Nodos (Intersecciones)
- **Atributos:** `id` (int/str), `pos_x` (int), `pos_y` (int), `tipo` (`'ORIGEN'`, `'DESTINO'`, `'INTERSECCION'`).

### B. Aristas (Calles / Tramos)
- **Atributos:**
  - `nodo_origen`: ID del nodo inicial.
  - `nodo_destino`: ID del nodo final.
  - `distancia`: Distancia euclidiana o en píxeles.
  - `limite_velocidad`: Velocidad máxima permitida (ej. 60 km/h o 30 km/h).
  - `factor_trafico`: Multiplicador dinámico entre `1.0` (vía libre) y `5.0+` (congestión pesada/accidente).
  - `tiene_semaforo`: Booleano. Si es `True`, alterna estado (`VERDE`/`ROJO`) cada $T$ segundos.
  - `tiene_pare`: Booleano. Añade una detención fija (ej. 3 segundos de penalización).

---

## 🧬 3. Diseño del Algoritmo Genético

### A. Estructura del Genoma (Cromosoma)
Cada individuo es una **lista ordenada de IDs de Nodos** que forman un camino válido y continuo desde `ORIGEN` hasta `DESTINO`.

- **Ejemplo:** `Ruta = [0, 3, 7, 12, 19]` (Donde $0$ es Origen y $19$ es Destino).

### B. Función de Fitness (Aptitud)
Buscamos **minimizar el tiempo total de viaje**.

$$	ext{Tiempo Tramo}(e) = rac{	ext{Distancia}(e)}{	ext{Velocidad Base} / 	ext{Factor Tráfico}(e)}$$

$$	ext{Tiempo Total} = \sum_{e \in 	ext{Ruta}} 	ext{Tiempo Tramo}(e) + \sum 	ext{Penalizaciones (Semáforos en Rojo + PARE)}$$

$$	ext{Fitness} = rac{1}{	ext{Tiempo Total}}$$

### C. Operadores Genéticos

1. **Población Inicial ($N = 30$ a $50$):**
   - Generada mediante caminatas aleatorias (*random walk*) que garantizan rutas válidas sin ciclos infinitos.
2. **Selección:**
   - Selección por Torneo (tamaño $k=3$) o Ruleta.
3. **Cruzamiento (Crossover de Nodos Comunes):**
   - Se buscan intersecciones comunes entre la `Ruta_Padre1` y la `Ruta_Padre2` (excluyendo origen y destino).
   - Se intercambia el segmento posterior al nodo común para formar la descendencia.
4. **Mutación:**
   - Se selecciona un nodo aleatorio intermedio de la ruta y se desvía la trayectoria recalculando un nuevo camino aleatorio válido hacia el destino.

---

## 🖥️ 4. Visualización Interactiva con Pygame

1. **Mapa de la Ciudad:**
   - Dibujar calles mediante líneas conectando nodos (color gris o negro).
   - Indicar semáforos con círculos verdes/rojos dinámicos.
   - Marcar nodos de Origen (Azul) y Destino (Verde).
2. **Población Fantasma:**
   - Renderizar los $N$ autobuses como rectángulos/puntos semitransparentes en movimiento.
   - Resaltar la **mejor ruta de la generación actual** con un trazo grueso de color amarillo/verde neón.
3. **Dashboard / HUD en Pantalla:**
   - Número de Generación actual.
   - Tiempo de la Mejor Ruta vs. Peor Ruta.
   - Estado de los eventos (ej. *"¡Accidente en Calle (3 -> 7)!"*).

---

## 🔌 5. Endpoints de FastAPI para Control Dinámico

FastAPI expondrá endpoints para manipular la simulación en tiempo real mientras Pygame se ejecuta:

- **`POST /api/accident`**
  - **Body:** `{"origen": 3, "destino": 7, "factor_trafico": 10.0}`
  - **Efecto:** Dispara un evento de congestión extrema/bloqueo en esa calle específica.
- **`POST /api/semaphore/toggle`**
  - **Body:** `{"nodo_id": 5, "estado": "ROJO"}`
  - **Efecto:** Fuerza un semáforo a cambiar de color.
- **`POST /api/ga/params`**
  - **Body:** `{"mutation_rate": 0.15, "population_size": 50}`
  - **Efecto:** Actualiza parámetros del algoritmo genético al vuelo.
- **`GET /api/status`**
  - **Respuesta:** Métricas de la generación actual (mejor fitness, generación número X, rutas activas).

---

## 📋 Entregables Esperados para el Agente de IA

1. `main.py`: Punto de entrada que arranca el servidor FastAPI en un hilo y la ventana de Pygame en el hilo principal.
2. `city_map.py`: Clases para Grafo, Nodos, Aristas, Semáforos y simulación de tráfico.
3. `genetic_algorithm.py`: Implementación completa de la clase `Poblacion`, `Individuo`, operadores de Cruzamiento, Mutación y Fitness.
4. `api_routes.py`: Rutas de FastAPI y manejo de colas de eventos (`queue.Queue`).
5. `requirements.txt`: Dependencias (`pygame`, `fastapi`, `uvicorn`, `pydantic`).
6. `README.md`: Instrucciones para instalar y ejecutar el proyecto.
