# Revisión y plan de mejora — módulo Rendimientos (2026-09-02)

> **Estado 2026-09-02 (noche): Fases 1-5 implementadas** en la rama `feat/integration-vocacional-export`.
> Semántica actual en `docs/CALCULO_RENDIMIENTOS.md`; mediciones en `docs/perf-baseline.md` §5.
> Pendiente de decisión de negocio: umbrales de plausibilidad, unidad de combustible LogiTracs,
> homologación de `hours_gps`, y aplicar `revalidate_performance --apply` al histórico (118/1445 filas cambian).
> No implementado por decisión: D6 (homologar `hours_gps`) — solo documentado.

Revisión completa del pipeline `/rendimientos` (cálculo mensual por proveedor, jobs, API de lectura, tabla y frontend).
Método: 5 auditorías paralelas (Geotab/multicall, resiliencia de jobs, lógica de datos, frontend, API/BD) más
verificación directa contra `navi_db` y el código. Todo lo listado tiene evidencia `archivo:línea` o consulta SQL.

Rutas relativas a `backend/` salvo indicación. Línea base previa: `docs/perf-baseline.md` (fases 0-2, jul 2026).

---

## 0. Evidencia real (navi_db, 2026-09-02)

| Señal | Dato |
|---|---|
| Jobs cron (706 targets, 05:00 Bogotá) | 7–18 min; 12 de 219 jobs en `error` |
| Jobs 211, 212, 214, 218 en error | `Disponibilidad: CloudFleetUnavailableError…` → fase disponibilidad tumba el job entero aunque rendimientos terminó |
| Placas `error` ago-2026 | 42, todas `OverLimitException. API calls quota exceeded. Maximum admitted 1000 per 1m.` (cron 2026-09-01) |
| Filas históricas (3.901) con valores imposibles | km>30k: 13 · h>744: 20 · km/gal>60: 19 · km/gal<1: 100 · odo_end<odo_start: 17 · horo_end<horo_start: 22 · km=0∧h>20 "calculated": 27 · km>500∧h=0 "calculated": 18 |
| ECM vs GPS divergen >30 % (geotab, `calculated`, jun+) | 51 filas |
| Ejemplos `calculated` | VMU022 ago: 1.733.035 km ECM vs 3.988 GPS · VMU039 jul: 479.119 km / 23.446 h / 71.073 gal · JTZ361 ago: 17.397 km ECM vs 17 GPS · VMU037 ago: 0 km ECM (retroceso) con 6.414 GPS |
| Warning más frecuente | "Odómetro inicial tomado de la primera lectura del mes (sin registro previo)" ×103 |
| Contenedor backend | `uvicorn --reload` en `Dockerfile:19` (prod) → cualquier cambio de archivo mata el job en curso |

Conclusión: los tres síntomas del usuario (poca resiliencia, datos ilógicos, filtros pobres) son reales y medibles.
Multicall SÍ se usa (Fase 1 jul-2026), pero solo **por vehículo**, no entre vehículos, y sin paralelismo.

---

## 1. Hallazgos por área

