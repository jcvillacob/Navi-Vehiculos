# Contrato de integración — Lo que recibe Portal Clientes

> Documento hermano: [plan-integracion-portal-clientes.md](plan-integracion-portal-clientes.md)
> (cambios necesarios en Navi Vehículos para producir esto).

Navi Vehículos es la **fuente de verdad** de clientes, databases Geotab,
credenciales, reglas y vehículos. Portal Clientes mantiene una **réplica local de
solo lectura**, sincronizada por HTTP. Portal Clientes nunca edita estos datos; si
algo está mal, se corrige en Navi Vehículos y la sync lo propaga.

---

## 1. Autenticación

- Header `X-API-Key: <clave>` en cada request.
- La clave se configura en Navi Vehículos (`INTEGRATION_API_KEYS`, admite varias para
  rotación) y en Portal Clientes como secreto de entorno (`NAVI_API_KEY`).
- Solo HTTPS. Respuestas `401` si falta/es inválida la clave; `503` si Navi
  Vehículos no tiene `INTEGRATION_API_KEYS` configurada.

> **Estado**: este contrato ya está implementado en Navi Vehículos
> (`app/api/routes/integration.py` + `app/services/integration_export.py`).

## 2. Endpoint principal

```
GET {NAVI_BASE_URL}/api/v1/integration/snapshot
    ?since=2026-06-10T00:00:00Z          (opcional — sync incremental)
    &include_credentials=true            (opcional — default false)
```

- Sin `since`: snapshot completo (full sync).
- Con `since`: solo registros con `updated_at > since` (sync incremental).
- `include_credentials=true`: incluye username/password de Geotab. Usarlo solo desde
  el job de sync del backend de Portal Clientes — nunca desde un browser.

### 2.1 Respuesta (shape completo)

```json
{
  "generated_at": "2026-06-11T14:30:00Z",
  "since": null,
  "customers": [
    {
      "id": 12,
      "name": "Transportes El Roble",
      "updated_at": "2026-06-01T10:00:00Z",
      "databases": [
        {
          "id": 31,
          "database_name": "el_roble_sa",
          "database_key": "el_roble_sa",
          "connection_type": "geotab",
          "access_url": null,
          "provider_config": { "plate_prefix": "TR" },
          "updated_at": "2026-06-01T10:00:00Z",
          "credentials": [
            {
              "id": 7,
              "username": "reportes@navitrans.com.co",
              "password": "********",
              "label": "cuenta reportes",
              "is_active": true,
              "updated_at": "2026-06-01T10:00:00Z"
            },
            {
              "id": 8,
              "username": "reportes2@navitrans.com.co",
              "password": "********",
              "label": "cuenta secundaria",
              "is_active": true,
              "updated_at": "2026-06-01T10:00:00Z"
            }
          ],
          "rules": [
            {
              "id": 101,
              "rule_id": "aB1cD2eF3gH",
              "name": "RPM > 2200",
              "category": "operacion",
              "motor_type": "ISD",
              "created_at": "2026-05-20T09:00:00Z"
            },
            {
              "id": 102,
              "rule_id": "aX9yZ8wV7uT",
              "name": "Frenada brusca",
              "category": "habito_seguro",
              "motor_type": null,
              "created_at": "2026-05-20T09:05:00Z"
            }
          ]
        }
      ]
    }
  ],
  "vehicles": [
    {
      "plate": "ABC123",
      "vin": "3HSDJAPR1KN123456",
      "geotab_device_id": "b1F2",
      "geotab_device_synced_at": "2026-06-10T03:00:00Z",
      "customer_id": 12,
      "customer_database_id": 31,
      "geotab_customer_database_id": 31,
      "geotab_customer_status": "found",
      "engine_number": "79123456",
      "technical_number": "D103005BX03",
      "motor_type": "ISD",
      "cpl": "4955",
      "marketing_model_name": "L9 370",
      "service_model_name": "L9 CM2450 L126B",
      "marca": "INTERNATIONAL",
      "linea": "PROSTAR",
      "ano_modelo": "2023",
      "tipo_combustible": "DIESEL",
      "nombre_vehiculo": "ABC123 - PROSTAR",
      "vocacional": false,
      "updated_at": "2026-06-10T03:00:00Z"
    }
  ]
}
```

