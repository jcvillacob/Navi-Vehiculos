# Plan — Webhook Geotab "En taller" + mapa de vehículos en taller

> Endpoint público que recibe eventos de Geotab (entrada/salida de taller) y los
> sirve al mapa de Navi Vehículos (`/mapa`) con cálculo de tiempo en taller,
> ventanas de visibilidad y gracia por reingreso.

---

## 1. Contexto y objetivo

Geotab dispara una regla **"En taller - Proyecto"** cuando un vehículo entra a una
geocerca de taller, y **"Salida taller - Proyecto"** cuando sale. Cada evento llega
como un JSON "sucio" (campos con `_` en vez de espacios/puntos decimales) a un
webhook nuestro, expuesto públicamente en el dominio de la app:

```
https://vehiculos.proyectosnavi.dev/api/v1/geotab/taller?api_key=...
```

El sistema debe:

1. Recibir el evento, limpiarlo y **resolver el vehículo** (por VIN, fallback
   placa). Si no existe o su categoría efectiva no es **Flota Administrada** o
   **Experiencia Superior**, se **ignora** (no aparece en el mapa).
2. Guardar el ingreso y mantener **estado vivo** por vehículo.
3. Mostrar en el mapa **solo** los vehículos que lleven **≥ 30 min** dentro del
   taller. El cálculo de tiempo se hace en el backend.
4. Al salir, **quitar del mapa** pero seguir contando el vehículo durante **1 h**
   de gracia. Si **reingresa** dentro de esa hora, se toma como que **nunca salió**
   (se conserva el `enter_ts` original).
5. Actualización **silenciosa** del front cada **10 min** sin recargar.
6. Payload optimizado: el mapa carga solo lo mínimo.
7. El timestamp se guarda tal como viene + un `event_ts` UTC parseado; el front
   recibe la hora ya convertida a **America/Bogota**.
8. Pasada **1 h** fuera, el vehículo se elimina del todo.

---

## 2. Decisiones de diseño (confirmadas)

| Tema | Decisión |
|---|---|
| Auth del webhook | **API key en query string** (`?api_key=...`), validada contra `GEOTAB_WEBHOOK_API_KEYS` (lista por comas, rotación). Patrón nuevo (no el header `X-API-Key` de integración). |
| Señal de entrada/salida | Entrada: `rule_triggered` limpio = `En taller - Proyecto`. Salida: `Salida taller - Proyecto`. **Configurables por env**, defaults arriba. |
| Alcance "público" | **Solo el webhook** es público. `/mapa` y su endpoint de datos siguen **detrás de login** (auth por cookie JWT, sin cambios). |
| Resolución vehículo | **1) por VIN** (`vehicle_motor_assignments.vin` = `vin`/`device_name` del payload), **2) fallback por placa** (`plate` = `UPPER(device_name)` cuando `device_name` no es un VIN). `device_id` **no** se usa para matchear (se repite entre DBs de Geotab); se guarda solo de referencia. |
| Filtro de zona | **Confiar en la regla** de Geotab; no filtrar por `zone_name`. |
| Almacenamiento | **Redis** = estado vivo por placa (con TTLs de gracia). **PostgreSQL** = historial/auditoría de eventos raw. Redis ya existe en el stack. |
| Refresh front | **Polling cada 10 min**. Para evitar lentitud con muchos viewers concurrentes, el snapshot del mapa se **cachea en Redis (TTL 60 s)** + **ETag/304**. |

> **Suposición a confirmar:** el webhook no envía un campo "placa" explícito; solo
> `device_id`, `device_name` y `vin` (en el sample, `device_name` == `vin`).
> "Segundo por placa" se interpreta como: si `device_name`/`vin` no matchea la
> columna `vin`, se intenta matchear `device_name` contra la columna `plate`. Si en
> la práctica `device_name` siempre es el VIN, el fallback de placa simplemente no
> opera (es un safety net).

---

## 3. Payload de Geotab y limpieza

### 3.1 Payload crudo (ejemplo)

