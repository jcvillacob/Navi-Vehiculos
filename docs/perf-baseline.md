# Baseline de rendimiento — 2026-07-11

Mediciones tomadas en el entorno dev (docker compose) antes de las optimizaciones.
Método: servicios llamados directo en el container backend (sin HTTP/auth), mediana de 5 corridas.

## 1. Latencia de servicios (lecturas)

| Servicio | Mediana | Min | Max |
|---|---|---|---|
| `list_vehicle_assignments` (799 filas, JOIN ancho + EXISTS) | 118 ms | 111 | 237 |
| `list_monthly_performance` 6 meses (2.744 filas) | 159 ms | 149 | 184 |
| `list_monthly_performance` 1 mes | 69 ms | 65 | 163 |
| `list_customers` (3 queries) | 55 ms | 52 | 56 |
| `get_availability_overview` | 44 ms | 41 | 90 |
| `get_availability_trend` 6m | 49 ms | 45 | 51 |
| `get_vehicle_ranking` | 50 ms | 44 | 52 |

- `psycopg.connect()`: **19.6 ms** mediana (17.7–34) → ~15-40 % de la latencia de cada lectura es abrir conexión. Justifica pool (Fase 2), ganancia moderada.
- EXPLAIN de rendimientos 6 meses: seq scan, 3 ms de ejecución, todo en shared buffers. **Los índices hoy no aportan nada** (3k filas); Fase 3 queda en baja prioridad hasta que crezcan los datos.
- Conclusión lecturas: ningún endpoint necesita vistas/materialización a esta escala (Fase 7 probablemente se descarta).

## 2. Jobs de cálculo (performance_calculation_jobs, duración real)

| Job | Scope | Targets | Duración |
|---|---|---|---|
| 120 | todos + disponibilidad (jun) | 1.131 | **25.4 min** |
| 118 | Carga Antioquia (frotcom, 27 placas, jul) | 27 | **16.5 min** |
| 117 | Carga Antioquia (jun) | 27 | 13.6 min |
| 119 | Carga Antioquia (jul, re-corrida) | 27 | 1.4 min |
| 111–115 | Entrekarga (geotab, 9 placas) | 9 | **0.3–0.4 min** |

Desglose del job 120 (logs): fase rendimientos 23.5 min, fase disponibilidad **1 min 53 s**
(descarga CloudFleet + cálculo 799 placas + upserts). La disponibilidad NO es el cuello.

## 3. Frotcom ya fue optimizado (commit 7a3f0cc, 2026-07-11); el costo restante está en Geotab

- **Frotcom**: los jobs 117-118 (13-16 min, 27 placas) corrieron ANTES del rework del
  cliente (chunks de trips de 6 días + retries 429 — commit 7a3f0cc del usuario). El job
  119, ya con el fix, tardó **1.4 min** (~3 s/vehículo). Frotcom dejó de ser hotspot.
- **Geotab**: ~2 s/vehículo en scope chico (9 placas → 0.3 min), 4-5 llamadas HTTP
  secuenciales por vehículo. Pero la aritmética del job 120 no cierra: 287 placas × 2 s
  ≈ 10 min, y la fase de rendimientos tomó 23.5 min (con Frotcom ya rápido). Hay ~12 min
  sin atribuir — posibles sospechosos: descarga de inventario de devices por database,
  varias databases geotab, artimo/logitracs, o costo por placa mayor en databases grandes.
  **Por eso la Fase 1 empieza instrumentando timing por provider/database antes de
  optimizar.**
- Junio por provider: geotab 287 placas, frotcom 27, artimo 10, logitracs 8.

## 4. Datos de disponibilidad (junio, post-fixes)

799 placas: 202 calculated (avg 95.4 %, min 11.5 %), 65 no_orders, 532 not_in_cloudfleet,
0 error, 0 valores fuera de rango. Pendiente validar con negocio si 267 placas es la
cobertura CloudFleet esperada (67 % not_in_cloudfleet).

## 4b. Resultado Fase 1 (2026-07-11): instrumentación + Geotab MultiCall

Instrumentación por provider/database + optimización Geotab (inventario de devices 1× por
database vía `get_cached_devices` + `get_month_data_bundle` con `api.multi_call` — 5 Gets
por vehículo en 1 round-trip, con fallback a llamadas individuales).

Medición real Harina del Valle (43 placas, junio, mismo scope antes/después):

| Componente | Antes | Después |
|---|---|---|
| Total | 134.3 s (3.12 s/placa) | **46.2 s (1.07 s/placa)** |
| resolve_devices | 18.9 s | 0.6 s |
| fetch_datos | 114.9 s | 45.1 s |
| Summary (39 calc/3 partial/1 unbound) | — | idéntico |

Proyección flota Geotab completa (287 placas): ~15 min → ~5 min.

## 5. Orden del plan según estos datos

1. **Fase 0.5** — `availability_only` en el job: botón Recalcular 25 min → ~2 min. (en curso)
2. **Fase 1 (redefinida)** — instrumentar timing por provider dentro del job y atacar
   Frotcom primero (chunks/429/paralelismo real); Geotab multicall después.
3. **Fase 2** — pool psycopg (20 ms/request + ~800 connects por job en progreso).
4. **Fase 4** — batch upserts + throttle de progreso.
5. **Fase 5/6** — paginación + frontend (preventivo).
6. **Fase 3** (índices) y **Fase 7** (vistas): pospuestas — los datos no las justifican hoy.