Notas sobre los campos clave:

| Campo | Significado |
|---|---|
| `databases[].database_key` | **Clave de la db física de Geotab** (`database_name` en minúsculas). El nombre de database es único globalmente en MyGeotab, así que dos filas con el mismo `database_key` —aunque pertenezcan a clientes distintos— son **la misma database** y comparten reglas y credenciales. Ver §3.1. |
| `vehicles[].geotab_device_id` | **Código interno de Geotab** del vehículo **en la db del cliente** (`geotab_customer_database_id`). Es el id que Portal Clientes usa para llamar a la API de Geotab (`deviceSearch: {id: ...}`). |
| `vehicles[].geotab_customer_status` | `found` / `not_found` / `unknown` / `not_applicable`. Solo confiar en `geotab_device_id` cuando es `found`. |
| `databases[].provider_config.plate_prefix` | Algunos clientes nombran devices con prefijo (ej. device `TRABC123` para placa `ABC123`). Relevante si Portal Clientes busca por placa en vez de por id. |
| `rules[].category` | `operacion` (reglas de motor) o `habito_seguro`. `rule_id` es el id nativo de Geotab para consultar `ExceptionEvent` (`ruleSearch: {id: ...}`). |
| `rules[].motor_type` | **Familia de motor** a la que aplica la regla (`engine_name` del motor; ej. `ISD`, `X15`). Solo las reglas `operacion` agrupadas traen valor; las `habito_seguro` y las `operacion` aún sin grupo traen `null`. Ver §2.3. |
| `vehicles[].motor_type` | **Familia de motor** del vehículo (`engine_name` del motor cuyo `technical_number` coincide). `null` si el `technical_number` no está en el catálogo. Mismo vocabulario que `rules[].motor_type`, así que cruzan directo. Ver §2.3. |
| `vehicles[].marketing_model_name` | **Marketing Model Name** devuelto por QuickServe/Cummins para el motor del vehículo. Puede venir `null` si aún no se consultó o QuickServe no lo entregó. |
| `vehicles[].service_model_name` | **Service Model Name** devuelto por QuickServe/Cummins para el motor del vehículo. Puede venir `null` si aún no se consultó o QuickServe no lo entregó. |
| `vehicles[].vocacional` | **Booleano** del tipo de uso del vehículo: `true` = uso vocacional, `false` = transporte/comercial. Nunca `null` (default `false`). |
| `credentials[]` | Pool de credenciales de esa db. Portal Clientes debe **rotar** entre las activas (round-robin o LRU) para no saturar una sola sesión Geotab. |

### 2.2 Endpoints de conveniencia

```
GET /api/v1/integration/vehicles?since=...&limit=500&offset=0
GET /api/v1/integration/customers?include_credentials=true
```

Mismos shapes que las secciones correspondientes del snapshot, con paginación para
`vehicles` (flotas grandes; `limit` máx. 2000). La respuesta de `/vehicles` incluye
`limit`, `offset` y `count` además del array `vehicles`.

### 2.3 Reglas por tipo de motor (`motor_type`)

Una database física puede tener vehículos de **distinto motor**. Las reglas de
`operacion` (RPM, ralentí, etc.) dependen del motor: la de un `ISD` no aplica a un
`X15`. Las de `habito_seguro` (frenada/aceleración/velocidad) aplican a toda la db.

