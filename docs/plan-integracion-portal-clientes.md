# Plan — Cambios en Navi Vehículos para la integración con Portal Clientes

> Documento hermano: [contrato-integracion-portal-clientes.md](contrato-integracion-portal-clientes.md)
> (describe el resultado exacto que recibe la app Portal Clientes).

## 1. Contexto y objetivo

Portal Clientes es una app nueva que muestra informes a los usuarios finales. Para
operar necesita conocer, por cliente:

- Los **vehículos** con su **código interno de Geotab** (`device.id` de la db del cliente).
- Las **databases de Geotab** de cada cliente y sus **credenciales** (varias por db,
  para repartir carga y no saturar una sola sesión).
- Las **reglas de Geotab por db**, separadas en dos categorías:
  - **Operación** (reglas de motor, las que hoy se agrupan por motor).
  - **Hábito seguro** (nueva categoría).

Navi Vehículos es la fuente de verdad de todo esto. El plan tiene 4 cambios internos
más 1 endpoint de salida.

## 2. Estado actual (resumen)

Tablas en PostgreSQL, DDL en [motor_catalog.py](../backend/app/services/motor_catalog.py)
(`_run_motor_tables_ddl_inner`):

| Tabla | Qué guarda hoy | Limitación |
|---|---|---|
| `customers` | `id`, `name` | OK |
| `customer_databases` | db por cliente + `username`/`password` embebidos, `connection_type`, `provider_config` (incl. `plate_prefix`), `access_url` | 1 sola credencial por fila; conexión y credencial mezcladas |
| `vehicle_motor_assignments` | placa (PK), vin, estados geotab, `geotab_customer_database_id`, motor, cliente, metadata Fenix | **No guarda el `device.id` de Geotab** |
| `geotab_rules` | `database_id`, `name`, `rule_id` (id Geotab tipo `aXyZ...`) | **Sin categoría** operación / hábito seguro |
| `geotab_rule_groups` + `geotab_rule_group_rules` | grupos de reglas por motor y db (`match_mode`) | Solo aplican a operación; sin cambios de fondo |

Flujo relevante: al asignar una db Geotab a un vehículo,
`_validate_vehicle_in_customer_geotab()` ya autentica contra la db del cliente y
encuentra el device por placa (con `plate_prefix`) o VIN — **pero solo retorna un
status** (`found`/`not_found`/`unknown`) y descarta el device. El `device["id"]`
está disponible gratis en ese punto.

---

## 3. Cambio 1 — Guardar el `geotab_device_id` del vehículo

El id de device de Geotab es **por database**: el mismo vehículo tiene ids distintos
en la db maestra de Navitrans y en la db del cliente. Lo que Portal Clientes necesita
es el id **en la db del cliente**, así que se guarda junto a
`geotab_customer_database_id` (que ya indica en qué db fue validado).

### 3.1 DDL

```sql
ALTER TABLE vehicle_motor_assignments
    ADD COLUMN IF NOT EXISTS geotab_device_id TEXT NULL;
ALTER TABLE vehicle_motor_assignments
    ADD COLUMN IF NOT EXISTS geotab_device_synced_at TIMESTAMPTZ NULL;
```

### 3.2 Captura automática (por placa, sin trabajo manual)

Archivos a tocar:

- [geotab_client.py](../backend/app/clients/geotab_client.py): sin cambios — ya
  retorna el device dict completo.
- [motor_catalog.py](../backend/app/services/motor_catalog.py):
  - `_validate_vehicle_in_customer_geotab()` → pasa a retornar
    `tuple[str, str | None]` (`status`, `device_id`). El device ya se obtiene con
    `get_cached_device_from_plate(...)` / `get_cached_device_from_vin(...)`; solo
    falta extraer `device.get("id")`.
  - `_update_geotab_customer_status()` → acepta `geotab_device_id` y setea también
    `geotab_device_synced_at = NOW()` cuando status = `found` (y `NULL` cuando
    `not_found`, para no dejar ids huérfanos de una db anterior).
  - `revalidate_vehicle_customer_geotab()` → propaga el id (este es el punto de
    re-sincronización por vehículo que ya existe vía
    `POST /vehicles/{plate}/revalidate-customer-geotab`).
  - `assign_vehicle_database()` / `_validate_and_store_customer_geotab()` → propagar.