### 1.1 Geotab / eficiencia
| # | Sev | Dónde | Problema | Fix |
|---|---|---|---|---|
| G1 | alta | `performance_providers.py:649-658`, `geotab_client.py:915-984` | 1 multi_call de 5 Gets **por placa** → 287 round-trips por job. `multi_call_with_retry` ya acepta listas arbitrarias (`geotab_taller_sync.py:91` lo usa cross-device) | Resolver devices primero, armar N×5 Gets, despachar en chunks de 10-20 vehículos (50-100 Gets); rebanar resultados |
| G2 | alta | `performance_providers.py:848`, `rendimientos.py:1165` | Loop placas y databases estrictamente secuencial. Frotcom ya tiene `ThreadPoolExecutor` (`FROTCOM_MAX_WORKERS`); Geotab no | `GEOTAB_MAX_WORKERS=3-4` por database sobre los chunks; recolectar por futures |
| G3 | alta | `geotab_client.py:618-697` | `_is_network_error` es matching de substrings; `OverLimitException`, `DbUnavailableException`, HTTP 429/500 no reintentan → placa `error` (42 casos reales) | Inspeccionar `exc.name` / `exc.response.status_code`; backoff exponencial con jitter; para OverLimit esperar ≥60 s |
| G4 | media | `geotab_client.py:550` | `mygeotab.API` con timeout default 300 s; placa colgada = 15 min de job | `timeout=60` (90-120 con chunks) |
| G5 | media | `geotab_client.py:938-948` | StatusData de combustible del mes completo sin `resultsLimit`, solo se usan extremos; `sorted` redundante | 2 Gets con `resultsLimit=1` en ventanas acotadas; quitar sort |
| G6 | media | `geotab_client.py:533-601`, `rendimientos.py:355-380` | Cache de devices por `database:username`; rendimientos usa credencial fija, no el pool LRU de `motor_catalog:3600` → cuota concentrada en un usuario | Clave por `database_name`; rotar credenciales con el pool |
| G7 | media | `scheduler.py` | `geotab_taller_sync` (30 min), snapshot de conexión y cron de rendimientos comparten la cuota 1000/min de las mismas databases | Rate limiter compartido por database (token bucket en proceso) |
| G8 | media | `performance_providers.py:857-869` | `find_matching_devices` + `_find_device_in_collection` = 2 pasadas O(N·M) por placa | Índice `dict[placa_normalizada → devices]` por database |
| G9 | baja | `geotab_client.py:966-976` | Fallback secuencial tras multi_call fallido por red: hasta 15 intentos, ~5 min por placa | Fallback solo ante error lógico, no de red |
| G10 | baja | `rendimientos.py:652, 782` | Cutoff CPK y granular ignoran `get_cached_devices`/multicall | Reusar helpers de Fase 1 |

Estimación G1+G2+G5: fetch Geotab 287 placas ≈ 5 min → 1–1.5 min. G3+G4 acotan el peor caso.

### 1.2 Resiliencia de jobs
| # | Sev | Dónde | Problema | Fix |
|---|---|---|---|---|
| R1 | alta | `routes/rendimientos.py` (BackgroundTasks), `Dockerfile:19` | Job corre en threadpool del mismo uvicorn con `--reload`; reinicio deja fila `running` eterna; índice único parcial bloquea nuevos jobs del scope (409) | Reaper al arrancar y en cron (`running` con `updated_at < now()-15min` → error "Proceso reiniciado"); quitar `--reload` en prod |
| R2 | alta | `rendimientos_cron.py:75-88`, `rendimientos_jobs.py:427-433` | Cron ante `JobAlreadyRunning` toma `exc.job` y lo **vuelve a ejecutar** → doble ejecución concurrente sobre las mismas filas | Cron: log y `continue`; `run_job`: claim atómico `UPDATE … SET status='running' WHERE status='queued' RETURNING id` |
| R3 | alta | `rendimientos.py:990-1013, 1213-1240` | `force_recalculate=True` + proveedor caído → upsert `error` pisa valores buenos con NULL; rompe cadena de odómetro del mes siguiente | No degradar `calculated→error`: conservar valores, guardar error en `warnings`/`last_error` |
| R4 | alta | `rendimientos_jobs.py:374-419` | Fallo de CloudFleet en fase disponibilidad marca todo el job `error` (jobs 211/212/214/218) aunque rendimientos terminó | Fases independientes: `status=done_with_warnings`, `availability_error` separado |
| R5 | alta | `rendimientos_jobs.py:592-616`, `:340-372` | Cancelación solo cosmética (nadie lee el estado); `_mark_done` sin `AND status='running'` sobreescribe "Cancelado" con `done` | `should_stop()` cooperativo por placa; `WHERE status='running'` en `_mark_*` |
| R6 | alta | `geotab_client.py:669-730` | Sin circuit breaker: Geotab caído = 3-5 min por placa × 200 placas | Tras 3 fallos de red consecutivos por database, marcar resto `error 'Geotab no disponible'` sin llamar |
| R7 | media | `logitracs_triton_client.py:166-261`, `artimo_client.py:286-415`, `frotcom_client.py:154` | Logitracs: 5 GET **sin timeout**; Artimo: cero retries, paginación recursiva sin tope; Frotcom: no reintenta `ConnectionError/Timeout` | Helper común `retry_http(retry_on=(429,5xx,ConnectionError), backoff exp+jitter, max=3)`; timeouts en todo |
| R8 | media | `rendimientos_jobs.py:151-165` | Scope key distingue `av/noav` → UI y cron corren en paralelo sobre el mismo mes | Guard por `(month)` o `pg_advisory_xact_lock(hash(month))` |
| R9 | media | `rendimientos.py:1213-1240`, providers | `except Exception` sin `logger.exception`; sin `job_id` en logs | Log con traceback + job_id + placa |
| R10 | media | `rendimientos_cron.py`, `scheduler.py:317` | Cron fallido no alerta a nadie; digest operativo no consulta jobs | Alerta si `status=error` o `error/total > umbral`; métrica en digest |