Por eso el snapshot anota `motor_type` (la **familia de motor**, `engine_name`) tanto
en cada regla como en cada vehículo. Navi Vehículos es la fuente de verdad: una regla
`operacion` queda asociada a un motor al meterla en un **grupo de motor**
(`geotab_rule_groups`), y el vehículo conoce su motor por su `technical_number`. Ambos
`motor_type` salen del mismo catálogo de motores, así que el vocabulario coincide.

Resolución en Portal Clientes (intersección por `database_key` + `motor_type`):

```sql
SELECT r.*
FROM vehicles v
JOIN geotab_databases d   ON d.id = v.database_id
JOIN geotab_databases sib ON sib.database_key = d.database_key   -- §3.1
JOIN geotab_rules r       ON r.database_id = sib.id
WHERE (
    r.category = 'habito_seguro'                                  -- toda la db
    OR (r.category = 'operacion' AND r.motor_type = v.motor_type) -- solo su motor
);
```

Casos huérfanos (cero reglas de `operacion`, en silencio): vehículos con
`motor_type = null`, o cuyo `motor_type` no tiene ninguna regla `operacion` agrupada
en su `database_key`.

---

## 3. Tablas sugeridas en Portal Clientes

Réplica con ids de origen (`source_id`) para upserts idempotentes, y `synced_at`
para detectar registros que dejaron de venir. PostgreSQL:

### 3.1 Regla de oro: la db física se identifica por `database_key`

Varios clientes pueden compartir la misma database de Geotab (ej. dos clientes
dentro de la db `navitrans`). En el snapshot, cada cliente trae su propia fila de
database, pero **las reglas y credenciales viven bajo la fila donde se
registraron** (sin duplicar). Por eso, en Portal Clientes:

- **Reglas de un vehículo** = reglas de **todas** las filas de database cuyo
  `database_key` coincide con el de la database asignada al vehículo — no solo
  las de su `database_id`.
- **Credenciales para consultar Geotab** = pool de todas las filas con el mismo
  `database_key` (rotar entre todas).

```sql
-- Reglas aplicables a un vehículo (operación y hábito seguro):
SELECT r.*
FROM vehicles v
JOIN geotab_databases d   ON d.id = v.database_id
JOIN geotab_databases sib ON sib.database_key = d.database_key
JOIN geotab_rules r       ON r.database_id = sib.id;
```

```sql
CREATE TABLE customers (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL UNIQUE,        -- customers.id en Navi Vehículos
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE geotab_databases (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL UNIQUE,        -- customer_databases.id
    customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    database_name TEXT NOT NULL,
    database_key TEXT NOT NULL,              -- db FISICA: filas con la misma key comparten reglas/credenciales
    connection_type TEXT NOT NULL,
    plate_prefix TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_geotab_databases_key ON geotab_databases (database_key);

CREATE TABLE geotab_credentials (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL UNIQUE,        -- customer_database_credentials.id
    database_id BIGINT NOT NULL REFERENCES geotab_databases(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,        -- cifrar al guardar (Fernet)
    label TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ NULL,           -- rotación local del pool
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE geotab_rules (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL UNIQUE,        -- geotab_rules.id
    database_id BIGINT NOT NULL REFERENCES geotab_databases(id) ON DELETE CASCADE,
    rule_id TEXT NOT NULL,                   -- id nativo Geotab
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('operacion', 'habito_seguro')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (database_id, rule_id, category)
);

CREATE TABLE vehicles (
    id BIGSERIAL PRIMARY KEY,
    plate VARCHAR(10) NOT NULL UNIQUE,       -- clave natural compartida
    vin TEXT NULL,
    geotab_device_id TEXT NULL,              -- código interno Geotab (db del cliente)
    customer_id BIGINT NULL REFERENCES customers(id),
    database_id BIGINT NULL REFERENCES geotab_databases(id),
    geotab_customer_status TEXT NOT NULL DEFAULT 'unknown',
    engine_number TEXT NULL,
    technical_number TEXT NULL,
    marketing_model_name TEXT NULL,
    service_model_name TEXT NULL,
    marca TEXT NULL,
    linea TEXT NULL,
    ano_modelo TEXT NULL,
    tipo_combustible TEXT NULL,
    nombre_vehiculo TEXT NULL,
    vocacional BOOLEAN NOT NULL DEFAULT FALSE, -- true = uso vocacional, false = transporte
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_vehicles_customer ON vehicles (customer_id);
CREATE INDEX idx_rules_database_category ON geotab_rules (database_id, category);
```

