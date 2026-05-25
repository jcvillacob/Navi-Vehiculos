# Documentación Técnica: Cálculo de Disponibilidad de Flota

**Sistema:** Dashboard de Flota Video Wall — Versión 2.5  
**Stack:** Python 3.x · Flask · Pandas · APScheduler  
**Fuente de datos:** CloudFleet API v1  
**Zona horaria:** America/Bogota (UTC−5)

---

## Tabla de Contenidos

1. [Variables de Entorno Necesarias](#1-variables-de-entorno-necesarias)
2. [Arquitectura y Flujo de Datos](#2-arquitectura-y-flujo-de-datos)
3. [Integración con la API de CloudFleet](#3-integración-con-la-api-de-cloudfleet)
4. [Estructura de los Datos](#4-estructura-de-los-datos)
5. [Agrupación de Vehículos por Flota](#5-agrupación-de-vehículos-por-flota)
6. [Estados de Órdenes y su Significado](#6-estados-de-órdenes-y-su-significado)
7. [Tipos de Disponibilidad](#7-tipos-de-disponibilidad)
8. [Fórmulas de Cálculo](#8-fórmulas-de-cálculo)
9. [Cálculo de KPIs: Medidores (Gauges)](#9-cálculo-de-kpis-medidores-gauges)
10. [Cálculo de KPIs: Tarjetas](#10-cálculo-de-kpis-tarjetas)
11. [Tendencias Históricas](#11-tendencias-históricas)
12. [Disponibilidad por Flota (Barras)](#12-disponibilidad-por-flota-barras)
13. [Monitor de Órdenes Activas](#13-monitor-de-órdenes-activas)
14. [Endpoints de la API Interna](#14-endpoints-de-la-api-interna)
15. [Sincronización Automática en Segundo Plano](#15-sincronización-automática-en-segundo-plano)
16. [Sistema de Caché](#16-sistema-de-caché)
17. [Umbrales y Clasificación de Estado](#17-umbrales-y-clasificación-de-estado)

---

## 1. Variables de Entorno Necesarias

Estas variables se configuran en el archivo `.env` en la raíz del proyecto. Solo `CLOUDFLEET_API_KEY` es obligatoria; el resto tienen valores por defecto.

### Obligatorias

| Variable | Descripción | Ejemplo |
|---|---|---|
| `CLOUDFLEET_API_KEY` | API Key de autenticación de CloudFleet. Sin esta variable, la aplicación no arranca. | `2mEI1Jn.gHC1...` |

### Conexión y Red

| Variable | Default | Descripción |
|---|---|---|
| `CLOUDFLEET_API_URL` | `https://fleet.cloudfleet.com/api/v1` | URL base de la API. Nota: el extractor usa la URL hardcoded `https://fleet.cloudfleet.com/api/v1`, no esta variable. |
| `HOST` | `0.0.0.0` | Interfaz de red donde escucha Flask |
| `PORT` | `5000` | Puerto de la aplicación (el `.env` actual usa `8000`) |

### KPIs y Umbrales

| Variable | Default | Descripción |
|---|---|---|
| `AVAILABILITY_TARGET` | `94.0` | Target (%) de disponibilidad mecánica. Referencia del gauge. |
| `AVAILABILITY_WARNING_THRESHOLD` | `94.5` | Umbral de advertencia para disponibilidad mecánica |
| `AVAILABILITY_CRITICAL_THRESHOLD` | `93.9` | Umbral crítico para disponibilidad mecánica |
| `PROJECTS_AVAILABILITY_TARGET` | `96.0` | Target (%) de disponibilidad de proyectos |
| `FLEET_NORMAL_THRESHOLD` | `97.0` | Umbral "verde" para barras de disponibilidad por flota |
| `FLEET_WARNING_THRESHOLD` | `96.0` | Umbral "amarillo" para barras de disponibilidad por flota |
| `MTTR_TARGET_HOURS` | `24.0` | Target MTTR en horas (promedio de reparación) |
| `MTTR_WARNING_HOURS` | `48.0` | Umbral de advertencia MTTR |
| `MTTR_CRITICAL_HOURS` | `72.0` | Umbral crítico MTTR |
| `MTBF_TARGET_HOURS` | `500.0` | Target MTBF en horas (tiempo entre fallas) |
| `MTBF_WARNING_HOURS` | `300.0` | Umbral de advertencia MTBF |
| `MTBF_CRITICAL_HOURS` | `200.0` | Umbral crítico MTBF |
| `ABOUT_TO_EXPIRE_HOURS` | `24` | Horas de anticipación para marcar una orden como "Por Vencer" |

### Aplicación y Sistema

| Variable | Default | Descripción |
|---|---|---|
| `FLASK_ENV` | `production` | Entorno Flask: `development`, `production` |
| `DEBUG` | `false` | Modo debug de Flask |
| `SECRET_KEY` | `dashboard-flota-secret-key-2024` | Clave secreta de Flask |
| `VIDEO_WALL_MODE` | `false` | Activa modo video wall (afecta configuración de caché y logs) |
| `AUTO_REFRESH_INTERVAL` | `30` | Segundos entre refresh automático del frontend |
| `ENABLE_CACHE` | `true` | Activa/desactiva el caché interno |
| `CACHE_TTL_MINUTES` | `15` | TTL general del caché en minutos |
| `LOCAL_TIMEZONE` | `America/Bogota` | Zona horaria local (referencia; la conversión real usa UTC−5 hardcoded) |
| `UTC_OFFSET_HOURS` | `-5` | Offset UTC (referencia; la conversión real usa `-5` hardcoded) |
| `LOG_LEVEL` | `INFO` | Nivel de logging: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ENABLE_SCHEDULER` | `true` | Activa el scheduler de sincronización automática |

### Configuración Actual del `.env`

```ini
CLOUDFLEET_API_URL=https://api.cloudfleet.com/v1
CLOUDFLEET_API_KEY=2mEI1Jn.gHC1SztAbD4qrLwFOoxbSsfAvYbMeoyMK
VIDEO_WALL_MODE=true
AUTO_REFRESH_INTERVAL=30
AVAILABILITY_TARGET=94.0
PROJECTS_AVAILABILITY_TARGET=96.0
FLEET_NORMAL_THRESHOLD=97.0
FLEET_WARNING_THRESHOLD=96.0
PORT=8000
LOCAL_TIMEZONE=America/Bogota
UTC_OFFSET_HOURS=-5
```

---

## 2. Arquitectura y Flujo de Datos

```
┌─────────────────────────────────────────────────────────────┐
│                    CloudFleet API v1                        │
│  GET /vehicles  ·  GET /work-orders/ (paginado por fechas) │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS · Bearer Token
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              scripts/data_extractor.py                      │
│  CloudFleetExtractor                                        │
│  · Carga inicial: bloques de 5 meses desde 2021-02-21       │
│  · Actualización incremental: desde ultima_fecha_consultada │
│  · Deduplicación por updatedAt más reciente                 │
│  · Pausa: 2s entre requests · 10s en rate limit (HTTP 429)  │
└──────────────┬──────────────────────────┬───────────────────┘
               │                          │
               ▼                          ▼
    data/vehiculos.json        data/resultados_ordenes.json
    data/estado_ordenes.json
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│              models/data_processor.py                       │
│  DataProcessor · carga archivos JSON con caché interno      │
│  TTL: 5 min órdenes · 30 min vehículos (aprox.)            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            models/availability.py                           │
│  AvailabilityCalculator                                     │
│  · Agrupa vehículos por flota (costCenter.name)             │
│  · calculate_monthly_consumption_availability()             │
│  · calculate_availability_by_hours()                        │
│  · _calculate_order_unavailable_hours()  (precisión: seg)   │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│              models/kpi_calculator.py                       │
│  KPICalculator                                              │
│  · calculate_gauges_data()   → 4 medidores                  │
│  · calculate_kpi_cards()     → 4 tarjetas                   │
│  · calculate_mttr()          → MTTR anual                   │
│  · calculate_mtbf()          → MTBF anual                   │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│                       app.py (Flask)                        │
│  /api/kpis/gauges        /api/kpis/cards                   │
│  /api/trends/availability /api/fleet/availability-bars      │
│  /api/orders/active       /api/health                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Integración con la API de CloudFleet

**URL base (hardcoded en el extractor):** `https://fleet.cloudfleet.com/api/v1`  
**Autenticación:** Bearer Token en header `Authorization`

```python
headers = {
    'Authorization': f'Bearer {CLOUDFLEET_API_KEY}',
    'Content-Type': 'application/json'
}
```

### Endpoint 1: Obtener Vehículos

```
GET https://fleet.cloudfleet.com/api/v1/vehicles
```

**Paginación:** El encabezado de respuesta `X-NextPage` contiene la URL completa de la siguiente página. El ciclo continúa mientras `X-NextPage` esté presente.

**Respuesta:** Array JSON de vehículos.

```json
[
  {
    "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "code": "VH-001",
    "costCenter": {
      "id": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy",
      "name": "Viacargo"
    },
    "model": "Volvo FH16",
    "year": 2020
  }
]
```

**Campos relevantes para el sistema:**

| Campo | Tipo | Uso |
|---|---|---|
| `code` | string | Identificador único del vehículo. Clave de cruce con órdenes. |
| `id` | UUID | Identificador interno de CloudFleet |
| `costCenter.name` | string | Determina a qué flota pertenece el vehículo. Si empieza por `Bav-`, se agrupa como `Bavaria`. |

### Endpoint 2: Obtener Órdenes de Trabajo

```
GET https://fleet.cloudfleet.com/api/v1/work-orders/
```

**Parámetros de query (solo en la primera página del rango):**

| Parámetro | Tipo | Descripción | Ejemplo |
|---|---|---|---|
| `updatedAtFrom` | ISO 8601 datetime | Fecha inicio del rango (basada en `updatedAt` de la orden) | `2021-02-21T00:00:00` |
| `updatedAtTo` | ISO 8601 datetime | Fecha fin del rango | `2025-05-25T23:59:59.999999` |

**Paginación:** Igual que vehículos — header `X-NextPage` en la respuesta.

**Manejo especial HTTP 404:** La API puede responder `404 Not Found` cuando no hay órdenes en el rango consultado. El sistema lo trata como una respuesta vacía válida, no como un error.

**Respuesta:** Array JSON de órdenes de trabajo.

```json
[
  {
    "number": "ORD-12345",
    "vehicleCode": "VH-001",
    "type": "Programado",
    "status": "opened",
    "reason": "Mantenimiento preventivo 30.000 km",
    "priority": "normal",
    "affectsVehicleAvailability": true,
    "maintenanceLabels": ["Backup en préstamo"],

    "createdAt":                "2025-01-15T10:30:00.0000000Z",
    "startDate":                "2025-01-15T14:00:00.0000000Z",
    "workshopDate":             "2025-01-15T14:00:00.0000000Z",
    "estimatedFinishDate":      "2025-01-16T14:00:00.0000000Z",
    "technicalCompletionDate":  "2025-01-16T10:00:00.0000000Z",
    "finalCompletionDate":      "2025-01-16T14:30:00.0000000Z",
    "updatedAt":                "2025-01-16T14:30:00.0000000Z"
  }
]
```

**Campos críticos para los cálculos:**

| Campo | Tipo | Rol en el cálculo |
|---|---|---|
| `vehicleCode` | string | Clave de cruce con el maestro de vehículos |
| `status` | string | Determina si la orden está activa o cerrada |
| `type` | string | `Programado` / `Varado` / `No Programado`. MTBF considera todo lo que **no** sea `Programado` como falla. |
| `affectsVehicleAvailability` | boolean | Si es `true`, la orden entra en el cálculo de **disponibilidad de proyectos**. |
| `startDate` | UTC datetime | Inicio del período de no disponibilidad. Si es `null`, se usa `workshopDate`. |
| `workshopDate` | UTC datetime | Fallback de `startDate`. |
| `technicalCompletionDate` | UTC datetime | Fin del período de no disponibilidad **en disponibilidad de proyectos**. |
| `finalCompletionDate` | UTC datetime | Fin del período de no disponibilidad **en disponibilidad mecánica**. |
| `estimatedFinishDate` | UTC datetime | Usado para determinar si una orden está "excedida" o "por vencer". |
| `updatedAt` | UTC datetime | Clave para deduplicación: se conserva la versión con `updatedAt` más reciente. |
| `maintenanceLabels` | array | Etiquetas como `"Backup en préstamo"`. Afectan la tarjeta KPI de backups. |

**Estrategia de sincronización de órdenes:**

```
¿Existe data/resultados_ordenes.json?
    NO → Carga inicial: consulta bloques de 5 meses desde 2021-02-21 hasta hoy
    SÍ → Carga incremental: consulta desde ultima_fecha_consultada (en estado_ordenes.json)
```

**Límites de la API:**
- `request_delay = 2 s` — pausa entre cada request exitoso
- `rate_limit_delay = 10 s` — pausa cuando la API responde HTTP 429
- `max_retries = 3` — reintentos máximos antes de abortar
- Bloques de 150 días (≈5 meses) por consulta

---

## 4. Estructura de los Datos

### `data/vehiculos.json`

Array JSON plano. Se actualiza al arrancar la aplicación y cada domingo a las 03:00.

```json
[
  {
    "id": "uuid",
    "code": "VH-001",
    "costCenter": { "id": "uuid", "name": "Viacargo" },
    "model": "Volvo FH16",
    "year": 2020
  }
]
```

### `data/resultados_ordenes.json`

Array JSON ordenado por `updatedAt` descendente. Se actualiza cada 10 minutos de forma incremental.

### `data/estado_ordenes.json`

Archivo de control de sincronización incremental.

```json
{
  "ultima_fecha_consultada": "2025-05-25T14:30:00.123456"
}
```

---

## 5. Agrupación de Vehículos por Flota

Los vehículos se agrupan usando el campo `costCenter.name` de cada vehículo.

**Regla especial Bavaria:** cualquier vehículo cuyo `costCenter.name` empiece por `Bav-` (p. ej. `Bav-001`, `Bav-002`) se agrupa bajo la flota `Bavaria`.

```python
# models/availability.py — get_fleet_vehicle_mapping()
if cost_center_name.startswith('Bav-'):
    fleet_name = 'Bavaria'
else:
    fleet_name = cost_center_name
```

**Resultado:** Un diccionario `{nombre_flota → [lista de vehículos]}`.

**Orden de visualización en el dashboard** (prioridad fija, definida en `app.py`):

```
1. Viacargo
2. Vigia
3. Rayogas
4. Rivercol
5. Concretos Alión
6. Bavaria
7. GranKarga
8. Ruta 40
9. El Condor
10. Back-Up
(Flotas no listadas aparecen al final, ordenadas por % de disponibilidad)
```

---

## 6. Estados de Órdenes y su Significado

### Estados Activos — Vehículo **NO disponible**

| Estado | Descripción |
|---|---|
| `opened` | Orden abierta y activa |
| `assigned` | Asignada a técnico |
| `in_progress` | En progreso |
| `pending` | Pendiente de materiales o aprobación |
| `working` | Técnico trabajando en el vehículo |
| `onTechnicalCompletion` | Completada técnicamente, pendiente de cierre administrativo |

### Estados Inactivos — Vehículo **disponible**

| Estado | Descripción |
|---|---|
| `closed` | Orden cerrada y completada |
| `cancelled` | Cancelada |
| `voided` | Anulada |
| `completed` | Completada |
| `finished` | Finalizada |

---

## 7. Tipos de Disponibilidad

El sistema calcula **dos tipos independientes** de disponibilidad:

### 7.1 Disponibilidad Mecánica

Mide si el vehículo está operativo desde el punto de vista de mantenimiento.

**¿Cuándo un vehículo NO está disponible mecánicamente?**
- Tiene **cualquier orden de trabajo** con estado activo (`opened`, `assigned`, `in_progress`, `pending`, `working`, `onTechnicalCompletion`)
- Y la orden ya comenzó (`startDate ≤ fecha evaluada`)

**Período de no disponibilidad por orden:**
- Inicio: `startDate` (o `workshopDate` si `startDate` es nulo)
- Fin: `finalCompletionDate` → si no existe, `technicalCompletionDate` → si la orden sigue activa, se usa el momento exacto del cálculo (`end_datetime`)

### 7.2 Disponibilidad de Proyectos

Mide si el vehículo está disponible para cumplir compromisos comerciales con clientes.

**¿Cuándo un vehículo NO está disponible para proyectos?**
- La orden tiene `affectsVehicleAvailability = true` (campo explícito en la orden)
- Y la fecha evaluada está dentro del intervalo `[startDate, technicalCompletionDate]`

**Período de no disponibilidad por orden:**
- Inicio: `startDate` (o `workshopDate` si `startDate` es nulo)
- Fin: `technicalCompletionDate` → si no existe y la orden sigue activa, se usa el momento exacto del cálculo (`end_datetime`)

**Diferencia clave respecto a mecánica:**
- Solo órdenes con `affectsVehicleAvailability = true`
- El período termina en `technicalCompletionDate`, NO en `finalCompletionDate`
- Esto significa que desde que el técnico termina el trabajo (completación técnica), el vehículo ya se considera disponible para proyectos, aunque el cierre administrativo esté pendiente

---

## 8. Fórmulas de Cálculo

### 8.1 Conversión de Fechas UTC a Hora Local

Todas las fechas de la API vienen en UTC. El sistema convierte a Colombia (UTC−5) restando 5 horas. El formato recibido es:

```
"2022-11-29T20:00:00.0000000Z"
```

```python
# models/availability.py — _convert_utc_to_local()
clean_str = utc_str.replace('Z', '')
# Truncar microsegundos a 6 dígitos (límite de Python)
datetime_part, micro = clean_str.split('.')
micro = micro[:6].ljust(6, '0')
utc_dt = datetime.fromisoformat(f"{datetime_part}.{micro}")
local_dt = utc_dt - timedelta(hours=5)
```

**Nota:** `kpi_calculator.py` usa `pandas.to_datetime(...).tz_convert('America/Bogota').replace(tzinfo=None)` para la misma conversión.

### 8.2 Horas No Disponibles por Orden (Fórmula de Intersección)

Para cada orden relevante, se calcula la intersección entre el período de la orden y el período de cálculo:

```
Variables:
  order_start_local  = startDate convertido a UTC−5
  order_end_local    = fecha de fin según tipo (ver sección 7)
                       Si la orden sigue activa: order_end_local = end_datetime (momento del cálculo)
  period_start       = datetime.combine(start_date, datetime.min.time())  → inicio del período a las 00:00:00
  period_end         = end_datetime  → momento exacto del cálculo (p. ej. 2025-05-25 14:32:17.483)

Intersección:
  effective_start = max(order_start_local, period_start)
  effective_end   = min(order_end_local,   period_end)

Condición:
  Si effective_start >= effective_end → horas = 0.0
  Si no:
    horas_no_disponibles = (effective_end - effective_start).total_seconds() / 3600.0
```

Esta fórmula garantiza que solo se cuenten las horas que caen **dentro del período de análisis**, sin importar si la orden comenzó antes o termina después del período.

### 8.3 Disponibilidad por Horas — `calculate_availability_by_hours()`

Usada en las tendencias históricas (meses anteriores completos).

```
Variables:
  N       = total de vehículos de la flota (o todas las flotas)
  D       = días del período = (end_date - start_date).days + 1
  H_total = N × D × 24   → horas teóricas que todos deberían estar disponibles

  H_no_disp = Σ horas_no_disponibles de cada orden (fórmula 8.2)
  H_disp    = H_total − H_no_disp

Fórmula:
  Disponibilidad (%) = (1 − H_no_disp / H_total) × 100
                     = (H_disp / H_total) × 100
```

### 8.4 Disponibilidad Mensual de Consumo — `calculate_monthly_consumption_availability()`

Usada en los **gauges del dashboard** y en las **barras por flota**. Muestra el avance del mes en curso.

```
Variables:
  N          = total de vehículos de la flota (o todas las flotas)
  D_mes      = días totales del mes de referencia (usando calendar.monthrange)
               Ejemplo: mayo 2025 → D_mes = 31
  H_total    = N × D_mes × 24   → horas teóricas del mes COMPLETO
               Este denominador NO cambia aunque hoy sea día 15.

  start_date = primer día del mes actual (p. ej. 2025-05-01)
  end_datetime = datetime.now() → momento exacto del cálculo

  H_no_disp  = Σ horas_no_disponibles desde start_date hasta end_datetime (fórmula 8.2)
  H_disp     = H_total − H_no_disp

Fórmula:
  Disponibilidad (%) = (H_disp / H_total) × 100
```

**¿Por qué el denominador es el mes completo?**
Esto es intencional. Si el mes tiene 31 días × 50 vehículos × 24 h = 37.200 h teóricas, y hoy es el día 15, los vehículos solo han tenido 15 días de oportunidad de estar disponibles. Esta lógica de "consumo" penaliza las fallas ocurridas pero el porcentaje naturalmente tiende a subir conforme avanza el mes sin nuevas fallas, reflejando una mejora en disponibilidad acumulada.

### 8.5 Ejemplo Numérico Completo

Supongamos:
- Flota con 10 vehículos
- Mes: mayo 2025 (31 días)
- Fecha del cálculo: 25 de mayo a las 14:32:17
- Una orden activa que inició el 10-mayo a las 08:00 (UTC−5) y sigue abierta

```
H_total = 10 vehículos × 31 días × 24 h = 7.440 h

Orden activa:
  order_start_local  = 2025-05-10 08:00:00
  order_end_local    = 2025-05-25 14:32:17  (end_datetime, orden sigue activa)
  period_start       = 2025-05-01 00:00:00
  period_end         = 2025-05-25 14:32:17

  effective_start = max(10-mayo 08:00, 1-mayo 00:00) = 10-mayo 08:00
  effective_end   = min(25-mayo 14:32:17, 25-mayo 14:32:17) = 25-mayo 14:32:17

  Duración = del 10-mayo 08:00 al 25-mayo 14:32:17
           = 15 días + 6 horas + 32 min + 17 seg
           = (15 × 24) + 6 + 32/60 + 17/3600
           ≈ 366.54 h

H_no_disp = 366.54 h
H_disp    = 7.440 − 366.54 = 7.073.46 h
Disponibilidad = (7.073,46 / 7.440) × 100 ≈ 95.07%
```

---

## 9. Cálculo de KPIs: Medidores (Gauges)

Los 4 medidores se calculan cada vez que expira el caché interno (configurable, por defecto `GAUGE_CACHE_SECONDS`). El código está en `models/kpi_calculator.py — calculate_gauges_data()`.

```python
# Período siempre es el mes actual
start_date_avail = datetime.now().date().replace(day=1)  # Ej: 2025-05-01
end_datetime_avail = datetime.now()                       # Momento exacto
```

### Gauge 1: Disponibilidad Mecánica

```
Método: calculate_monthly_consumption_availability(
    start_date = primer día del mes,
    end_datetime = ahora,
    fleet_name = None,     # Todas las flotas
    availability_type = 'mechanical'
)
Target: AVAILABILITY_TARGET = 94.0%
Estado: good si ≥ 94.0%, warning si ≥ 94.5% (ver sección 17)
```

### Gauge 2: Disponibilidad de Proyectos

```
Método: calculate_monthly_consumption_availability(
    start_date = primer día del mes,
    end_datetime = ahora,
    fleet_name = None,
    availability_type = 'project'
)
Target: PROJECTS_AVAILABILITY_TARGET = 96.0%
Estado: good si ≥ 96.0%, warning si ≥ 91.0%, critical si < 91.0%
```

### Gauge 3: MTTR (Mean Time To Repair)

Promedio de horas que tarda en repararse un vehículo, basado en órdenes completadas en el **año actual**.

```
Filtro: order.finalCompletionDate.year == año_actual
        Y order.startDate existe

Por cada orden válida:
  duracion_h = (finalCompletionDate_local − startDate_local).total_seconds() / 3600
  Solo incluir si duracion_h > 0

Fórmula:
  MTTR = media(lista de duracion_h)  → en horas

Ejemplo:
  Orden A: 8 h   Orden B: 24 h   Orden C: 12 h
  MTTR = (8 + 24 + 12) / 3 = 14.67 h

Target: MTTR_TARGET_HOURS = 24 h
Estado: good si ≤ 24 h · warning si ≤ 48 h · critical si > 48 h
```

### Gauge 4: MTBF (Mean Time Between Failures)

Tiempo promedio entre fallas consecutivas de un mismo vehículo, en el **año actual**.

```
Definición de "falla": cualquier orden cuyo type ≠ 'Programado'
                       con startDate en el año actual

Paso 1 — Agrupar fallas por vehículo:
  failures_by_vehicle = {
    'VH-001': [2025-01-10 08:00, 2025-02-15 09:30, 2025-04-01 07:00],
    'VH-002': [2025-01-20 14:00, 2025-03-10 11:00],
    ...
  }

Paso 2 — Calcular intervalos entre fallas consecutivas (por vehículo):
  VH-001:
    intervalo_1 = (15-feb − 10-ene).total_seconds() / 3600 = 876 h
    intervalo_2 = (01-abr − 15-feb).total_seconds() / 3600 = 1032 h
  VH-002:
    intervalo_1 = (10-mar − 20-ene).total_seconds() / 3600 = 1224 h

  Lista total intervalos = [876, 1032, 1224]

Paso 3 — Promediar:
  MTBF = media([876, 1032, 1224]) = 1044 h

Solo se consideran vehículos con 2 o más fallas en el año.

Target: MTBF_TARGET_HOURS = 500 h
Estado: good si ≥ 500 h · warning si ≥ 300 h · critical si < 300 h
```

---

## 10. Cálculo de KPIs: Tarjetas

Las 4 tarjetas se calculan en tiempo real (sin caché) en `kpi_calculator.py — calculate_kpi_cards()`.

### Tarjeta 1: Vehículos en Taller

```
Condición: order.status == 'opened'
           AND order.technicalCompletionDate es null/vacío
           AND order.vehicleCode existe

Resultado: conteo de vehicleCodes únicos (set)
```

Un mismo vehículo con múltiples órdenes abiertas cuenta una sola vez.

### Tarjeta 2: Órdenes Abiertas

```
Condición: order.status IN {'opened', 'onTechnicalCompletion'}
           AND order.number existe

Resultado: conteo de números de orden únicos (set)
```

### Tarjeta 3: Órdenes Excedidas

```
Condición: order.status IN {'opened', 'onTechnicalCompletion'}
           AND order.number existe
           AND order.estimatedFinishDate existe
           AND parse_utc_to_local(estimatedFinishDate) < datetime.now()

Resultado: conteo de números de orden únicos (set)
```

### Tarjeta 4: Backup en Préstamo

```
Condición: order.finalCompletionDate es null/vacío
           AND order.number existe
           AND 'Backup en préstamo' IN order.maintenanceLabels

Resultado: conteo de números de orden únicos (set)
```

---

## 11. Tendencias Históricas

Endpoint: `GET /api/trends/availability?months=4`

Calcula disponibilidad **mecánica y de proyectos** para los últimos N meses (por defecto 4, mínimo 3, máximo 12).

**Lógica de fechas:**
- Los meses anteriores al actual se calculan como períodos **completos** (del día 1 al último día del mes)
- El mes actual se calcula desde el día 1 hasta el **día de hoy** (período parcial)

**Método usado:** `calculate_availability_by_hours()` (fórmula 8.3, no la de consumo mensual)

**Ejemplo con 4 meses (hoy = 25-mayo-2025):**

| Posición | Período | Denominador | Etiqueta |
|---|---|---|---|
| Mes 1 | 2025-02-01 → 2025-02-28 | 28 días × N vehículos × 24 h | Feb 2025 |
| Mes 2 | 2025-03-01 → 2025-03-31 | 31 días × N vehículos × 24 h | Mar 2025 |
| Mes 3 | 2025-04-01 → 2025-04-30 | 30 días × N vehículos × 24 h | Abr 2025 |
| Mes 4 | 2025-05-01 → 2025-05-25 | 25 días × N vehículos × 24 h | May 2025 (parcial) |

**Respuesta JSON:**

```json
{
  "success": true,
  "data": {
    "period": {
      "months": 4,
      "start_date": "2025-02-01",
      "end_date": "2025-05-25"
    },
    "mechanical_availability": [96.2, 95.8, 94.1, 95.3],
    "projects_availability":   [97.5, 97.1, 96.4, 96.8],
    "labels": ["Feb 2025", "Mar 2025", "Abr 2025", "May 2025 (parcial)"]
  },
  "timestamp": "2025-05-25T14:32:17.483"
}
```

---

## 12. Disponibilidad por Flota (Barras)

Endpoint: `GET /api/fleet/availability-bars`

Calcula **disponibilidad de proyectos** del mes actual para cada flota usando la lógica de consumo mensual (fórmula 8.4).

```python
now = datetime.now()
start_date = now.date().replace(day=1)  # Primer día del mes
end_datetime = now

for cada flota:
    resultado = calculate_monthly_consumption_availability(
        start_date, end_datetime, fleet_name, 'project'
    )
```

**Respuesta por flota:**

```json
{
  "fleet_name": "Viacargo",
  "availability_percentage": 97.3,
  "status": "good",
  "vehicle_count": 15
}
```

**Clasificación de estado (umbrales por flota):**

| Estado | Condición |
|---|---|
| `good` | `availability_percentage >= 97.0` (FLEET_NORMAL_THRESHOLD) |
| `warning` | `96.0 <= availability_percentage < 97.0` (FLEET_WARNING_THRESHOLD) |
| `critical` | `availability_percentage < 96.0` |

---

## 13. Monitor de Órdenes Activas

Endpoint: `GET /api/orders/active`

Filtra todas las órdenes con `status IN {'opened', 'onTechnicalCompletion'}` y las enriquece con:

**Estado temporal de cada orden:**

| Indicador | Condición |
|---|---|
| `on_time` | La orden no ha excedido su fecha estimada |
| `about_to_expire` | `(estimatedFinishDate - ahora) < ABOUT_TO_EXPIRE_HOURS` (24 h por defecto) |
| `overdue` | `estimatedFinishDate < ahora` |
| `pending_closure` | `technicalCompletionDate` existe Y `finalCompletionDate` no existe |

**Orden de presentación** (sort por defecto):

1. Órdenes **con etiquetas** (`maintenanceLabels` no vacío) primero
2. Dentro de cada grupo: por `days_elapsed` descendente (más antiguas primero)

**Respuesta JSON:**

```json
{
  "success": true,
  "data": {
    "orders": [
      {
        "order_number": "ORD-12345",
        "vehicle_code": "VH-001",
        "fleet": "Viacargo",
        "type": "Programado",
        "status": "opened",
        "maintenance_labels": ["Backup en préstamo"],
        "labels_for_sort": "Backup en préstamo",
        "days_elapsed": 12,
        "status_indicator": "overdue",
        "time_status_text": "Excedido"
      }
    ],
    "summary": {
      "total_active": 8,
      "on_time": 3,
      "about_to_expire": 1,
      "overdue": 2,
      "pending_closure": 2
    }
  }
}
```

---

## 14. Endpoints de la API Interna

| Método | Ruta | Descripción | Caché TTL |
|---|---|---|---|
| `GET` | `/` | Dashboard principal (HTML) | — |
| `GET` | `/api/kpis/gauges` | 4 medidores: Disp. Mecánica, Disp. Proyectos, MTTR, MTBF | 5 min |
| `GET` | `/api/kpis/cards` | 4 tarjetas: Vehículos taller, Órdenes abiertas, Excedidas, Backups | 3 min |
| `GET` | `/api/trends/availability?months=4` | Tendencias históricas de disponibilidad | 30 min |
| `GET` | `/api/fleet/availability-bars` | Barras de disponibilidad de proyectos por flota | 10 min |
| `GET` | `/api/orders/active` | Tabla de órdenes activas enriquecida | 2 min |
| `GET` | `/api/orders/detail/<número>` | Detalle completo de una orden específica | 5 min |
| `GET` | `/api/health` | Estado del sistema y resumen de datos | — |
| `POST` | `/api/config/refresh` | Limpia el caché interno del DataProcessor | — |

**Estructura estándar de respuesta:**

```json
{
  "success": true,
  "data": { ... },
  "timestamp": "2025-05-25T14:32:17.483"
}
```

**En caso de error:**

```json
{
  "success": false,
  "error": "Descripción del error",
  "data": null
}
```

El endpoint `/api/kpis/gauges` devuelve HTTP 503 en caso de error (no 500) para indicar un fallo temporal en el cálculo.

---

## 15. Sincronización Automática en Segundo Plano

Implementada con APScheduler en `app.py`.

| Tarea | Trigger | Frecuencia | Acción |
|---|---|---|---|
| `update_orders_task` | Interval | Cada **10 minutos** | Llama a `CloudFleetExtractor().update_orders()` → actualización incremental |
| `update_vehicles_task` | Cron | Domingos a las **03:00** | Llama a `CloudFleetExtractor().extract_all_vehicles()` |

**Al arrancar la aplicación:**
1. Siempre actualiza el maestro de vehículos (sincronía)
2. Si no existe `resultados_ordenes.json`: ejecuta la carga inicial completa (puede tardar varios minutos)
3. Si el archivo ya existe: el scheduler incremental se encarga desde ahí

El scheduler solo se inicia en el proceso principal de Flask (no en el proceso de recarga del modo debug, para evitar duplicados).

---

## 16. Sistema de Caché

El sistema tiene **dos niveles de caché**:

### Nivel 1: Caché interno del KPICalculator (en memoria)

Solo para el cálculo de gauges. Variable de instancia `self._cache`.

```python
self.cache_duration = timedelta(seconds=getattr(config, 'GAUGE_CACHE_SECONDS', 30))
```

Si el resultado de `calculate_gauges_data()` tiene menos de `GAUGE_CACHE_SECONDS` segundos, se devuelve el valor cacheado sin recalcular.

### Nivel 2: TTL por tipo de endpoint (configuración)

Definidos en `config.py — CACHE_TTL_CONFIG`. La implementación real del caché de nivel 2 está en `DataProcessor`.

| Tipo de dato | TTL |
|---|---|
| KPIs gauges | 5 minutos |
| KPIs tarjetas | 3 minutos |
| Tendencias históricas | 30 minutos |
| Disponibilidad por flota | 10 minutos |
| Órdenes activas | 2 minutos |
| Detalle de orden | 5 minutos |
| Datos de vehículos | 60 minutos |
| MTTR / MTBF | 20 minutos |

---

## 17. Umbrales y Clasificación de Estado

### Disponibilidad Mecánica (gauges)

| Estado | Condición |
|---|---|
| `good` | `valor >= 94.0` (AVAILABILITY_TARGET) |
| `warning` | `valor >= 94.5` (AVAILABILITY_WARNING_THRESHOLD) |
| `critical` | `valor < 93.9` (AVAILABILITY_CRITICAL_THRESHOLD) |

**Nota:** La lógica actual en `config.get_availability_status()` devuelve `good` si `>= target`, `warning` si `>= warning_threshold`, y `critical` en otro caso. Los umbrales de warning y target se solapan; revisar si la lógica de negocio requiere ajuste.

### Disponibilidad de Proyectos (gauges)

```python
# kpi_calculator.py — _get_projects_status()
target = 96.0
good     → valor >= 96.0
warning  → valor >= 91.0  (target − 5)
critical → valor < 91.0
```

### MTTR

| Estado | Condición |
|---|---|
| `good` | `mttr <= 24 h` |
| `warning` | `24 < mttr <= 48 h` |
| `critical` | `mttr > 48 h` |

### MTBF

| Estado | Condición |
|---|---|
| `good` | `mtbf >= 500 h` |
| `warning` | `300 h <= mtbf < 500 h` |
| `critical` | `mtbf < 300 h` |

### Disponibilidad por Flota (barras)

| Estado | Condición |
|---|---|
| `good` | `valor >= 97.0` (FLEET_NORMAL_THRESHOLD) |
| `warning` | `96.0 <= valor < 97.0` (FLEET_WARNING_THRESHOLD) |
| `critical` | `valor < 96.0` |

### Órdenes (estado temporal)

| Indicador | Texto UI | Condición |
|---|---|---|
| `on_time` | En Tiempo | No excedida y no por vencer |
| `about_to_expire` | Por Vencer | `(estimatedFinishDate - ahora) < 24 h` |
| `overdue` | Excedido | `estimatedFinishDate < ahora` |
| `pending_closure` | Pendiente Cierre | `technicalCompletionDate` existe y `finalCompletionDate` no existe |

---

## Resumen de Fórmulas

| Métrica | Fórmula | Período | Denominador |
|---|---|---|---|
| **Disponibilidad Mecánica (gauge)** | `(H_total_mes − H_no_disp_acum) / H_total_mes × 100` | Mes actual, desde el día 1 hasta ahora | `N vehículos × días_del_mes × 24 h` (mes completo) |
| **Disponibilidad Proyectos (gauge)** | Ídem, solo órdenes con `affectsVehicleAvailability=true` | Mes actual | `N vehículos × días_del_mes × 24 h` (mes completo) |
| **Disponibilidad Histórica (tendencias)** | `(1 − H_no_disp / H_total) × 100` | Mes completo anterior | `N vehículos × días_del_período × 24 h` |
| **Disponibilidad por Flota (barras)** | Ídem gauge de proyectos, por flota individual | Mes actual | `N_flota × días_del_mes × 24 h` (mes completo) |
| **MTTR** | `media(finalCompletionDate − startDate)` | Año en curso | Por orden completada con `finalCompletionDate` en el año |
| **MTBF** | `media(intervalos entre fallas consecutivas del mismo vehículo)` | Año en curso | Por vehículo con ≥ 2 fallas del año |
| **Vehículos en taller** | `count(distinct vehicleCode)` | Tiempo real | Órdenes: `status='opened'` y sin `technicalCompletionDate` |
| **Órdenes abiertas** | `count(distinct number)` | Tiempo real | `status IN {opened, onTechnicalCompletion}` |
| **Órdenes excedidas** | `count(distinct number)` | Tiempo real | Abiertas con `estimatedFinishDate < ahora` |
| **Backup en préstamo** | `count(distinct number)` | Tiempo real | Sin `finalCompletionDate` y con etiqueta `'Backup en préstamo'` |

---

*Documento generado el 2026-05-25. Basado en el código fuente de la versión actual del proyecto.*