### 1.3 Lógica de datos ("datos ilógicos")
| # | Sev | Dónde | Problema | Fix |
|---|---|---|---|---|
| D1 | alta | `performance_providers.py:195,223,227,719,738,1076,1120,1596` | `max(0, end-start)` silencia retrocesos/cambios de equipo como **0 km/0 h con `calculated`** (VMU037) | Si `end<start` → métrica `None`, warning con magnitud, `partial` |
| D2 | alta | `performance_providers.py:695-761`, `_analyze_geotab_regressions` | Retroceso detectado se guarda solo como warning; valores absurdos quedan `calculated` (VMU022 1.7M km, VMU039 23k h) | Umbral de plausibilidad → `partial`; regresión >5 % del total → `partial` |
| D3 | alta | `performance_providers.py:118-166, :214` (Artimo) | Mes sin viajes cierra con odómetro GPS de otra base; mes siguiente hereda → km fantasma o clamp a 0 | Persistir `odo_end_source`; no encadenar bases distintas; validar `kms_ecm ≈ Σdistance_viajes ±5 %` |
| D4 | alta | `performance_providers.py:1617-1620` | Logitracs `extract_fuel_liters` → `fuel_gallons` directo (29 filas "unidad por confirmar") | Confirmar unidad; mientras, `None` + `partial` |
| D5 | media | `rendimientos.py:1136-1137` | Cierre previo se usa sin mirar `calculation_status`; previo `error/partial` propaga; recalcular M no invalida M+1 | Usar previo solo si `calculated/partial` con fuente compatible; marcar M+1 `stale` |
| D6 | media | providers (:770, :1136, :219, :1616) | `hours_gps` significa 3 cosas distintas según proveedor (conducción+ralentí / solo conducción / ECM de viajes / encendido) | Documentar y homologar (Frotcom sumar ralentí) |
| D7 | media | providers (:777, :244, :1161, :1622) | Definición de `partial` distinta por proveedor; ninguno marca `km=0∧h>0` | Regla común en capa de validación |
| D8 | media | `artimo_client.py:213-216` | Nulos como 0 en distancia/horas/combustible → KPG inflado sin aviso | Contar viajes sin dato; `None` si faltan >10 % |
| D9 | media | `performance_providers.py:747-761` | Combustible Geotab usa primera/última lectura del mes; odómetro usa cierre previo → KPG sesgado | Persistir `fuel_end` acumulado y encadenar |
| D10 | media | `geotab_client.py:915-975` | Trips Geotab que cruzan medianoche del 1° se cuentan en ambos meses (Artimo ya lo resolvió) | Filtrar por `trip.stop ∈ [from,to)` |
| D11 | media | `rendimientos.py:113-140`, `schemas/vehicle.py:878-886` | Sin `CHECK`/`ge=0` en métricas; `period_month` y `calculation_status` sin CHECK | Constraints en migración + `Field(ge=0)` |
| D12 | media | `UNIQUE(customer_database_id, plate, period_month)` | Misma placa en dos databases (Geotab+Frotcom) → listado suma doble | Dedupe por placa con proveedor preferido |
| D13 | baja | `performance_providers.py:607-633` | `_td_to_hours` devuelve 0 para TimeSpan con fracción (`"00:15:30.1230000"`). **Latente**: mygeotab 0.8.3 deserializa durations (970/970 filas OK); rompe si se cambia versión o llega string crudo | Regex robusto + test |
| D14 | baja | `rendimientos.py:1380-1449` | Rango multi-mes suma filas `error/partial`, descarta `warnings` | Exponer `months_present/expected`; conservar warnings |