---

## 4. Cómo consumirlo (job de sync)

### 4.1 Estrategia

| Mecanismo | Frecuencia | Qué hace |
|---|---|---|
| **Sync incremental** | cada 15–60 min (cron/APScheduler) | `GET /snapshot?since=<último sync exitoso>` → upsert por `source_id` / `plate`. |
| **Full sync** | 1 vez al día | `GET /snapshot` sin `since` → upsert todo + marcar `is_active = false` a lo que **no** vino (detección de borrados). |

Guardar el watermark (`last_sync_at = generated_at` de la respuesta, no la hora
local) en una tabla `sync_state` de Portal Clientes.

### 4.2 Pseudocódigo del job (Python / httpx)

```python
def run_sync(full: bool = False):
    since = None if full else get_sync_state("snapshot")
    params = {"include_credentials": "true"}
    if since:
        params["since"] = since

    resp = httpx.get(
        f"{NAVI_BASE_URL}/api/v1/integration/snapshot",
        params=params,
        headers={"X-API-Key": NAVI_API_KEY},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    with db.transaction():
        seen = upsert_customers(data["customers"])      # upsert por source_id
        seen |= upsert_vehicles(data["vehicles"])       # upsert por plate
        if full:
            deactivate_missing(seen)                    # is_active = false
        set_sync_state("snapshot", data["generated_at"])
```

Reglas del upsert:

- `INSERT ... ON CONFLICT (source_id) DO UPDATE` (vehículos: `ON CONFLICT (plate)`).
- Cifrar `password` antes de persistir (no guardar en claro ni loguearlo).
- La transacción es por sync completo: o entra todo el snapshot o nada.
- Reintentos con backoff ante 5xx/timeout; si el sync falla, el watermark no avanza
  y el próximo intento repite el rango (los upserts son idempotentes).

### 4.3 Uso del pool de credenciales en Portal Clientes

Al consultar la API de Geotab (informes, ExceptionEvents de las reglas):

1. Tomar la credencial activa con `last_used_at` más antiguo **entre todas las
   databases con el mismo `database_key`** (ver §3.1) y marcarla usada.
2. Crear la sesión `mygeotab.API(username, password, database)` — cachear por
   `database:username` (mismo patrón que Navi Vehículos).
3. Ante error de autenticación, desactivar temporalmente esa credencial y reintentar
   con la siguiente del pool.
4. Consultar eventos por regla: `Get ExceptionEvent` con
   `search={"ruleSearch": {"id": rule_id}, "deviceSearch": {"id": geotab_device_id}, "fromDate": ..., "toDate": ...}`.

### 4.4 Errores del endpoint

| Código | Causa | Acción en Portal Clientes |
|---|---|---|
| `401` | API key inválida/ausente | Alertar — requiere intervención (rotación de clave). |
| `422` | Parámetro `since` mal formado | Bug del cliente — corregir formato ISO-8601. |
| `429` | Rate limit | Backoff exponencial, reintentar. |
| `503` | `INTEGRATION_API_KEYS` sin configurar en Navi Vehículos | Alertar — requiere intervención. |
| `5xx` | Error interno Navi Vehículos | Reintentar con backoff; watermark no avanza. |

---

## 5. Seguridad

- La API key vive solo en variables de entorno de ambos backends.
- `include_credentials=true` únicamente desde el job server-side; el frontend de
  Portal Clientes jamás ve passwords de Geotab.