```json
{
  "event_info": {
    "exception_id": "a2SRCOgOAx0iOM8LRsUWQyA",
    "rule_triggered": "En_taller_-_Proyecto",
    "date": "Jun,_24,_2026",
    "time": "4:27:21_PM",
    "timezone": "UTC"
  },
  "asset_info": {
    "device_id": "b945",
    "device_name": "LDYCCS8D0V0000035",
    "vin": "LDYCCS8D0V0000035"
  },
  "telemetry_info": {
    "zone_id": "b38FC7",
    "zone_name": "Navitrans_Itagui",
    "latitude": "6_15622",
    "longitude": "-75_62516",
    "odometer": "1,721_km"
  }
}
```

### 3.2 Reglas de limpieza (`clean_geotab_payload`)

Barredora recursiva **consciente del campo** (no un replace global, porque `_`
significa cosas distintas según el campo):

| Campo | Transformación | Resultado |
|---|---|---|
| `latitude`, `longitude` | `_` → `.`, luego `float(...)` | `6.15622`, `-75.62516` |
| `odometer` | `_` → ` ` | `"1,721 km"` (se guarda string + valor numérico parseado) |
| `date`, `time`, `timezone` | `_` → ` `, strip | `"Jun, 24, 2026"`, `"4:27:21 PM"`, `"UTC"` |
| `rule_triggered`, `zone_name` | `_` → ` `, strip, collapse espacios | `"En taller - Proyecto"`, `"Navitrans Itagui"` |
| `device_name`, `vin` | strip (sin tocar `_`, son VINs) | `"LDYCCS8D0V0000035"` |
| `device_id`, `zone_id`, `exception_id` | sin cambios (ids internos) | `"b945"`, `"b38FC7"` |

### 3.3 Timestamp

```python
timestamp_str = f"{date} {time}"            # "Jun, 24, 2026 4:27:21 PM"
event_ts_utc = datetime.strptime(timestamp_str, "%b, %d, %Y %I:%M:%S %p")
                .replace(tzinfo=pytz.UTC)
```

- Se guarda el **raw** (`date_raw`, `time_raw`, `timezone_raw`) **y** el `event_ts`
  UTC parseado (timestamptz) en PG.