### 3.3 Backfill

Job único (estilo `check_all_geotab_connections`, que ya itera por db con
credenciales del cliente):

- Para cada `customer_databases` tipo `geotab`, traer devices con
  `get_cached_devices(...)` (1 llamada por db, ya cacheada 5 min).
- Para cada vehículo con `geotab_customer_database_id` = esa db y
  `geotab_customer_status = 'found'`, matchear por placa/VIN con
  `_find_device_in_collection` y guardar `geotab_device_id`.
- Exponer como `POST /vehicles/backfill-geotab-ids` (permiso `vehicles.refresh` o
  similar) para correrlo una vez y ante migraciones de db de cliente.

---

## 4. Cambio 2 — Categoría de reglas: operación vs hábito seguro

### 4.1 DDL

```sql
ALTER TABLE geotab_rules
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'operacion';
-- constraint vía DO $$ (mismo patrón que uq_rule_one_group):
ALTER TABLE geotab_rules
    ADD CONSTRAINT ck_geotab_rules_category
    CHECK (category IN ('operacion', 'habito_seguro'));
```

Las filas existentes quedan como `'operacion'` (hoy todas las reglas registradas se
usan para grupos de motor), por lo que el default hace la migración de datos sola.

### 4.2 Backend

- `GeotabRuleCreateRequest` (schemas): nuevo campo `category` con default
  `"operacion"`, validado contra el enum.
- `create_geotab_rule()`: persistir `category`.
- `_list_rules_for_database()` / `GeotabRuleRecord`: incluir `category`; el merge de
  databases hermanas (`_sibling_database_ids`) no cambia — la dedupe por `rule_id`
  debe considerar `(rule_id, category)` por si una misma regla se registra en ambas
  categorías (caso raro pero válido).
- `create_geotab_rule_group()`: validar que las reglas agregadas a grupos de motor
  sean `category = 'operacion'` (los grupos son el mecanismo de operación; las de
  hábito seguro no se agrupan por motor).
- Nota: la `UNIQUE (database_id, rule_id)` actual impediría registrar la misma regla
  en ambas categorías. Si se quiere permitir, cambiar a
  `UNIQUE (database_id, rule_id, category)`; decisión por defecto: **mantener** la
  unicidad actual (una regla pertenece a una sola categoría) y solo relajarla si
  aparece el caso real.

### 4.3 Frontend (ClientesPage / gestión de reglas)

- Selector de categoría al registrar una regla (radio: "Operación" / "Hábito seguro").
- Listado de reglas por db con la categoría visible (pill `.status-ok` style o chip).
- El flujo de grupos de motor filtra solo reglas de operación.

---

## 5. Cambio 3 — Pool de credenciales por database

Hoy `customer_databases` lleva `username`/`password` en la misma fila; "varias
credenciales" se simula creando otra fila con el mismo `database_name` (de ahí el
hack de `_sibling_database_ids`). Se separa la credencial en su propia tabla.

### 5.1 DDL

```sql
CREATE TABLE IF NOT EXISTS customer_database_credentials (
    id BIGSERIAL PRIMARY KEY,
    customer_database_id BIGINT NOT NULL
        REFERENCES customer_databases(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    password TEXT NOT NULL,
    label TEXT NULL,                 -- ej. "cuenta reportes", "cuenta nocturna"
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ NULL,   -- para rotación least-recently-used
    last_auth_error_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (customer_database_id, username)
);
```

### 5.2 Migración de datos

En la DDL inicial (mismo patrón `_run_motor_tables_ddl_inner`):

```sql
INSERT INTO customer_database_credentials (customer_database_id, username, password)
SELECT id, username, password FROM customer_databases
ON CONFLICT (customer_database_id, username) DO NOTHING;
```