- Passwords cifrados en reposo en ambas apps (Fernet con key propia por app).
- El endpoint de integración no expone usuarios, roles ni datos de auth de Navi
  Vehículos — solo el dominio cliente/vehículo/regla/credencial.

---

## 6. Disponibilidad y MTTR

```
GET {NAVI_BASE_URL}/api/v1/integration/availability
    ?month_from=2026-01          (requerido)
    &month_to=2026-03            (requerido)
    &since=2026-02-01T00:00:00Z  (opcional — incremental por last_calculated_at)
    &limit=500                   (opcional — default 500, máx. 2000)
    &offset=0                    (opcional — default 0)
```

Exporta las filas de disponibilidad mensual calculadas desde CloudFleet,
cruzadas con el cliente asignado localmente. Portal Clientes puede usar este
endpoint para replicar métricas de disponibilidad y MTTR por vehículo.

Filtros aplicados por Navi Vehículos:

- `month_from` / `month_to`: rango de `period_month` (formato `YYYY-MM`).
- `source = 'cloudfleet'`.
- Se excluyen las placas asignadas al cliente interno `__navitrans_system__`.
- `since` filtra filas con `last_calculated_at` posterior al valor dado.

### 6.1 Respuesta

```json
{
  "generated_at": "2026-03-15T10:00:00Z",
  "month_from": "2026-01",
  "month_to": "2026-03",
  "since": null,
  "total": 2,
  "limit": 500,
  "offset": 0,
  "rows": [
    {
      "plate": "ABC123",
      "period_month": "2026-01",
      "calculation_status": "calculated",
      "project_availability_pct": 98.5,
      "h_total": 744.0,
      "h_no_disp": 11.16,
      "orders_considered": 3,
      "mttr_hours": 3.72,
      "orders_closed": 3,
      "customer_id": 12,
      "customer_name": "Transportes El Roble",
      "last_calculated_at": "2026-01-31T23:59:00Z"
    },
    {
      "plate": "DEF456",
      "period_month": "2026-02",
      "calculation_status": "no_orders",
      "project_availability_pct": 100.0,
      "h_total": 672.0,
      "h_no_disp": 0.0,
      "orders_considered": 0,
      "mttr_hours": null,
      "orders_closed": 0,
      "customer_id": 12,
      "customer_name": "Transportes El Roble",
      "last_calculated_at": "2026-02-28T23:59:00Z"
    }
  ]
}
```

Notas sobre los campos clave:

| Campo | Significado |
|---|---|
| `rows[].period_month` | Mes al que aplica el cálculo (`YYYY-MM`). |
| `rows[].calculation_status` | `calculated`, `no_orders`, `not_in_cloudfleet` o `error`. |
| `rows[].project_availability_pct` | Porcentaje de disponibilidad del vehículo en el mes. |
| `rows[].h_total` | Horas totales del mes consideradas para el cálculo. |
| `rows[].h_no_disp` | Horas en las que el vehículo estuvo no disponible. |
| `rows[].orders_considered` | Órdenes de trabajo consideradas para el cálculo. |
| `rows[].mttr_hours` | Tiempo medio de reparación (horas) calculado sobre las órdenes cerradas. |
| `rows[].orders_closed` | Órdenes cerradas incluidas en el cálculo de MTTR. |
| `rows[].customer_id` / `customer_name` | Cliente asignado localmente; puede ser `null` si la placa no tiene cliente. |
| `rows[].last_calculated_at` | Última vez que se recalculó la fila; útil para sync incremental. |

### 6.2 Paginación

La respuesta incluye `total` (total de filas que coinciden con los filtros),
`limit`, `offset` y el array `rows` con hasta `limit` elementos. Portal Clientes
puede recorrer el dataset completo incrementando `offset` en cada request.

### 6.3 Errores