### 1.4 API / BD
| # | Sev | Dónde | Problema | Fix |
|---|---|---|---|---|
| A1 | alta | `rendimientos.py:1425, 1437` | **Bug**: consulta por rango omite `a.vocacional` en SELECT y GROUP BY → en rangos multi-mes todo se muestra como km (la de mes único sí lo trae, :1364) | Añadir columna al SELECT y GROUP BY + test |
| A2 | alta | DDL `rendimientos.py:113-141` | Solo índice UNIQUE `(customer_database_id, plate, period_month)`; filtro real es `period_month + customer_id` → seq scan | Migración: `(period_month, customer_id)` y `(plate, period_month)` |
| A3 | media | `routes/rendimientos.py:31-45` | Sin filtros server-side de `status`, `category`, `source_provider`; `motor_group` valor único; sin `limit/offset/total` | `Query(list[str])` repetidos; `ANY(%s)`; status en rango vía `HAVING` |
| A4 | media | `motor_catalog.py:212-260` vía `_ensure_performance_tables` | Cada GET ejecuta `_sync_inferred_provider_types` (SELECT + posibles UPDATE) | Sacar del path de lectura o cachear N min |
| A5 | media | `rendimientos.py:558, 745, 1461` | `psycopg.connect` directo en lecturas ligeras (no usan pool) | Migrar a `db_conn()` |
| A6 | media | tablas core sin migración Alembic | `monthly_vehicle_performance`, `vehicle_provider_bindings` existen solo por DDL runtime; `ALTER … ADD COLUMN IF NOT EXISTS` en cada arranque | Mover a Alembic; `_ensure_*` no-op en prod |
| A7 | media | esquema | Sin auditabilidad: falta `job_id`, timestamps de lecturas, device id, fuente de cada extremo | `source_meta JSONB` + `job_id FK` |
| A8 | baja | `rendimientos.py:1449` | Rango calcula `jsonb_path_query_array(...)` de warnings y lo descarta | Eliminar |

### 1.5 Frontend (`frontend/src/pages/RendimientosPage.jsx`, 2.087 líneas)
| # | Sev | Dónde | Problema | Fix |
|---|---|---|---|---|
| F1 | alta | `:1444-1471` | Cliente/Categoría/Grupo motor usan `SingleSelectFilter` (radio) | Reusar `components/MultiSelectFilter.jsx` (ya existe, usado en `VehiclesPage.jsx:990`); `filters.*` → arrays; `filterRows` con `Set.has` |
| F2 | alta | `:1385-1400, :1030` | Estado es toggle XOR de un solo chip | `status: []`, chips OR |
| F3 | alta | `:590-634, :1094` | Dos loops de polling en paralelo (closure `calculating` siempre `false`) | `activePollRef` + hook único `usePerformanceJobs` |
| F4 | alta | `:636-649, :748-777` | Sin AbortController ni token de secuencia → respuesta vieja pisa la nueva | `requestIdRef` o `AbortController` en `fetchWithAuth` |
| F5 | alta | `:1339-1342` (backend) vs tabla | `warnings` y `geotab_regression_*` llegan y **no se muestran**; en rango se vacían | Columna "Alertas" con tooltip + flags de fila (`kms<0`, `h>0∧km=0`, KPG>50) |
| F6 | media | `:392, :1523` | Filtros viajan por `location.state`; F5 o compartir link los pierde | `useSearchParams`, params repetidos (`?client=A&client=B&status=partial`) |
| F7 | media | `:1054, :576-580` | Polling fijo 3 s + historial 5 s sin backoff ni pausa con tab oculta | Backoff 3→10 s; `visibilityState` |
| F8 | media | `:716-777` | 3 fetches desacoplados por cambio de rango; `connStats` hace 1 request **por mes** | Hook `useRendimientosData` con `Promise.all`; backend acepta rango |
| F9 | media | `:826-880, :1508, :1637` | `plateSearch` sin debounce recalcula 4 memos; `ctx` por fila; `getValue` 2× por celda | `useDeferredValue`; memoizar `ctx` |
| F10 | media | `:188-250` | `FilterDropdown` duplica MultiSelectFilter | Borrar |
| F11 | media | archivo completo | 2.087 líneas; paginación duplicada 2×; helpers duplicados (`formatNumber`, `formatMonthLabel`, `getCurrentMonth`) | Split en `features/rendimientos/` (ver §3) |
| F12 | baja | `:1443-1465, :1663, :1655` | Labels sin `htmlFor`; botones `‹ ›` sin `aria-label` | Corregir |
| F13 | baja | `:1945` | "Próximamente" en Calcular Disponibilidad aunque ya funciona | Quitar |