`customer_databases.username/password` se conservan como "credencial legacy /
primaria" durante la transición (todo el código actual sigue funcionando) y se
deprecan en una fase posterior. No se eliminan columnas en este plan.

### 5.3 Selector con rotación

Nueva función en `motor_catalog.py` (reemplaza gradualmente a
`_get_geotab_database_config`):

```
get_geotab_config(database_id, *, exclude_credential_ids=()) -> tuple[GeotabConfig, int]
```

- Elige la credencial activa con `last_used_at` más antiguo (LRU) →
  `UPDATE ... SET last_used_at = NOW()` en la misma query (`RETURNING`), atómico.
- El caller, ante error de autenticación (ya detectado por `_is_auth_error` en
  [geotab_client.py](../backend/app/clients/geotab_client.py)), marca
  `last_auth_error_at`, y reintenta con la siguiente credencial excluyendo la
  fallida. Si todas fallan, propaga el error.
- El cache de sesión de `geotab_client` ya es por `database:username`, así que varias
  credenciales conviven sin tocarlo.

Puntos de uso a migrar: `resolve_geotab_rule`, `inspect_geotab_rule_record`,
`_validate_vehicle_in_customer_geotab`, `revalidate_vehicle_customer_geotab`,
`check_all_geotab_connections`, providers de rendimientos
([performance_providers.py](../backend/app/services/performance_providers.py)).

### 5.4 API + Frontend

- `POST /customers/databases/{database_id}/credentials` · `GET` (lista sin password)
  · `PUT /credentials/{id}` (rotar password, activar/desactivar) ·
  `DELETE /credentials/{id}` (bloquear si es la última activa).
- En ClientesPage: sub-sección "Credenciales" dentro de cada database, tabla con
  username, label, estado, último uso.

### 5.5 Seguridad (recomendado, fase aparte)

Los passwords hoy están en texto plano en Postgres. Al crear la tabla nueva,
cifrarlos con Fernet (`cryptography`, key en env `CREDENTIALS_ENCRYPTION_KEY`):
`encrypt` al escribir, `decrypt` solo en `get_geotab_config`. Nunca exponer el
password por la API de gestión (solo por el endpoint de integración, ver Cambio 4).

---

## 6. Cambio 4 — Endpoint de integración para Portal Clientes

Router nuevo: `app/api/routes/integration.py`, prefijo `/api/v1/integration`.

### 6.1 Autenticación servicio-a-servicio

La auth actual es JWT por cookie (usuarios humanos) — no sirve para otra app.

- Header `X-API-Key`, validado contra env `INTEGRATION_API_KEYS`
  (lista separada por comas; permite rotar agregando la nueva antes de quitar la
  vieja). Comparación con `secrets.compare_digest`.
- Dependencia FastAPI `require_integration_key()` en `core/dependencies.py`.
- Rate-limit reutilizando `core/rate_limit.py`.
- Solo HTTPS en producción (ya cubierto por el reverse proxy de `infra/nginx`).

### 6.2 Endpoints

| Método y ruta | Devuelve |
|---|---|
| `GET /integration/snapshot` | Snapshot completo: clientes → databases → credenciales → reglas + vehículos. Parámetro `since` (ISO-8601) para sync incremental por `updated_at`. |
| `GET /integration/vehicles` | Solo vehículos, paginado (`limit`/`offset`, `since`). |
| `GET /integration/customers` | Clientes + databases + credenciales + reglas. |

`snapshot` es el principal; los otros dos son conveniencia. Las credenciales se
incluyen **solo** si el request trae `include_credentials=true` (y el password viaja
descifrado únicamente aquí).

El shape exacto del JSON está en
[contrato-integracion-portal-clientes.md](contrato-integracion-portal-clientes.md).

### 6.3 Implementación

- Service nuevo `app/services/integration_export.py`: una query por recurso con
  JOINs (no N+1), filtrando `updated_at > since` cuando aplica.
- Para detectar **borrados** en sync incremental: el snapshot incluye
  `deleted: false` implícito; Portal Clientes hace full-sync periódico (p. ej. 1/día)
  y marca como inactivo lo que ya no venga. No se implementa soft-delete acá.