- Para el front, el backend convierte a `America/Bogota` antes de responder
  (req #7). El front **no** hace conversión de zona.

---

## 4. Resolución de vehículo y filtro de categoría (req #1)

```
identifier = (device_name or vin).strip()        # limpio
```

Consulta en `vehicle_motor_assignments` (mismo patrón de conexión de
[motor_catalog.py](../backend/app/services/motor_catalog.py)):

```sql
-- Paso 1: por VIN
SELECT a.plate, a.vin, a.geotab_device_id,
       COALESCE(a.category, c.category, 'Ninguna') AS category,
       c.name AS client_name, m.engine_name AS motor
FROM vehicle_motor_assignments a
LEFT JOIN customers c ON c.id = a.customer_id
LEFT JOIN motor_catalog m ON m.technical_number = a.technical_number
WHERE a.vin ILIKE %s
LIMIT 1;

-- Paso 2 (solo si el paso 1 no devuelve fila): por placa
... WHERE a.plate = UPPER(%s) LIMIT 1;
```

Reglas:

- Si **no se encuentra** el vehículo → evento `ignored=True`,
  `ignore_reason='vehicle_not_found'`. Se persiste en PG (auditoría) pero **no**
  crea estado en Redis ni aparece en el mapa.
- Si la **categoría efectiva** es `Ninguna` → `ignored=True`,
  `ignore_reason='category_ninguna'`. Mismo tratamiento: auditable, invisible en
  el mapa.
- Solo `Flota Administrada` o `Experiencia Superior` avanzan al estado en Redis.

> `device_id` **no** se usa para matchear (se repite entre DBs de Geotab). Se
> guarda en PG y en el estado de Redis solo como referencia.

---

## 5. Estado vivo en Redis (máquina de estados por placa)

### 5.1 Claves

| Clave | Tipo | Contenido |
|---|---|---|
| `taller:state:{plate}` | Hash | `plate`, `vin`, `device_id`, `zone_id`, `zone_name`, `lat`, `lng`, `enter_ts` (UTC ISO), `exit_ts`, `last_event_ts`, `category`, `client_name`, `motor`, `odometer`, `status` |
| `taller:active` | Set | placas con `status` ∈ {`in`, `grace`} (para enumerar el mapa rápido) |

Cliente Redis: reusar el patrón de
[auth_service.py](../backend/app/services/auth_service.py) `_redis_client()` →
`redis.from_url(settings.redis_url, decode_responses=True)` (singleton por módulo).

### 5.2 Transiciones

**Evento de entrada** (vehículo pasa filtro de categoría):

| Estado actual | Acción |
|---|---|
| No existe | Crear `taller:state:{plate}`, `status=in`, `enter_ts=event_ts`, `last_event_ts=event_ts`. Agregar a `taller:active`. |
| `status=in` | Refrescar `lat/lng/zone/odometer/last_event_ts`. **No** cambiar `enter_ts`. |
| `status=grace` | **Reingreso = nunca salió**: `status=in`, **conservar `enter_ts` original**, limpiar `exit_ts`, actualizar `last_event_ts`. (req #4) |

**Evento de salida**:

| Estado actual | Acción |
|---|---|
| `status=in` | `status=grace`, `exit_ts=event_ts`, actualizar `last_event_ts`. **Sigue en `taller:active`** (gracia) pero **no se muestra en el mapa**. (req #4) |
| `status=grace` | Refrescar `exit_ts`/`last_event_ts` (sin reiniciar `enter_ts`). |
| No existe | Ignorar (salida sin entrada previa conocida). `ignored=True`, `ignore_reason='exit_without_state'`. |

**Expiración de gracia (req #8)** — dos mecanismos complementarios:

- **Lazy** (garantiza correctness): al construir el snapshot del mapa, antes de
  responder, eliminar de Redis los `status=grace` con `now - exit_ts > TALLER_GRACE_HOURS`.
- **Sweep periódico** (mantiene Redis limpio): job del
  [scheduler.py](../backend/app/services/scheduler.py) cada 5 min que purga lo
  expirado y recalcula TTLs. Sigue el patrón de jobs existente.

### 5.3 TTL de claves

- `taller:state:{plate}`: al pasar a `grace`, `EXPIRE` = `TALLER_GRACE_HOURS*3600 + buffer`.
  En `in`, sin TTL (se gestiona por eventos/sweep) o TTL largo de seguridad (ej. 24 h)
  para no acumular estado stale si Geotab deja de reportar.

---

## 6. Endpoint del mapa + concurrencia (req #2, #3, #5, #6)

### 6.1 Endpoint

```
GET /api/v1/mapa/taller
```

- **Auth:** cookie JWT (mismo `get_current_user` que el resto de la app). El mapa
  sigue detrás de login.
- **Respuesta 200** — solo vehículos `status=in` con `now - enter_ts >= TALLER_MIN_MINUTES`:

```jsonc
{
  "generated_at": "2026-06-24T11:27:21-05:00",   // America/Bogota
  "vehicles": [
    {
      "plate": "TLK240",
      "lat": 6.15622,
      "lng": -75.62516,
      "zone_id": "b38FC7",
      "zone_name": "Navitrans Itagui",
      "category": "Flota Administrada",
      "client_name": "Transportes Andina",
      "motor": "ISD",
      "enter_ts_local": "2026-06-24T10:55:00-05:00",
      "minutes_inside": 32,
      "odometer": "1,721 km"
    }
  ],
  "zones": [
    { "id": "b38FC7", "name": "Navitrans Itagui", "lat": 6.15622, "lng": -75.62516 }
  ]
}
```

- `minutes_inside` se **calcula en el backend** (req #3) = `now - enter_ts`.
- `enter_ts_local` ya en America/Bogota (req #7).
- `zones` se **deriva de los vehículos** (zona por `zone_id`, coords del primer
  vehículo de esa zona). Reemplaza las geofences harcodeadas de
  [mockData.js](../frontend/src/features/mapa/mockData.js).

### 6.2 Snapshot cache + ETag (concurrencia)

Para que **varias personas viendo el mapa a la vez** no disparen cómputo
redundante ni golpeen Redis por cada request:

1. Al construir el snapshot, si existe `mapa:snapshot` en Redis (TTL
   `MAPA_SNAPSHOT_TTL_SECONDS`, default **60 s**) → se devuelve directo.
2. Si no existe → se construye (lazy cleanup de gracia + enumerar `taller:active`
   + filtrar ≥30 min + calcular minutos + join info vehículo), se serializa a JSON
   y se guarda en `mapa:snapshot` con su TTL.
3. **ETag**: hash del snapshot → header `ETag`. Si el cliente manda
   `If-None-Match` y coincide → **304 Not Modified** (cuerpo vacío).

Resultado: el polling de 10 min de N viewers converge al mismo snapshot cacheado
(≤60 s de staleness) → carga O(1) por request, sin lentitud.

### 6.3 Payload optimizado (req #6)

El endpoint del mapa devuelve **solo** los campos de arriba. Nada de raw payload,
ni historial, ni vehículos en gracia, ni vehículos con <30 min. El webhook sí
guarda todo en PG (auditoría), pero eso no se sirve al mapa.

---

## 7. Webhook de Geotab (entrada pública)

### 7.1 Endpoint

```
POST /api/v1/geotab/taller?api_key=<key>
```

- **Auth:** query param `api_key` validado contra `GEOTAB_WEBHOOK_API_KEYS`
  (comma-separated, rotación) con `secrets.compare_digest` (mismo estilo seguro de
  [dependencies.py](../backend/app/core/dependencies.py) `require_integration_key`).
- **Público:** no requiere login. Ya queda cubierto por `location /api/` del
  [nginx](../infra/nginx/default.conf) (proxy a backend). El TLS lo termina el
  reverse proxy externo que apunta `vehiculos.proyectosnavi.dev` al `APP_PORT`.

### 7.2 Flujo

1. Validar `api_key` (query). Si falta/invalid → **401**.
2. Leer cuerpo crudo (`await request.body()`), `json.loads`, `clean_geotab_payload`.
3. Parsear `event_ts` UTC. Si no parsea → **422**.
4. Clasificar `event_kind`: `enter` / `exit` / `unknown` según `rule_triggered`.
5. Resolver vehículo (VIN → placa) + categoría.
6. Persistir evento en PG `geotab_taller_events` (siempre, con flag `ignored`).
7. Si **no** ignorado → aplicar transición en Redis (§5.2) + invalidar
   `mapa:snapshot` (DEL) para que el próximo read reconstruya con el dato nuevo.
8. Responder **200**:
   - `{"status":"ok","event_kind":"enter","plate":"TLK240"}`
   - `{"status":"ok","ignored":true,"reason":"category_ninguna"}` (200 igual, para
     que Geotab **no reintente**).
   - `{"status":"error","message":"..."}` solo ante fallo real (payload corrupto,
     error interno) → Geotab puede reintentar.

> Siempre **200** para procesado-o-ignorado; 4xx/5xx solo para fallos genuinos.

---

## 8. Historial en PostgreSQL

Nueva tabla, DDL idempotente con el patrón `_ensure_*_table` (flag global, conexión
propia) de [motor_catalog.py](../backend/app/services/motor_catalog.py) y
[availability_store.py](../backend/app/services/availability_store.py):

```sql
CREATE TABLE IF NOT EXISTS geotab_taller_events (
    id              BIGSERIAL PRIMARY KEY,
    plate           VARCHAR(10) NULL,        -- resuelto (NULL si no encontrado)
    vin             TEXT NULL,
    device_id       TEXT NULL,
    rule_triggered  TEXT NOT NULL,
    event_kind      TEXT NOT NULL CHECK (event_kind IN ('enter','exit','unknown')),
    zone_id         TEXT NULL,
    zone_name       TEXT NULL,
    latitude        DOUBLE PRECISION NULL,
    longitude       DOUBLE PRECISION NULL,
    odometer        TEXT NULL,
    date_raw        TEXT NULL,
    time_raw        TEXT NULL,
    timezone_raw    TEXT NULL,
    event_ts        TIMESTAMPTZ NOT NULL,    -- UTC parseado
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ignored         BOOLEAN NOT NULL DEFAULT FALSE,
    ignore_reason   TEXT NULL,               -- vehicle_not_found | category_ninguna | exit_without_state | unknown_rule
    raw_payload     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_geotab_taller_events_plate_ts
    ON geotab_taller_events (plate, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_geotab_taller_events_ts
    ON geotab_taller_events (event_ts DESC);
```

Uso: solo auditoría/diagnóstico. El mapa **no** lee de esta tabla (lee Redis).

---

## 9. Frontend (req #5, #7)

### 9.1 Cambios

| Archivo | Cambio |
|---|---|
| [useMapaData.js](../frontend/src/features/mapa/hooks/useMapaData.js) | Reemplazar mock por `fetch('/api/v1/mapa/taller')`. Polling cada `VITE_MAPA_POLL_MS` (default 600000 = 10 min) con `setInterval`. Primera carga muestra spinner; refrescos silenciosos (sin spinner, solo actualizan markers). ETag/304 support. |
| [mockData.js](../frontend/src/features/mapa/mockData.js) | Deprecar/eliminar una vez conectado el real. |
| [MapView.jsx](../frontend/src/features/mapa/components/MapView.jsx) | Adaptar shape: `v.minutes_inside` (en vez de `hoursInside`), zonas dinámicas desde `zones` (circles por `zone_id` con coords del payload), popup con hora local ya formateada. |
| [MapaPage.jsx](../frontend/src/pages/MapaPage.jsx) | Columna "Tiempo en taller" usa `minutes_inside` formateado; chip de zona desde `zone_name`; badge de categoría. |
| `src/api/` | Nuevo helper `mapaApi.js` (mismo estilo del resto de `src/api/`). |
| [App.jsx](../frontend/src/App.jsx) | **Sin cambios**: `/mapa` sigue dentro de `ProtectedRoute` (login obligatorio). |

### 9.2 Actualización silenciosa

```js
// useMapaData.js (esquema)
useEffect(() => {
  let cancelled = false;
  const load = async () => {
    const res = await fetch('/api/v1/mapa/taller', { headers: etagHeader });
    if (res.status === 304) return;           // sin cambios
    const data = await res.json();
    if (!cancelled) { setVehicles(data.vehicles); setZones(data.zones); saveEtag(res); }
  };
  load();                                      // carga inicial (con spinner)
  const id = setInterval(load, POLL_MS);       // refrescos silenciosos cada 10 min
  return () => { cancelled = true; clearInterval(id); };
}, []);
```

- El backend entrega `enter_ts_local` y `minutes_inside` → el front **solo
  formatea**, no convierte zona (req #7).
- El polling de todos los viewers golpea el snapshot cacheado (≤60 s) → sin
  lentitud aunque entren muchos al tiempo.

---

## 10. Variables de entorno

Añadir a [.env.example](../.env.example) y a
[config.py](../backend/app/core/config.py):

| Variable | Default | Descripción |
|---|---|---|
| `GEOTAB_WEBHOOK_API_KEYS` | (vacío) | Claves del webhook, separadas por comas (rotación). Vacío → webhook 503. |
| `GEOTAB_TALLER_RULE_ENTER` | `En taller - Proyecto` | `rule_triggered` limpio que indica entrada. |
| `GEOTAB_TALLER_RULE_EXIT` | `Salida taller - Proyecto` | `rule_triggered` limpio que indica salida. |
| `TALLER_MIN_MINUTES` | `30` | Min. minutos dentro para aparecer en el mapa (req #2). |
| `TALLER_GRACE_HOURS` | `1` | Horas de gracia tras salida (req #4, #8). |
| `MAPA_SNAPSHOT_TTL_SECONDS` | `60` | TTL del cache de snapshot del mapa. |
| `VITE_MAPA_POLL_MS` | `600000` | Cadencia de polling del front (10 min). |

---

## 11. Archivos a tocar (resumen)

### Backend (nuevos)
- `app/api/routes/geotab_taller.py` — router: `POST /geotab/taller` (webhook) + `GET /mapa/taller` (datos).
- `app/services/geotab_taller.py` — limpieza, resolución vehículo, máquina de estados Redis, snapshot cache, persistencia PG.
- `app/schemas/geotab_taller.py` — modelos pydantic (payload crudo, evento limpio, respuesta mapa).

### Backend (edición)
- `app/core/dependencies.py` — añadir `require_geotab_webhook_key` (query param).
- `app/core/config.py` — añadir settings (§10).
- `app/api/router.py` — registrar `geotab_taller` router.
- `app/services/scheduler.py` — job de sweep de gracia (opcional, §5.2).
- `.env.example` — nuevas variables (§10).

### Frontend
- `src/features/mapa/hooks/useMapaData.js` — fetch real + polling + ETag.
- `src/features/mapa/components/MapView.jsx` — shape nuevo + zonas dinámicas.
- `src/pages/MapaPage.jsx` — columnas/badges.
- `src/features/mapa/mockData.js` — eliminar.
- `src/api/mapaApi.js` — helper.

### Infra
- `infra/nginx/default.conf` — **sin cambios** (`/api/` ya proxya al backend; la CSP ya permite tiles OSM y `connect-src 'self'`). Confirmar que el reverse proxy externo termina TLS para `vehiculos.proyectosnavi.dev`.

### Tests
- `backend/tests/test_geotab_taller.py` — limpieza, parseo, resolución VIN/placa, filtro categoría, transiciones (enter/exit/reingreso/gracia/expiración), snapshot cache, auth por query param.

---

## 12. Rutas finales

| Método | Ruta | Auth | Propósito |
|---|---|---|---|
| `POST` | `/api/v1/geotab/taller?api_key=` | query key (público) | Recibe eventos de Geotab. |
| `GET` | `/api/v1/mapa/taller` | cookie JWT (login) | Snapshot de vehículos en taller ≥30 min para el mapa. |

---

## 13. Orden de implementación sugerido

1. **Backend base:** schemas + limpieza + parseo timestamp + tests unitarios de limpieza.
2. **Persistencia:** DDL `geotab_taller_events` + guardado de evento raw (con `ignored`).
3. **Resolución + filtro:** match VIN/placa + categoría efectiva.
4. **Estado Redis:** máquina de estados (enter/exit/reingreso) + TTLs.
5. **Webhook:** ruta `POST /geotab/taller` con auth por query + integración de 1–4.
6. **Snapshot:** `GET /mapa/taller` + cache Redis + ETag + lazy cleanup de gracia.
7. **Sweep:** job periódico de expiración de gracia.
8. **Frontend:** `useMapaData` real + polling + `MapView`/`MapaPage` adaptados.
9. **Env + nginx:** variables, confirmar TLS externo, smoke test contra `vehiculos.proyectosnavi.dev`.

---

## 14. Casos borde y notas

- **Reingreso dentro de gracia:** conservar `enter_ts` original → el tiempo en
  taller **no** se reinicia (req #4). Solo se limpia `exit_ts` y `status=in`.
- **Entrada sin salida previa (refresco):** no reiniciar `enter_ts`; solo actualizar
  posición/`last_event_ts`.
- **Salida sin estado previo:** `ignored=True, reason='exit_without_state'` (auditable).
- **Regla desconocida:** `event_kind='unknown'`, `ignored=True, reason='unknown_rule'`.
- **Vehículo no registrado / categoría Ninguna:** auditable en PG, invisible en mapa.
- **Geotab reenvía el mismo evento:** idempotencia por `exception_id` — antes de
  procesar, comprobar si ya existe en PG (o en Redis `taller:seen:{exception_id}`
  con TTL 24 h); si existe, responder 200 sin reprocesar.
- **Reloj:** el cálculo de `minutes_inside` y la expiración de gracia usan `now`
  UTC del servidor; el `event_ts` viene del payload (UTC). Si el reloj de Geotab
  difiere, se usa `event_ts` para las transiciones y `now` del server para las
  ventanas de visibilidad/expiración.