---

## 2. Plan de mejora por fases

Orden por impacto/riesgo. Cada fase termina con: tests, suite (`docker compose exec -T backend python -m pytest tests/ -q`), medición y commit.
Metodología sugerida (misma de jul-2026): spec quirúrgica → `opencode` ejecuta → Claude verifica diff/tests → commit.

### Fase 1 — Cortar sangrado (1 día) · sin cambios de esquema
1. **A1** fix `vocacional` en rango + test. (30 min)
2. **G3** retry para `OverLimitException`/`DbUnavailableException`/HTTP 429-5xx con backoff exp+jitter (OverLimit ≥60 s). (2 h)
3. **G4** `timeout=60` en `mygeotab.API`; **R7** timeouts en Logitracs. (1 h)
4. **R2** claim atómico en `run_job` + cron no reejecuta job activo. (1 h)
5. **R1** reaper de jobs `running` huérfanos al arrancar scheduler y en cron. (1 h)
6. **R4** fase disponibilidad no tumba el job: `done` + `availability_error`. (1 h)
7. **R9** `logger.exception` + `job_id` en todos los catch. (1 h)
8. Quitar `--reload` del CMD de producción (Dockerfile o override en compose). (15 min)

Resultado esperado: cero placas `error` por cuota; cron ya no falla por CloudFleet; no más jobs zombis.

### Fase 2 — Datos confiables (2-3 días) · migración Alembic
1. **D1/D2/D7** `app/services/performance_validation.py`: función pura `validate_record(record, days_in_month, vocacional)` invocada antes de cada upsert. Tabla de reglas:

   | Métrica | Regla | Acción |
   |---|---|---|
   | `odo_end<odo_start` / `horo_end<horo_start` | delta negativo | métrica `None`, **partial**, warning con magnitud |
   | `kms_ecm` | >30.000/mes comercial · >15.000 vocacional | **partial** |
   | `hours_*` | >24·días | **partial**; >18·días warning |
   | `kms/hours` | >110 km/h ó <2 con km>200 | warning |
   | `kms/fuel` | fuera 1–60 km/gal | warning; fuera 0.3–120 **partial** |
   | `fuel` | >4.000 gal/mes | **partial** |
   | ECM vs GPS | ambos >100 y desvío >15 % | warning; >40 % **partial** |
   | `km=0∧h>10` ó `km>50∧h=0` | incoherencia | **partial** |
   | `km>0∧fuel=0` | | `fuel=None`, **partial** |
   | `odo_start` vs `previous.odo_end` | difieren >1 km | warning "cadena rota" |
   | `geotab_regression_total_km > 5 % kms` | | **partial** |
   | cualquier valor <0 | | **error** |

   Umbrales por `vocacional` y `provider_config.plausibility_overrides`. Tests por regla.
2. **R3** upsert no degrada `calculated→error`: conserva valores, guarda `last_error`.
3. **D5** cierre previo solo si `status ∈ {calculated, partial}`; recalcular M marca M+1 `stale` (o lo encola).
4. **D3/D9/A7** columnas nuevas: `odo_end_source`, `horo_end_source`, `fuel_end`, `source_meta JSONB`, `job_id`. No encadenar bases distintas.
5. **D11/A2/A6** migración Alembic: tablas core + índices `(period_month, customer_id)`, `(plate, period_month)` + CHECKs (status, period_month, no negativos).
6. **D10** trips Geotab por `stop ∈ [from,to)`. **D4** Logitracs combustible → `None` hasta confirmar unidad. **D8** Artimo nulos.
7. **D13** `_td_to_hours` regex robusto + test con strings.
8. Script de re-validación: correr `validate_record` sobre histórico y reportar cuántas filas cambian de estado (esperado: ~200 de 3.901).