| Código | Causa | Acción en Portal Clientes |
|---|---|---|
| `422` | `month_from` / `month_to` mal formado o `since` no es ISO-8601 | Corregir formato de los parámetros. |
| `401` | API key inválida/ausente | Revisar rotación de clave. |
| `503` | `INTEGRATION_API_KEYS` sin configurar | Alertar al equipo de Navi Vehículos. |

---

## 7. Órdenes de taller activas

```
GET {NAVI_BASE_URL}/api/v1/integration/taller-ordenes
    ?customer_id=12              (opcional — filtra por cliente)
    &force_refresh=false         (opcional — default false)
```

Exporta las órdenes de taller activas que Navi Vehículos lee desde CloudFleet.
Cada orden viene enriquecida con el cliente asignado localmente y un indicador
 temporal (`status_indicator`).

### 7.1 Latencia y cache

El monitor mantiene una cache en memoria de 10 minutos. La primera llamada sin
cache caliente puede tardar aproximadamente **30–60 segundos** porque descarga
work-orders de CloudFleet en una ventana de ~180 días hacia atrás.

Portal Clientes debe:

- Usar `force_refresh=false` salvo que sepa que necesita datos frescos.
- Tolerar el warm-up inicial, o consumir el endpoint después de que un scheduler
  interno haya precalentado la cache.

### 7.2 Respuesta

```json
{
  "generated_at": "2026-07-12T10:00:00",
  "customer_id": null,
  "summary": {
    "total_active": 3,
    "on_time": 1,
    "about_to_expire": 0,
    "overdue": 1,
    "pending_closure": 1,
    "pending_closure_7d": 1,
    "pending_closure_30d": 0,
    "con_etiquetas": 1
  },
  "orders": [
    {
      "order_number": "OT-001",
      "plate": "ABC123",
      "customer_id": 12,
      "customer_name": "Transportes El Roble",
      "type": "preventive",
      "status": "opened",
      "status_indicator": "on_time",
      "time_status_text": "En tiempo",
      "days_elapsed": 2,
      "pending_closure_days": null,
      "maintenance_labels": []
    }
  ]
}
```

Cuando se pasa `customer_id`, la respuesta filtra `orders` y recalcula
`summary` únicamente sobre ese subconjunto.

Notas sobre los campos clave:

| Campo | Significado |
|---|---|
| `orders[].order_number` | Número de orden en CloudFleet. |
| `orders[].plate` | Placa normalizada del vehículo. |
| `orders[].customer_id` / `customer_name` | Cliente asignado localmente en Navi Vehículos; puede ser `null` si la placa no tiene asignación. |
| `orders[].status` | Estado nativo de CloudFleet (`opened`, `ontechnicalcompletion`, etc.). |
| `orders[].status_indicator` | `on_time`, `about_to_expire`, `overdue` o `pending_closure`. |
| `orders[].time_status_text` | Etiqueta legible del indicador temporal. |
| `orders[].days_elapsed` | Días transcurridos desde el inicio de la orden. |
| `orders[].pending_closure_days` | Días desde la terminación técnica sin cierre administrativo; `null` si no aplica. |
| `orders[].maintenance_labels` | Etiquetas de mantenimiento asociadas a la orden. |
| `summary.pending_closure_7d` | Órdenes con cierre administrativo pendiente de más de 7 días. |
| `summary.pending_closure_30d` | Órdenes con cierre administrativo pendiente de más de 30 días. |
| `summary.con_etiquetas` | Órdenes que tienen al menos una etiqueta de mantenimiento. |

### 7.3 Errores

| Código | Causa | Acción en Portal Clientes |
|---|---|---|
| `422` | `customer_id` menor o igual a 0 | Corregir el parámetro. |
| `401` | API key inválida/ausente | Revisar rotación de clave. |
| `503` | CloudFleet no disponible o no configurado; `INTEGRATION_API_KEYS` sin configurar | Reintentar con backoff; alertar al equipo de Navi Vehículos si persiste. |