---

## 7. Orden de ejecución sugerido

| Fase | Contenido | Depende de |
|---|---|---|
| 1 | Cambio 1 (geotab_device_id + captura + backfill) | — |
| 2 | Cambio 2 (categoría de reglas, backend + frontend) | — |
| 3 | Cambio 3 (tabla de credenciales + migración + selector LRU) | — |
| 4 | Migrar callers de `_get_geotab_database_config` al selector nuevo | 3 |
| 5 | Cambio 4 (endpoint de integración + API key) | 1–3 |
| 6 | Cifrado de passwords (Fernet) | 3 |

Las fases 1–3 son independientes entre sí y pueden hacerse en cualquier orden.

## 8. Estado de implementación (11 jun 2026) — ✅ COMPLETADO (fases 1–5)

Todo implementado y probado (`tests/test_portal_clientes_integration.py`, 13 tests).
Detalles que difieren ligeramente del plan original:

- **Backfill**: la ruta real es `POST /api/v1/vehicle/backfill-geotab-ids`
  (el router usa prefijo `/vehicle`, singular). Permiso: `vehicles.refresh`.
- **Limpieza del device id**: `not_found`/`not_applicable` limpian
  `geotab_device_id`; `unknown` (error de conexión) conserva el último valor
  conocido para no perder datos por una caída transitoria.
- **Credenciales — rutas**: `GET/POST /customers/databases/{id}/credentials`,
  `PUT/DELETE /customers/databases/credentials/{credential_id}`. No se puede
  eliminar ni desactivar la última credencial activa de una database.
- **Callers migrados al pool** (con rotación ante fallo de auth vía
  `call_with_geotab_credentials`): `resolve_geotab_rule`,
  `inspect_geotab_rule_record`, `_validate_vehicle_in_customer_geotab`
  (asignación y revalidación de vehículos).
- **Callers que siguen en credencial legacy** (sincronizada como primaria del
  pool, pendiente fase posterior): `check_all_geotab_connections` y los
  providers de rendimientos (`performance_providers.py`).
- **Frontend**: selector de categoría en el form de reglas, panel "Reglas de
  hábito seguro" y panel "Credenciales" (alta, activar/desactivar, eliminar)
  dentro del modal de detalle de database (ClientesPage).
- **Sharing por db física** (ajuste post-revisión): las "databases hermanas"
  ahora se determinan **solo por `database_name`** (case-insensitive), ya no
  por (nombre, username). El nombre de db es único globalmente en MyGeotab, así
  que si Navitrans y Vigía comparten la db `navitrans`, las reglas registradas
  bajo cualquiera de los dos aplican para ambos (no hay que re-registrar), no
  se permite duplicar una regla entre hermanas, y el pool de credenciales rota
  entre **todas** las credenciales de la db física (de cualquier cliente). El
  snapshot expone `database_key` para que Portal Clientes haga el mismo join.
- **Pendiente (fase 6, opcional)**: cifrado Fernet de passwords en reposo.
- **Config**: agregar `INTEGRATION_API_KEYS` al `.env` (ya está en
  `.env.example`). Sin la variable, `/integration/*` responde 503.

## 9. Riesgos y notas

- **Ids de device por db**: si un cliente migra de db Geotab, el `geotab_device_id`
  queda obsoleto → siempre interpretarlo junto a `geotab_customer_database_id`; la
  revalidación y el backfill lo corrigen.
- **`plate_prefix`**: algunos clientes guardan devices con prefijo en la placa
  (`provider_config.plate_prefix`); el matching existente ya lo maneja — el backfill
  debe pasarlo.
- **Unicidad de reglas por categoría**: ver decisión en §4.2.
- **Credenciales legacy**: mientras convivan `customer_databases.username/password`
  y la tabla nueva, el selector debe leer **solo** la tabla nueva (la migración
  inicial garantiza que la legacy está copiada).
- **DDL en runtime**: este proyecto crea tablas vía `_run_motor_tables_ddl_inner`
  (no Alembic, salvo auth). Mantener el mismo patrón para consistencia.
