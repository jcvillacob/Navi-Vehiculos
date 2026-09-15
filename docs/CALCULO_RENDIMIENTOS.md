# Documentación Técnica: Cálculo de Rendimientos Mensuales

**Módulo:** Rendimientos (`/rendimientos`) — corte mensual por placa
**Stack:** Python 3.x · FastAPI · psycopg 3 · APScheduler
**Fuentes de datos:** Geotab (mygeotab) · Frotcom · Artimo/Opperar · LogiTracs Triton
**Zona horaria de corte:** America/Bogota (UTC−5 → medianoche local = 05:00 UTC)
**Estado del documento:** describe el árbol de trabajo a 2026-09-03 (hardening sep-2026: migración `20260902_0001`).

---

## Tabla de Contenidos

1. [Modelo de datos](#1-modelo-de-datos)
2. [Flujo de cálculo (`calculate_monthly_performance`)](#2-flujo-de-cálculo)
3. [Estados y campos de trazabilidad](#3-estados-y-campos-de-trazabilidad)
4. [Proveedores: qué significa cada columna](#4-proveedores-qué-significa-cada-columna)
5. [Validación de plausibilidad](#5-validación-de-plausibilidad)
6. [Listado y consulta por rango](#6-listado-y-consulta-por-rango)
7. [Jobs, cron y scheduler](#7-jobs-cron-y-scheduler)
8. [Resiliencia del cliente Geotab](#8-resiliencia-del-cliente-geotab)
9. [Variables de entorno](#9-variables-de-entorno)
10. [Operación](#10-operación)
11. [Preguntas abiertas / decisiones pendientes](#11-preguntas-abiertas--decisiones-pendientes)

---

## 1. Modelo de datos

Tablas creadas por bootstrap en runtime (`rendimientos.py:_run_performance_tables_ddl_inner`) y endurecidas por la migración `app/migrations/versions/20260902_0001_rendimientos_hardening.py` (columnas, índices y CHECKs `NOT VALID` + `VALIDATE` tolerante a datos legacy).

| Tabla | Clave | Uso |
|---|---|---|
| `monthly_vehicle_performance` | `UNIQUE (customer_database_id, plate, period_month)` | Un corte por placa, database y mes. Columnas de métricas (`odo_*`, `horo_*`, `kms_*`, `hours_*`, `fuel_gallons`, `fuel_end`), retrocesos Geotab, `calculation_status`, `warnings`, `validation_flags`, `*_source`, `source_meta`, `job_id`, `last_error`, `is_stale`, `is_adhoc`. |
| `vehicle_provider_bindings` | `UNIQUE (plate, customer_database_id, provider)` | Resolución placa → id externo (`provider_vehicle_id`), `binding_status` (`resolved`/`unbound`/`error`), `is_manual` (un binding manual nunca se sobrescribe: `rendimientos.py:_upsert_binding`). |
| `performance_calculation_jobs` | índice único parcial `(month, scope_key) WHERE status IN ('queued','running')` | Historial y estado de cada corrida (UI o cron). |
| `customer_databases.provider_config` | JSONB | Config por database: `plate_prefix`, `customer_id`/`group_name` (Artimo), `codigo_empresa` (LogiTracs), `logitracs_fuel_unit`, `plausibility_overrides`. |

CHECKs de la migración: `mvp_status_chk` (estados válidos), `mvp_period_chk` (`YYYY-MM`), `mvp_nonneg_chk` (`kms_*`, `hours_*`, `fuel_gallons` ≥ 0 con `COALESCE`). Si filas legacy los violan quedan `NOT VALID` y se registra en el log.

---

## 2. Flujo de cálculo

Función: `app/services/rendimientos.py:calculate_monthly_performance(payload, progress_callback, should_stop, job_id)`.

```
POST /rendimientos/calculate ──► rendimientos_jobs.create_job ──► run_job (BackgroundTask / cron)
                                                                      │
                                                                      ▼
                                              calculate_monthly_performance(month)
   1. targets      _fetch_targets (placas con customer_database_id) + _fetch_adhoc_targets (Geotab Navitrans)
   2. existentes   _load_existing_records(month)   → se reutilizan si !force_recalculate
   3. mes anterior _load_existing_records(M-1) ──► _filter_chainable_previous (solo calculated/partial)
   4. bindings     _load_binding_map
   5. agrupar      (provider_key, customer_database_id) → provider.calculate_database_rows(...)
   6. por registro validate_record(previous, overrides) → job_id/is_stale=False → _upsert_monthly_record
   7. cascada      _mark_next_month_stale(M+1) si M+1 <= mes actual Bogotá
   8. commit por grupo provider/database
```

### 2.1 Targets

- `_fetch_targets`: `vehicle_motor_assignments` con `customer_database_id IS NOT NULL`, filtrable por `customer_id`, `customer_ids`, `customer_database_id`. El proveedor se infiere con `provider_registry.infer_provider_key(connection_type, database_name, access_url, provider_config)`; se descartan los que no soportan rendimientos (`supports_monthly_performance`).
- Ad-hoc (`include_adhoc` / `adhoc_only`): placas **sin** database, consultadas con las credenciales globales de Navitrans Geotab (`GEOTAB_USERNAME/PASSWORD/DATABASE`) bajo el cliente sintético `__navitrans_system__` (`_ensure_system_database`). Filtros: `adhoc_plates`, `adhoc_filters` (`marca`, `linea`, `nombre_vehiculo`). Estas filas llevan `is_adhoc = TRUE` y se muestran como cliente "Navitrans" / database "Geotab Global".
- **Pendientes de placa excluidos**: los vehículos registrados sin placa real (placa temporal `P-000001`, `plate_pending = TRUE`) quedan fuera de ambos caminos (`NOT a.plate_pending`). Sin placa no hay nada que buscarle al proveedor, y calcularlos solo produciría `no_data`. Entran al cálculo en cuanto alguien complete la placa desde Vehículos.

### 2.2 Encadenamiento con el mes anterior (D5)

`odo_start`/`horo_start` del mes M se toman de `odo_end`/`horo_end` del mes M−1 **solo si** ese registro está en `calculated` o `partial` (`_CHAINABLE_STATUSES`). Un mes anterior en `error`/`no_data`/`unbound` se descarta y el proveedor cae a la primera lectura del mes (con warning y `*_source = first_reading`). Geotab además encadena combustible (`fuel_end` + `source_meta.fuel_source`, ver §4.1).

### 2.3 Upsert con preservación ante error (R3)

`_upsert_monthly_record` hace `INSERT ... ON CONFLICT DO UPDATE`. Si el registro nuevo llega en `error` y la fila existente está en `calculated`/`partial` (`_UPSERT_PRESERVE_CONDITION`), se **conservan** métricas, `calculation_status`, `*_source`, `fuel_end`, `validation_flags` y `calculated_at`, y el texto del error se **anexa** a `warnings`. Siempre se reescriben `job_id`, `last_error`, `is_stale`, `source_meta`, `provider_vehicle_id`, `engine_name`. `last_error` se rellena con el primer warning cuando el registro es `error` y no trae texto explícito.

Esto aplica cuando todo un grupo provider/database falla (excepción en `calculate_database_rows`, o proveedor sin adapter): el orquestador upsertea cada placa en `error` con métricas NULL.

### 2.4 Cascada `is_stale`

Al reescribir el mes M, los cortes ya existentes de M+1 para las mismas placas y database se marcan `is_stale = TRUE` (`_mark_next_month_stale`), porque su `odo_start`/`horo_start` dependía del cierre de M. Solo si M+1 ≤ mes actual en Bogotá (`_current_month_bogota`). Un recálculo de M+1 vuelve a poner `is_stale = FALSE`.

### 2.5 Cancelación y progreso

- `should_stop` se evalúa por placa (`job_control.check_stop`) y por grupo; los proveedores re-lanzan `JobCancelled` (nunca la tragan en sus `except Exception`).
- Ante `JobCancelled` en medio de un grupo se hace `conn.rollback()` del grupo parcial; los grupos ya commiteados se conservan.
- `on_target_done` bumpea el progreso fino; al terminar el grupo se reconcilia con `max(len(records), bumps)`.

---

## 3. Estados y campos de trazabilidad

| `calculation_status` | Significado | Quién lo produce |
|---|---|---|
| `calculated` | Todos los campos base del proveedor presentes y sin degradación. | Proveedor; nunca lo sube el validador. |
| `partial` | Falta algún campo base, hubo retroceso de contador, campo anulado por plausibilidad, o regla con acción `partial`. | Proveedor (`_cap_partial`) y `performance_validation`. |
| `unbound` | No se pudo resolver el id externo de la placa en el proveedor. | Proveedor (también actualiza `vehicle_provider_bindings.binding_status`). |
| `no_data` | Id resuelto pero sin lecturas/viajes/reporte en el mes. | Proveedor. |
| `error` | Excepción por placa, fallo de autenticación, grupo entero fallido, proveedor sin adapter, o regla `negative_value`. | Proveedor, orquestador, validador. |

Campos adicionales (hardening sep-2026):

| Campo | Contenido |
|---|---|
| `warnings` | Mensajes legibles (español). Los del validador llevan prefijo `Plausibilidad: `; los retrocesos Geotab `Retroceso Geotab detectado [acumulado]`. |
| `validation_flags` | Banderas de máquina del validador (§5) más `duplicate_plate` (solo en listado, no persistida). |
| `odo_start_source`, `odo_end_source`, `horo_start_source`, `horo_end_source` | Procedencia de cada lectura: `previous`, `first_reading`, `last_reading`, `gps`, `can`, `trips`, `estimated`, `diagnostic` (`performance_providers.py:SOURCE_*`). |
| `fuel_end` | Última lectura acumulada de combustible (litros) para encadenar el mes siguiente (Geotab, Frotcom CAN). |
| `source_meta` | JSON libre por proveedor: `device_id`/`vehicle_id`, `fuel_source`, `fuel_start_source`, `fuel_unit`, `fuel_raw`, `trips_count`, `trips_dropped_boundary`, `odo_first_at`/`odo_last_at`, `artimo_trip_count`, `artimo_trips_missing`, `gps_rows`, `kms_ecm_source`, `hours_ecm_source`, `odo_start_from`. |
| `job_id` | Job que produjo/actualizó la fila (sin FK). |
| `last_error` | Último error del orquestador/proveedor, aunque la fila conserve métricas buenas (R3). |
| `is_stale` | El mes anterior fue recalculado después de este corte (§2.4). |

---

## 4. Proveedores: qué significa cada columna

Archivo: `app/services/performance_providers.py`. Registro en `_MONTHLY_PERFORMANCE_PROVIDERS` (`artimo`, `geotab`, `frotcom`, `logitracs_triton`). Regla común de delta: `_positive_delta(start, end)` devuelve `end − start`; si `end < start` devuelve `None`, deja warning "X retrocede" y el proveedor degrada a `partial` (D1, antes se recortaba a 0).

Regla común de mes: **un viaje cuenta en el mes donde TERMINA** (Artimo `select_trips_in_window`, Geotab `_filter_geotab_trips_in_window`, Frotcom `tripLimits=split`).

### 4.1 Geotab (`GeotabMonthlyPerformanceProvider`, `_calculate_geotab_vehicle_record`)

Ventana: `get_geotab_month_range` → `[día 1 05:00Z, día 1 del mes siguiente 05:00Z)`. Bundle por device: StatusData de `DiagnosticOdometerId`, `DiagnosticEngineHoursId`, `DiagnosticTotalFuelUsedId`, `DiagnosticDeviceTotalFuelId` (los dos de combustible solo en ventanas borde, `_GEOTAB_EDGE_ONLY_KEYS`) + `Trip`. 7 Gets por device.

| Columna | Fuente | Detalle |
|---|---|---|
| `odo_start` | `previous.odo_end` (`previous`) o primera lectura del mes (`first_reading`) | Odómetro en metros → km. |
| `odo_end` | Última StatusData del mes (`last_reading`) | |
| `horo_start` / `horo_end` | Igual que odómetro con EngineHours (segundos → h) | |
| `kms_ecm` | `odo_end − odo_start` | `None` + `partial` si retrocede. |
| `hours_ecm` | `horo_end − horo_start` | Ídem. |
| `kms_gps` | Σ `Trip.distance` de los viajes que terminan en el mes | |
| `hours_gps` | Σ `drivingDuration + idlingDuration` (TimeSpan .NET, `_td_to_hours`) | **Conducción + ralentí.** |
| `fuel_gallons` | `_geotab_fuel_chain`: `TotalFuelUsed` preferido, `DeviceTotalFuel` alternativo | Arranque = `previous.fuel_end` **solo si** `previous.source_meta.fuel_source` es la misma fuente; si no, primera lectura del mes (warning). Retroceso del acumulado → `None`. Litros → galones (`/3.7854118`). |
| `fuel_end` | Última lectura acumulada (L) de la fuente usada | |
| `geotab_regression_*` | `_analyze_geotab_regressions` sobre cada caída entre lecturas consecutivas (incluye el cierre previo como punto inicial) | Si el total retrocedido supera 5 % de la métrica (`_GEOTAB_REGRESSION_SIGNIFICANT_RATIO`) → `partial`. `geotab_regression_count` cuenta horómetro si `vocacional`, odómetro si no. |

`no_data` cuando no hay lecturas de odómetro **ni** de horas. `calculated` exige los 7 campos base (`odo_*`, `horo_*`, `fuel_gallons`, `kms_gps`, `hours_gps`).

Resolución de device: binding manual > índice de placas del inventario cacheado (`build_plate_index`, `lookup_plate_index`) con desempate por device activo y `preferred_id` (`_find_device_in_collection`); 2+ devices **activos** con la misma placa generan warning.

Ejecución por database (G1/G2/R6): fase 1 resuelve devices en el hilo principal; fase 2 parte las placas en chunks de `GEOTAB_BUNDLE_CHUNK_DEVICES` (14) y las procesa con `ThreadPoolExecutor(GEOTAB_MAX_WORKERS=3)`; cada worker hace **un** `multi_call` por chunk (`geotab_client.get_month_data_bundles`). `_GeotabCircuitBreaker`: tras `GEOTAB_BREAKER_THRESHOLD` (3) fallos consecutivos `transient`/`auth` se abre y los chunks restantes salen en `error` sin llamar a Geotab; un éxito reinicia el contador. Los fallos `rate_limit` y `fatal` no cuentan.

### 4.2 Frotcom (`FrotcomMonthlyPerformanceProvider`, `_calculate_frotcom_vehicle_record`)

Ventana: `get_frotcom_month_range` (resumen `mileageandtime`, fin inclusivo 04:59:59Z) y `get_frotcom_month_range_utc_bounds` (viajes, semiabierto). Placas en paralelo con `FROTCOM_MAX_WORKERS` (1–8) tras un preflight por juego de credenciales.

| Columna | Fuente | Detalle |
|---|---|---|
| `odo_start` | `previous.odo_end` > odómetro del primer viaje (`trips`/`estimated` si reconstruido) > primera lectura `vehicleCanInfo` (`can`) | |
| `odo_end` | Odómetro del último viaje (`split` en el corte) > última lectura CAN > `odo_start + mileageCanKms` (`estimated`; resumen negativo no se usa) | |
| `horo_start` / `horo_end` | `previous.horo_end` > `vehicleCanInfo.engineHours` (`can`) > chronometer actual del vehículo menos horas de viajes posteriores (`estimated`) | El resumen mensual **no** sirve como horómetro. |
| `kms_ecm` | `odo_end − odo_start`; si falta uno, `mileageCanKms` (`source_meta.kms_ecm_source`) | |
| `hours_ecm` | `horo_end − horo_start`; si no hay horómetro y no hubo retroceso, Σ horas de motor de los viajes (`trips_engine_hours`, conducción + ralentí) | |
| `kms_gps` | `mileageGpsKms` del resumen | |
| `hours_gps` | `drivingTimeSeconds` del resumen | **Solo conducción.** |
| `fuel_gallons` | `totalFuelUsed` del resumen (L); si no viene, `CAN last − CAN first`; retroceso → `None` | |
| `fuel_end` | `total_fuel_used` de la última lectura CAN | |

`no_data` si no hay resumen, ni viajes con odómetro, ni lecturas CAN.

### 4.3 Artimo / Opperar (`ArtimoMonthlyPerformanceProvider`, `_calculate_vehicle_record`)

Reportes: `trips` agregado (mes actual y anterior, por placa), `trips` detallado con lookback de 2 días (`get_trip_lookback_range`) filtrado por fin de viaje (`select_trips_in_window`), y `gps` paginado por `resource_id`.

| Columna | Fuente | Detalle |
|---|---|---|
| `odo_start` | `previous.odo_end` > odómetro del viaje de cierre del mes anterior (`trips`) > `odo_end − distancia del mes` (`estimated`, nunca negativo) > primera fila GPS (`gps`) | `_derive_start_values`. |
| `odo_end` | Odómetro del último viaje que termina en el mes (`trips`); sin viajes, último GPS (`gps`) | Bases distintas viajes/GPS generan warning. |
| `horo_start` / `horo_end` | Igual con horómetro del viaje; sin viaje, `None` | |
| `kms_ecm` / `hours_ecm` | Deltas de odómetro/horómetro | |
| `kms_gps` | Último − primer odómetro de las filas GPS | Retroceso → `None` + `partial`. |
| `hours_gps` | Σ tiempo de motor de los viajes de la ventana | **Tiempo de motor según viajes**, no GPS. |
| `fuel_gallons` | Σ consumo (L) de los viajes → galones | |

Nulos (D8, `_apply_artimo_missing_fields`): viajes sin distancia/horas/consumo se reportan en `source_meta.artimo_trips_missing`; si superan el 10 % de los viajes (`_ARTIMO_MISSING_FIELD_RATIO`) la métrica derivada se anula y el estado baja a `partial`; por debajo solo avisa. Viajes que terminan después del corte se cuentan en el mes siguiente (warning). Sin viajes en el mes → `partial` con solo GPS.

### 4.4 LogiTracs Triton (`LogitracsTritonMonthlyPerformanceProvider`, `_calculate_logitracs_vehicle_record`)

Informe operacional de flota por rango de fechas planas (`YYYY-MM-01` … último día), mes actual y anterior; el id externo es la placa normalizada.

| Columna | Fuente | Detalle |
|---|---|---|
| `odo_start` | `previous.odo_end` > "Odometro final" del informe del mes anterior (`previous`, `source_meta.odo_start_from`) > `odo_end − Kilometraje` (`estimated`) | |
| `odo_end` | "Odometro final" (`last_reading`); un 0 con kilometraje > 0 se descarta y se estima `odo_start + Kilometraje` (`estimated`) | |
| `kms_ecm` | Delta de odómetros; si no se pueden derivar ambos, "Kilometraje" del informe (`report_kilometraje`) | |
| `hours_gps` | "Tiempo Encendido(h)" | **Tiempo encendido** reportado por LogiTracs. |
| `horo_*`, `hours_ecm`, `kms_gps` | Siempre `None` | No disponibles en el informe. |
| `fuel_gallons` | "Combustible" según unidad configurada (D4) | `provider_config.logitracs_fuel_unit` manda; si falta, env `LOGITRACS_FUEL_UNIT`. Valores válidos `gal` o `l` (÷ 3.785411784). Sin unidad confirmada: **se omite**, se guarda el crudo en `source_meta.fuel_raw` y `fuel_unit = "desconocida"`. |

`calculated` exige `odo_start`, `odo_end`, `kms_ecm`, `hours_gps`, `fuel_gallons`.

### 4.5 `hours_gps` no está homologado

| Proveedor | Qué mide `hours_gps` |
|---|---|
| Geotab | Conducción + ralentí de los viajes. |
| Frotcom | Solo tiempo de conducción (`drivingTimeSeconds`). |
| Artimo | Tiempo de motor sumado de los viajes. |
| LogiTracs | "Tiempo Encendido(h)". |

Comparar `hours_gps` entre proveedores, o usarlo como sustituto de `hours_ecm`, no es válido hoy. Está documentado; no homologado (§11).

---

## 5. Validación de plausibilidad

Archivo: `app/services/performance_validation.py:validate_record(record, days_in_month, previous, overrides)`. Función pura, idempotente (descarta sus propios warnings/flags previos), nunca lanza. Solo evalúa `calculated`/`partial`; el resto pasa intacto. Nunca sube el estado. Se ejecuta en `calculate_monthly_performance` (con el M−1 encadenable como `previous`), en `preview_cpk_cutoffs` (sin previous) y en `revalidate_performance`.

Umbrales en `PlausibilityThresholds` (defaults comerciales; `vocacional=True` cambia `max_km_month` a `max_km_month_vocacional`). Overrides por database en `customer_databases.provider_config.plausibility_overrides` usando los nombres de campo (`rendimientos.py:_plausibility_overrides`): solo claves conocidas y valores numéricos; el resto se ignora con log.

| Flag | Condición | Acción |
|---|---|---|
| `negative_value` | Cualquier métrica de `METRIC_FIELDS` < 0 | `error` |
| `odo_regression` | `odo_end < odo_start` | `partial`; anula `kms_ecm` |
| `horo_regression` | `horo_end < horo_start` | `partial`; anula `hours_ecm` |
| `km_over_max` | `kms_ecm > max_km_month` (30 000 / 15 000 vocacional) | `partial` |
| `km_gps_over_max` | `kms_gps > max_km_month` | `warning` |
| `hours_over_month` | `hours_ecm` o `hours_gps` > 24 h/día × días | `partial` |
| `hours_high` | > 18 h/día × días | `warning` |
| `kmh_implausible` | `kms_ecm/hours_ecm` > 110 km/h, o < 2 km/h con > 200 km | `warning` |
| `kpg_out_of_range` | km/gal fuera de [0.3, 120] | `partial` |
| `kpg_out_of_range` | km/gal fuera de [1, 60] | `warning` |
| `gph_out_of_range` | Solo vocacional: gal/h fuera de [0.2, 25] | `warning` |
| `fuel_over_max` | `fuel_gallons > 4000` | `partial` |
| `ecm_gps_divergence` | ambos > 100 km y desvío relativo > 40 % | `partial` |
| `ecm_gps_divergence` | desvío > 15 % | `warning` |
| `hours_ecm_gps_divergence` | ambos > 20 h y desvío > 25 % | `warning` |
| `km_hours_incoherent` | 0 km con > 10 h, o > 50 km con 0 h | `partial` |
| `fuel_zero_with_km` | `fuel_gallons == 0` con km > 0 (o ya anulado antes por esta flag) | `partial`; anula `fuel_gallons` |
| `chain_broken` | `|odo_start − previous.odo_end| > 1 km` o `|horo_start − previous.horo_end| > 0.1 h` | `warning` |
| `regression_significant` | `geotab_regression_total_km > 5 % kms_ecm` (ídem horas) | `partial` |
| `source_mix` | `odo_start_source ≠ odo_end_source` (ídem horómetro) | `warning` |

Resolución: la acción más severa gana (`error` > `partial` > `warning`); `partial` solo degrada desde `calculated`. Los mensajes se escriben con prefijo `Plausibilidad: ` y magnitudes formateadas (`_fmt`).

Flag adicional no persistida: `duplicate_plate` (§6).

---

## 6. Listado y consulta por rango

`rendimientos.py:list_monthly_performance(month_from, month_to, ...)`, expuesto en `GET /rendimientos` (`app/api/routes/rendimientos.py`, permiso `rendimientos.view`).

Filtros: `customer_id`/`customer_ids`, `customer_database_id`, `plate_search` (LIKE sobre placa en mayúsculas), `motor_group`/`motor_groups` (igualdad sobre `engine_name`), `status` (lista validada contra `KNOWN_CALCULATION_STATUSES`, 400 si hay valores desconocidos), `source_provider` (lista). Siempre se exige que la placa siga asignada a la misma database (`mp.is_adhoc OR a.plate IS NOT NULL`) para no mezclar histórico de una base anterior.

Rango (`month_from ≠ month_to`), agregado por (cliente, database, placa):

| Campo | Agregación |
|---|---|
| `odo_start`, `horo_start`, `*_start_source` | Primer mes con valor no nulo (ASC). |
| `odo_end`, `horo_end`, `*_end_source`, `fuel_end` | Último mes con valor no nulo (DESC). |
| `kms_*`, `hours_*`, `fuel_gallons`, `geotab_regression_*` | `SUM` (los NULL no cuentan; un mes `partial` sin `kms_ecm` simplemente no suma). |
| `calculation_status` | Peor estado del rango (`_RANGE_STATUS_CASE_SQL`: error > unbound > no_data > partial > calculated). El filtro `status` en rango se aplica por `HAVING` sobre este agregado. |
| `warnings`, `validation_flags` | Concatenación cronológica deduplicada (`_flatten_range_warnings`). |
| `source_provider`, `provider_vehicle_id`, `job_id`, `last_error` | Del mes más reciente. |
| `is_stale`, `is_adhoc` | `BOOL_OR`. |
| `source_meta` | `{}` (no se agrega). |
| `period_month` | `MIN`; la respuesta trae `month_from`/`month_to`. |

Placas duplicadas (D12, `_flag_duplicate_plates`): si la misma placa aparece en más de una database dentro del resultado, cada fila recibe la flag `duplicate_plate` y el warning `Plausibilidad: la placa aparece en N databases; las métricas pueden duplicarse al sumar.` No se elimina ninguna fila.

---

## 7. Jobs, cron y scheduler

Archivo: `app/services/rendimientos_jobs.py`.

- **Scope**: `_compute_scope_key` = `clientes|database|av/noav/avonly|std/adhoc/adhoconly`. `create_job` rechaza (409, `JobAlreadyRunning`) un job nuevo si existe uno `queued`/`running` del mismo mes con el mismo scope **o** cuyos targets se solapan (`_jobs_overlap`): "todos los clientes" choca con cualquier subconjunto, clientes en común chocan, dos databases distintas explícitas no, `availability_only` solo choca con otro job que también escriba disponibilidad, `adhoc_only` no choca con un job estándar sin adhoc. La carrera SELECT/INSERT la cubre el índice único parcial + `UniqueViolation`.
- **Claim atómico**: `_claim_job` hace `UPDATE ... SET status='running' WHERE status='queued' RETURNING`; solo un worker ejecuta el job. `_mark_done`/`_mark_error` solo aplican desde `running`/activos: un job cancelado o reapeado nunca vuelve a `done`.
- **Heartbeat y cancelación cooperativa**: `_make_should_stop` consulta la DB como máximo cada 2 s (`_SHOULD_STOP_INTERVAL_S`); cada consulta real hace `_heartbeat` (`updated_at = NOW()`) y devuelve `True` si el job dejó de estar activo (`cancel_job` lo pasa a `error` con "Cancelado por el usuario"). Si la DB falla se asume vivo.
- **Reaper**: `reap_stale_jobs(max_age_minutes=15)` marca `error` ("Proceso reiniciado o job sin progreso por más de 15 min") los jobs `queued`/`running` sin `updated_at` reciente. Corre al arrancar el proceso, cada 10 min desde el scheduler (`rendimientos_stale_reaper`) y antes de cada cron.
- **Fase de disponibilidad**: `_run_availability_for_job` corre `availability_store.run_availability_phase` (CloudFleet) después de rendimientos si `compute_availability`. Si falla, el job queda **`done`** con `error_message = "Disponibilidad: <Tipo>: <detalle>"` (advertencia, no error). En `availability_only` la falla sí es fatal. `total_targets` se extiende con las placas de disponibilidad para que el progreso sea monótono.
- **Digest**: `summarize_recent_jobs`/`list_recent_job_alerts` (24 h) clasifican `error`, `availability_warning` (done con `error_message`) y `high_error_ratio` (> 20 % placas en error, `_DIGEST_ERROR_RATIO`); los consume el digest de alertas de las 06:00.

Cron (`app/jobs/rendimientos_cron.py:_run`): reaper → snapshot de conexión Geotab → por cada mes (`_months_to_calculate`: mes actual, y también el anterior si `día ≤ RENDIMIENTOS_PREVIOUS_MONTH_REFRESH_DAYS` = 3) `create_job(force_recalculate=True, compute_availability=True, triggered_by="cron")` + `run_job`. Si el mes ya tiene un job activo se omite (no reejecuta). `_check_month_alert` escribe `ERROR` con marcador `[ALERTA rendimientos]` si el job terminó en `error` o si `error/total > 20 %` (`ALERT_ERROR_RATIO`).

Scheduler (`app/services/scheduler.py`): APScheduler `BackgroundScheduler` en el proceso del backend, zona `America/Bogota`. `rendimientos_daily` a las **05:00** (`misfire_grace_time` 1 h, `coalesce`, `max_instances=1`); `rendimientos_stale_reaper` cada 10 min; digest de alertas a las 06:00. `DISABLE_SCHEDULER=1` lo apaga. Diseñado para un solo worker de uvicorn.

Endpoints: `POST /rendimientos/calculate` (202 con el job; 409 con el job existente), `GET /rendimientos/jobs?active=1|limit=N`, `GET /rendimientos/jobs/{id}`, `POST /rendimientos/jobs/{id}/cancel`, `GET /rendimientos/availability`, `GET /rendimientos/adhoc-filters`, `POST /rendimientos/cpk-cutoffs/preview`.

---

## 8. Resiliencia del cliente Geotab

Archivos: `app/clients/geotab_client.py`, `app/clients/geotab_rate_limiter.py`.

- **Sesión y timeout**: `get_authenticated_client` cachea una `mygeotab.API` por `database:username` con `timeout = GEOTAB_HTTP_TIMEOUT_SECONDS` (60 s). Inventario de devices cacheado 300 s (`get_cached_devices`).
- **Clasificación** (`_classify_error`): `auth` (credenciales/sesión expirada) → una re-autenticación transparente; `rate_limit` (`OverLimitException` o HTTP 429); `transient` (red/SSL/timeout, `DbUnavailableException`/`ServerException`/`TimeoutException`, HTTP 5xx, excepciones de `requests`); `fatal` (todo lo demás, se propaga).
- **Reintentos** (`_call_with_retry`): transitorios hasta `GEOTAB_RETRY_MAX_ATTEMPTS` (4) intentos con backoff exponencial full-jitter `uniform(0, min(30, 2·2^n))`; rate limit hasta `GEOTAB_RATE_LIMIT_MAX_ATTEMPTS` (3) esperando `≥ GEOTAB_RATE_LIMIT_WAIT_SECONDS` (60 s) + jitter ≤ 10 s, y penalizando el bucket local.
- **Rate limiter local (G7)**: token bucket por database (`GeotabRateLimiter`), capacidad `GEOTAB_RATE_LIMIT_BURST`, recarga `GEOTAB_RATE_LIMIT_PER_MINUTE` (900 < 1000 de Geotab). Cada `Get` cuesta 1 token; un `multi_call` de N Gets cuesta N. `acquire` espera hasta `GEOTAB_RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS` (180 s) y si expira sigue "a ciegas" con warning. `penalize` vacía el bucket y bloquea la clave 60 s ante OverLimit. Compartido por rendimientos, CPK on-demand y taller sync.
- **Bundles**: `get_month_data_bundle` (un device) y `get_month_data_bundles` (varios devices, G1) arman los mismos Gets: StatusData completo para odómetro/horas, dos ventanas borde de `GEOTAB_FUEL_EDGE_WINDOW_DAYS` (2) días para los acumulados de combustible (G5, con segunda pasada de serie completa si una ventana viene vacía) y `Trip`. `get_month_data_bundles` concatena `GEOTAB_BUNDLE_CHUNK_DEVICES` devices por `multi_call` (por defecto `GEOTAB_BUNDLE_MAX_CALLS // Gets por device` = 100 // 7 = 14). Solo un error `fatal` (p. ej. `GeotabBundleShapeError`) cae a llamadas individuales; auth/rate_limit/transient agotados se re-lanzan para no multiplicar reintentos.
- **Paralelismo y circuit breaker**: ver §4.1 (`GEOTAB_MAX_WORKERS`, `GEOTAB_BREAKER_THRESHOLD`).

---

## 9. Variables de entorno

| Variable | Default | Archivo | Propósito |
|---|---|---|---|
| `GEOTAB_USERNAME` / `GEOTAB_PASSWORD` / `GEOTAB_DATABASE` | obligatorias para ad-hoc | `core/config.py:load_geotab_config` | Credenciales globales Navitrans (database sintética `__navitrans_system__`, snapshot de conexión). |
| `GEOTAB_HTTP_TIMEOUT_SECONDS` | `60` | `geotab_client.py` | Timeout HTTP de `mygeotab.API`. |
| `GEOTAB_RETRY_MAX_ATTEMPTS` | `4` | `geotab_client.py` | Intentos totales ante errores transitorios. |
| `GEOTAB_RATE_LIMIT_WAIT_SECONDS` | `60` | `geotab_client.py` | Espera mínima tras `OverLimitException`/429 (y duración de la penalización local). |
| `GEOTAB_RATE_LIMIT_MAX_ATTEMPTS` | `3` | `geotab_client.py` | Intentos ante rate limit. |
| `GEOTAB_RATE_LIMIT_ACQUIRE_TIMEOUT_SECONDS` | `180` | `geotab_client.py` | Espera máxima en el limitador local antes de seguir sin reserva. |
| `GEOTAB_RATE_LIMIT_PER_MINUTE` | `900` | `geotab_rate_limiter.py` | Tokens por minuto por database. |
| `GEOTAB_RATE_LIMIT_BURST` | `= PER_MINUTE` | `geotab_rate_limiter.py` | Capacidad del bucket. |
| `GEOTAB_RATE_LIMIT_PER_USER` | off | `geotab_rate_limiter.py` | `1/true/yes/on`: clave del bucket por database + usuario. |
| `GEOTAB_BUNDLE_MAX_CALLS` | `100` | `geotab_client.py` | Tope de Gets por `multi_call`; deriva el tamaño de chunk si no se fija el siguiente. |
| `GEOTAB_BUNDLE_CHUNK_DEVICES` | derivado (14) | `geotab_client.py` / `performance_providers.py` | Devices por `multi_call`. El proveedor usa 14 como default propio. |
| `GEOTAB_FUEL_EDGE_WINDOW_DAYS` | `2` | `geotab_client.py` | Días de las ventanas borde para acumulados de combustible. |
| `GEOTAB_MAX_WORKERS` | `3` | `performance_providers.py` | Hilos por database Geotab (chunks en paralelo). |
| `GEOTAB_BREAKER_THRESHOLD` | `3` | `performance_providers.py` | Fallos consecutivos `transient`/`auth` que abren el circuit breaker de la database. |
| `FROTCOM_MAX_WORKERS` | `4` (tope 8) | `performance_providers.py` | Placas Frotcom en paralelo por grupo. |
| `LOGITRACS_FUEL_UNIT` | `""` | `performance_providers.py` | `gal` o `l`; fallback cuando `provider_config.logitracs_fuel_unit` no está. Vacío = combustible omitido. |
| `LOGITRACS_TIMEOUT_SECONDS` | `60` | `logitracs_triton_client.py` | Read timeout HTTP (connect fijo 10 s). |
| `RENDIMIENTOS_PREVIOUS_MONTH_REFRESH_DAYS` | `3` | `rendimientos_cron.py` | Días del mes en que el cron recalcula también el mes anterior. |
| `DISABLE_SCHEDULER` | off | `scheduler.py` | `1/true/yes` apaga APScheduler (cron, reaper, digest, backups). |
| `UVICORN_RELOAD` | vacío | `backend/Dockerfile` | Si está definida, el CMD agrega `--reload` a uvicorn. |
| `CLOUDFLEET_API_KEY`, `CLOUDFLEET_API_URL`, `CLOUDFLEET_HTTP_TIMEOUT`, `CLOUDFLEET_REQUEST_DELAY`, `CLOUDFLEET_RATE_LIMIT_DELAY`, `CLOUDFLEET_MAX_RETRIES` | ver `core/config.py` | `core/config.py` | Fase de disponibilidad (no afectan rendimientos). |

---

## 10. Operación

### 10.1 Revalidar plausibilidad sobre cortes persistidos

```bash
# Dentro del contenedor backend (cwd /app)
python -m app.jobs.revalidate_performance --from 2026-01 --to 2026-08            # dry-run
python -m app.jobs.revalidate_performance --from 2026-01 --to 2026-08 --json     # reporte JSON
python -m app.jobs.revalidate_performance --to 2026-08 --customer-database-id 12 # una database
python -m app.jobs.revalidate_performance --from 2026-01 --to 2026-08 --apply    # persiste
```

`app/jobs/revalidate_performance.py`: carga `[--from − 1 mes, --to]` (el mes previo solo sirve como `previous`), reconstruye cada `MonthlyPerformanceRecord`, aplica `validate_record` con el M−1 **original** de la misma placa/database y los `plausibility_overrides` de la database. Defaults: `--to` = mes actual Bogotá, `--from` = `--to`.

El dry-run reporta por mes y total: filas evaluadas, filas que cambian (estado, warnings, flags o métricas anuladas), transiciones de estado (`calculated->partial: N`), frecuencia de cada flag y una muestra de hasta 30 filas priorizando cambios de estado. Con `--apply` se actualizan `calculation_status`, `warnings`, `validation_flags`, `kms_ecm`, `hours_ecm`, `fuel_gallons` en una sola transacción. Nunca toca `unbound`/`no_data`/`error`. Exit code siempre 0.

### 10.2 Recalcular un mes

- UI: botón de recálculo → `POST /rendimientos/calculate` con `force_recalculate`. El 409 devuelve el job activo para seguirlo.
- CLI: `python -m app.jobs.rendimientos_cron` (mismo flujo que el scheduler; respeta jobs activos).
- Seguimiento: `GET /rendimientos/jobs/{id}` (progreso `processed_targets/total_targets`, `summary`, `error_message`). Logs: `Rendimientos <mes> (job <id>): <provider>/<database> -> N placas en X s` y `Geotab <mes> [<db>]: ... workers= chunks= breaker=`.

### 10.3 Reactivar `--reload` en desarrollo

La imagen arranca sin `--reload` (`backend/Dockerfile`: `uvicorn ... ${UVICORN_RELOAD:+--reload}`). Para desarrollo, definir `UVICORN_RELOAD=1` en `.env` (lo lee `docker-compose.yml` vía `env_file`) o en `environment:` del servicio `backend`, y recrear el contenedor. Con `--reload`, cada reinicio del worker mata los `BackgroundTask` en curso: el reaper marcará esos jobs como `error` a los 15 min (o al arrancar el proceso).

### 10.4 Consultas útiles

```sql
-- Cortes desactualizados por recálculo del mes anterior
SELECT period_month, plate, customer_database_id FROM monthly_vehicle_performance WHERE is_stale;

-- Distribución de flags de un mes
SELECT f, COUNT(*) FROM monthly_vehicle_performance, jsonb_array_elements_text(validation_flags) f
WHERE period_month = '2026-08' GROUP BY f ORDER BY 2 DESC;

-- Jobs con advertencia de disponibilidad
SELECT id, month, error_message FROM performance_calculation_jobs WHERE status='done' AND error_message IS NOT NULL;
```

---

## 11. Preguntas abiertas / decisiones pendientes

1. **Umbrales de plausibilidad**: los defaults de `PlausibilityThresholds` (30 000 km/mes comercial, 15 000 vocacional, 110 km/h, km/gal 1–60, gal/h 0.2–25, 4000 gal) son propuestas técnicas; falta validarlos con el negocio por segmento (comercial vs vocacional) y decidir si se fijan por database vía `plausibility_overrides`.
2. **Unidad de combustible LogiTracs**: sin `logitracs_fuel_unit`/`LOGITRACS_FUEL_UNIT` el combustible se omite. Hay que confirmar con LogiTracs si "Combustible" viene en litros o galones y fijar la config por database.
3. **Homologar `hours_gps`**: hoy mide cosas distintas por proveedor (§4.5). Opciones: renombrar/separar en `hours_driving` + `hours_idle`, o normalizar a "conducción + ralentí" donde el proveedor lo permita (Frotcom expone ambas en los viajes).
4. **Aplicar la revalidación al histórico**: `revalidate_performance --apply` cambia estados y anula métricas de filas ya exportadas al Portal Clientes (`/integration`). Falta acordar ventana, comunicación y si el Portal debe re-consumir.
5. **Cascada `is_stale`**: hoy solo marca; no dispara recálculo automático de M+1. Decidir si el cron debe recalcular los meses `is_stale` o si basta con la señal en UI.
6. **Preservación ante error (R3)**: `source_meta` no está en `_UPSERT_PRESERVED_COLUMNS`, así que una fila preservada pierde `fuel_source` y el mes siguiente no puede encadenar combustible Geotab. Evaluar incluirlo.