Resultado esperado: ninguna fila `calculated` con valor imposible; frontend puede confiar en `status`.

### Fase 3 — Velocidad y resiliencia estructural (2-3 días)
1. **G1** multi_call cross-vehículo en chunks (10-20 placas). Medir con Harina del Valle (43 placas) como en Fase 1 jul-2026.
2. **G2** `ThreadPoolExecutor(GEOTAB_MAX_WORKERS=3)` por database sobre chunks.
3. **G7** rate limiter por database compartido entre rendimientos, taller sync y snapshot (token bucket 900/min).
4. **R6** circuit breaker por database (3 fallos de red consecutivos → resto `error 'Geotab no disponible'`).
5. **R5** cancelación cooperativa (`should_stop` por placa) + `WHERE status='running'` en `_mark_*`.
6. **G5/G8/G9/G10** volumen fuel con `resultsLimit`, índice de placas, fallback solo lógico, cutoff/granular con cache.
7. **R7** helper `retry_http` común (Frotcom, Artimo, Logitracs); tope en paginación Artimo.
8. **R8** scope por mes; **R10** alerta en digest.
9. **A4/A5** sync de tipos fuera del GET; lecturas al pool.

Resultado esperado: job diario 706 placas ~10 min → ~3-4 min; peor caso acotado; cancelar cancela.

### Fase 4 — Frontend (2-3 días)
1. **F11** split a `features/rendimientos/`:
   ```
   features/rendimientos/
     columns.js · filters.js
     hooks/useRendimientosFilters.js (useSearchParams, arrays)
           useRendimientosData.js (Promise.all + race guard)
           usePerformanceJobs.js (poll único, backoff, visibility)
           useConnectionCalendar.js
     components/RendimientosSummary · RendimientosFilterBar · RendimientosTable
                TablePagination (genérico) · JobProgressBar · JobHistoryCard
                CalculateModal · ConnectionCalendarPopover
   pages/RendimientosPage.jsx (~150 líneas)
   utils/formatters.js (formatNumber, formatMonthLabel, getCurrentMonth unificados)
   ```
2. **F1/F2/F10** multi-select con `MultiSelectFilter` existente para cliente, categoría, grupo motor, estado (OR); borrar `FilterDropdown`.
3. **F6** filtros en URL con params repetidos; el backend ya acepta `customer_ids` repetido.
4. **F3/F4/F7** polling único con backoff y pausa en tab oculta; race guard en cargas.
5. **F5** columna "Alertas" (warnings + regresiones) y flags visuales de fila; con Fase 2 basta pintar `partial` y mostrar warnings.
6. **F8** `connStats` por rango (backend `month_from/month_to`, como ya hace `connection-calendar`).
7. **F9/F12/F13** debounce, memo, a11y, textos.
8. **A3** (opcional): filtros server-side `status/category/source_provider/motor_group` como listas si el dataset crece; hoy client-side es aceptable (<5k filas).

### Fase 5 — Consolidación (medio día)
- Actualizar `docs/perf-baseline.md` con mediciones antes/después de Fase 3.
- `docs/CALCULO_RENDIMIENTOS.md`: semántica por columna y proveedor (D6), reglas de plausibilidad, estados.
- Memoria del proyecto.

---

## 3. Riesgos y decisiones abiertas
- **Unidad de combustible Logitracs** (D4): requiere confirmación con el proveedor; hasta entonces `None`.
- **Umbrales de plausibilidad**: valores iniciales propuestos; validar con negocio (vocacional vs comercial) antes de Fase 2.
- **Cuota Geotab compartida** (G7): el paralelismo de Fase 3 sin rate limiter empeora G3; implementar G7 antes o junto con G2.
- **Re-validación de histórico** (Fase 2.8): cambia estados de filas ya publicadas al Portal Clientes; coordinar.
- **`--reload` en prod**: confirmar que no dependen de hot-reload en el servidor actual antes de quitarlo.
