# Plan — ID externo manual por vehículo para proveedores GPS

## Contexto y problema

Hoy el `provider_vehicle_id` (el ID con el que los proveedores GPS identifican a un vehículo) se resuelve así:

| Proveedor | Resolución |
|---|---|
| **Geotab** | Auto por placa — `find_device_by_plate(api, plate)` |
| **Artimo** | 1) `vehicle_provider_bindings` (DB) → 2) `legacy_bindings` en `provider_config` de la database → 3) **dict hardcodeado en `backend/app/services/legacy_provider_bootstrap.py`** (10 UUIDs de Opperar) → 4) extracción del reporte `trips` |
| **Frotcom** | 1) `vehicle_provider_bindings` (DB) → 2) auto por placa vía `/v2/vehicles` |

Problemas:
- Existe un **dict hardcodeado** (Opperar/Artimo) en el código fuente. Eso no escala y obliga a hacer deploy para añadir clientes.
- Cuando el matching auto por placa falla (p.ej. Frotcom devuelve `PXK375` pero la placa en Navi está como `PXK-375`, o Artimo no trae la placa en el reporte), no hay forma de que un editor corrija el binding desde la UI sin editar `provider_config.legacy_bindings` a mano.
- Un mismo vehículo podría necesitar IDs distintos por proveedor si en el futuro se usa más de uno (p.ej. CAN de Frotcom + GPS Artimo). El diseño actual ya lo permite en `vehicle_provider_bindings` (UNIQUE por plate+database+provider), pero no hay UI.

## Objetivo

Permitir al usuario **fijar manualmente el `provider_vehicle_id` por vehículo** desde el modal de asignación del vehículo, y que los adapters de rendimientos lo usen con **máxima prioridad** antes de cualquier auto-resolución. Migrar el dict hardcoded de Opperar al nuevo mecanismo y eliminarlo del código.

## Decisiones arquitectónicas (fijas)

1. **Fuente de verdad única:** la tabla existente `vehicle_provider_bindings` (`plate, customer_database_id, provider, provider_vehicle_id, binding_status, ...`). **No** se crea tabla nueva. **No** se añade columna a `vehicle_motor_assignments`.
2. **Scope del binding:** `(plate, customer_database_id, provider)`. El provider se toma del `connection_type` de la database asignada al vehículo. Un vehículo sólo tiene una database asignada a la vez, así que en la práctica la UI maneja un único binding por vehículo.
3. **Campo nuevo:** `vehicle_provider_bindings.is_manual BOOLEAN NOT NULL DEFAULT FALSE`. Marca los bindings puestos por un humano para que las auto-resoluciones **nunca los sobreescriban**.
4. **Prioridad de resolución** (nueva, igual para los 3 proveedores):
   1. `vehicle_provider_bindings` con `is_manual = TRUE` → se respeta sin llamar al API externo.
   2. `vehicle_provider_bindings` con `is_manual = FALSE` → se respeta como cache; si el API externo devuelve otro valor distinto, se actualiza.
   3. Auto-resolución del adapter (Geotab `find_device_by_plate`, Frotcom `/v2/vehicles`, Artimo extracción de `trips`).
   4. **Eliminado:** el dict hardcoded de `legacy_provider_bootstrap.py` y la ruta `legacy_bindings` dentro de `provider_config`.
5. **Migración de datos:** al correr la migración, los 10 bindings de Opperar (hoy en el dict hardcoded) se insertan en `vehicle_provider_bindings` con `is_manual = TRUE` y `binding_status = 'resolved'`. Requiere resolver el `customer_database_id` de Opperar/Artimo en tiempo de migración.
6. **UI:** el campo aparece en `VehicleAssignmentModal` como *“ID externo del vehículo (opcional)”* **solo si** la database seleccionada tiene `connection_type ∈ {geotab, artimo, frotcom}`. Para `database` no se muestra.
7. **Permisos:** editar este campo requiere `vehicles.edit` (el mismo que ya protege `PUT /api/v1/vehicle/{plate}/database`). Sin permiso nuevo.
8. **Nomenclatura en UI (español):** Etiqueta *“ID externo del vehículo”*, subtexto *“Se usará tal cual para consultar {PROVEEDOR}. Si lo dejas vacío se resuelve automáticamente por placa.”*

---

## FASE 1 — Migración de esquema y datos

### 1.1. Crear migración Alembic

Crear archivo `backend/app/migrations/versions/20260422_0001_add_manual_provider_vehicle_bindings.py`.

Contenido exacto:

```python
from __future__ import annotations

from alembic import op


revision = "20260422_0001"
down_revision = "20260417_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Asegurar que la tabla exista (fue creada vía _ensure_performance_tables, no por Alembic).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_provider_bindings (
            id BIGSERIAL PRIMARY KEY,
            plate VARCHAR(10) NOT NULL,
            customer_database_id BIGINT NOT NULL REFERENCES customer_databases(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            provider_vehicle_id TEXT NULL,
            provider_plate TEXT NULL,
            provider_customer_id TEXT NULL,
            binding_status TEXT NOT NULL DEFAULT 'unknown',
            last_resolved_at TIMESTAMPTZ NULL,
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (plate, customer_database_id, provider)
        );
        """
    )

    # 2. Añadir columna is_manual.
    op.execute(
        """
        ALTER TABLE vehicle_provider_bindings
        ADD COLUMN IF NOT EXISTS is_manual BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )

    # 3. Backfill de los bindings hardcoded de Opperar/Artimo.
    #    Sólo se insertan si encontramos la database (cliente "Opperar", database cuyo
    #    nombre contiene "artimo" case-insensitive). Si no existe, no-op.
    op.execute(
        """
        WITH target AS (
            SELECT cd.id AS database_id
            FROM customer_databases cd
            INNER JOIN customers c ON c.id = cd.customer_id
            WHERE LOWER(c.name) = 'opperar'
              AND LOWER(cd.database_name) LIKE '%artimo%'
            LIMIT 1
        ),
        pairs(plate, provider_vehicle_id) AS (
            VALUES
                ('TLK520', '68065b1e-f1a2-4510-b1b5-2fc9571b2b18'),
                ('TLK521', '44c21d53-2969-4b60-8d9e-22e837782ec5'),
                ('TLK522', 'c1140340-25f5-4dd4-835d-19dd78cd6059'),
                ('TLK523', '9d3c9acc-5bd9-47fd-a981-dd026087cd65'),
                ('TLK524', '1f4c222d-c79f-4ce2-b0b8-ce053f0f9469'),
                ('TLK525', '2406b72d-6a39-47e3-aaa7-60f50c14f696'),
                ('TLK526', 'a09748d5-d392-425f-8e5e-3090ada7632a'),
                ('TLK527', '0884ba93-f1a7-4639-98b6-17ab2bddedc4'),
                ('TLK528', '2063037e-4652-4d18-adb4-c1563e44c2b8'),
                ('TLK529', '53bc1c18-a38b-4bb1-a026-a2ea65916bab')
        )
        INSERT INTO vehicle_provider_bindings (
            plate, customer_database_id, provider,
            provider_vehicle_id, binding_status,
            last_resolved_at, is_manual
        )
        SELECT
            p.plate,
            t.database_id,
            'artimo',
            p.provider_vehicle_id,
            'resolved',
            NOW(),
            TRUE
        FROM pairs p
        CROSS JOIN target t
        WHERE EXISTS (SELECT 1 FROM target)
          AND EXISTS (
              SELECT 1 FROM vehicle_motor_assignments a WHERE a.plate = p.plate
          )
        ON CONFLICT (plate, customer_database_id, provider)
        DO UPDATE SET
            provider_vehicle_id = EXCLUDED.provider_vehicle_id,
            binding_status = 'resolved',
            is_manual = TRUE,
            last_resolved_at = NOW(),
            updated_at = NOW();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE vehicle_provider_bindings
        DROP COLUMN IF EXISTS is_manual;
        """
    )
```

### 1.2. Sincronizar la función `_ensure_performance_tables`

Editar `backend/app/services/rendimientos.py` → función `_ensure_performance_tables` (aprox. línea 23).

En el bloque que crea `vehicle_provider_bindings`, después del `CREATE TABLE IF NOT EXISTS`, añadir un `ALTER TABLE ... ADD COLUMN IF NOT EXISTS is_manual BOOLEAN NOT NULL DEFAULT FALSE;`. Esto cubre entornos donde Alembic no corra antes de que alguien llame la lógica de rendimientos (el patrón ya se usa para el resto de columnas en `motor_catalog.py`).

Ejemplo de snippet a insertar inmediatamente después del `CREATE TABLE` de `vehicle_provider_bindings`:

```python
cur.execute(
    """
    ALTER TABLE vehicle_provider_bindings
    ADD COLUMN IF NOT EXISTS is_manual BOOLEAN NOT NULL DEFAULT FALSE;
    """
)
```

### 1.3. Eliminar el bootstrap hardcoded

Borrar completamente el archivo `backend/app/services/legacy_provider_bootstrap.py`. No dejar shim ni re-export.

Cambios acoplados:
- `backend/app/services/performance_providers.py`: borrar la línea `from app.services.legacy_provider_bootstrap import get_legacy_provider_vehicle_id` y la llamada dentro de `ArtimoMonthlyPerformanceProvider.calculate_database_rows` (ver fase 3.2).
- `grep -rn "legacy_provider_bootstrap" backend/` debe quedar vacío.

### 1.4. Eliminar la ruta `legacy_bindings` en `provider_config`

Editar `backend/app/services/provider_registry.py` → función `normalize_provider_config` (≈línea 131):

- Borrar el bloque `binding_source = raw.get("legacy_bindings", ...)` y el `normalized["legacy_bindings"] = normalized_bindings`.
- Borrar `"legacy_bindings_count": len(...)` dentro de `public_provider_config` (≈línea 202).

Estas ramas quedan inalcanzables tras la migración de datos (Fase 1.1) y mantenerlas invita a divergencia.

---

## FASE 2 — Backend: persistir y leer el binding manual

### 2.1. Schema Pydantic: extender la request de asignación

Editar `backend/app/schemas/vehicle.py` → clase `VehicleDatabaseAssignmentRequest` (≈línea 181).

Añadir campo:

```python
provider_vehicle_id: str | None = Field(
    default=None,
    max_length=128,
    description="ID externo del vehiculo para el proveedor GPS de la database asignada. Si es None se limpia el binding manual.",
)
```

### 2.2. Schema Pydantic: exponer el binding en `VehicleAssignmentRecord`

Editar `backend/app/schemas/vehicle.py` → clase `VehicleAssignmentRecord` (≈línea 136). Añadir campo:

```python
provider_vehicle_id: str | None = Field(
    default=None,
    description="ID externo manual del vehiculo para el proveedor GPS de la database asignada (si existe)",
)
is_provider_vehicle_id_manual: bool = Field(
    default=False,
    description="True si el provider_vehicle_id viene de un binding marcado como manual",
)
```

### 2.3. Incluir el binding manual en `list_vehicle_assignments`

Editar `backend/app/services/motor_catalog.py` → función `list_vehicle_assignments` (≈línea 1139).

En el `SELECT` principal (≈línea 1171), añadir un `LEFT JOIN LATERAL` contra `vehicle_provider_bindings` filtrando por `customer_database_id` y por `provider = cd.connection_type` (tras pasar por `infer_provider_key`). Problema: `infer_provider_key` vive en Python, no SQL. Estrategia:

1. Traer el JOIN “crudo” contra `vehicle_provider_bindings` con un predicado que matchee varios providers candidatos:

```sql
LEFT JOIN LATERAL (
    SELECT vpb.provider_vehicle_id, vpb.is_manual, vpb.binding_status
    FROM vehicle_provider_bindings vpb
    WHERE vpb.plate = a.plate
      AND vpb.customer_database_id = a.customer_database_id
    ORDER BY vpb.is_manual DESC, vpb.updated_at DESC
    LIMIT 1
) vpb ON TRUE
```

2. En el `SELECT`, añadir `vpb.provider_vehicle_id`, `vpb.is_manual AS provider_vehicle_id_is_manual`.

3. En el bucle Python que construye `VehicleAssignmentRecord` (≈línea 1227), añadir al `payload`:

```python
payload["provider_vehicle_id"] = row.get("provider_vehicle_id")
payload["is_provider_vehicle_id_manual"] = bool(row.get("provider_vehicle_id_is_manual"))
```

> **Nota de implementación:** el JOIN trae el último binding sin validar que el `provider` corresponda al `connection_type` real de la database. Como `vehicle_provider_bindings` es UNIQUE por `(plate, customer_database_id, provider)` y sólo hay un `connection_type` activo por database, en la práctica traerá la fila correcta. Si en el futuro conviven múltiples providers por database, filtrar por `vpb.provider = <resultado de infer_provider_key>` usando un `CASE` en SQL (no implementar ahora — YAGNI).

### 2.4. Persistir el binding manual en `assign_vehicle_database`

Editar `backend/app/services/motor_catalog.py` → función `assign_vehicle_database` (≈línea 2587).

Después del `UPDATE vehicle_motor_assignments ...` que asigna la database al vehículo (≈línea 2663), y **dentro del mismo bloque transaccional**, añadir lógica para el `provider_vehicle_id` del payload:

Pseudo-código paso a paso:

```python
# 1) Determinar el provider efectivo usando infer_provider_key.
#    Requiere importar: from app.services.provider_registry import infer_provider_key
provider_key = infer_provider_key(
    connection_type=selected_database.get("connection_type"),
    database_name=selected_database.get("database_name"),
    access_url=selected_database.get("access_url"),
    provider_config=None,  # no se necesita aqui
)

# 2) Normalizar el input.
raw_id = (payload.provider_vehicle_id or "").strip() if payload.provider_vehicle_id is not None else None

# 3) Tres casos:
#    (a) payload.provider_vehicle_id is None (no se envio el campo) -> no tocar bindings.
#    (b) raw_id == "" (se envio explicitamente vacio) -> borrar el binding manual.
#    (c) raw_id != "" -> upsert del binding con is_manual=TRUE.
if payload.provider_vehicle_id is None:
    pass  # no-op
elif raw_id == "":
    cur.execute(
        """
        DELETE FROM vehicle_provider_bindings
        WHERE plate = %s
          AND customer_database_id = %s
          AND provider = %s
          AND is_manual = TRUE;
        """,
        (normalized_plate, int(selected_database["id"]), provider_key),
    )
else:
    cur.execute(
        """
        INSERT INTO vehicle_provider_bindings (
            plate, customer_database_id, provider,
            provider_vehicle_id, binding_status,
            last_resolved_at, is_manual, updated_at
        )
        VALUES (%s, %s, %s, %s, 'resolved', NOW(), TRUE, NOW())
        ON CONFLICT (plate, customer_database_id, provider)
        DO UPDATE SET
            provider_vehicle_id = EXCLUDED.provider_vehicle_id,
            binding_status = 'resolved',
            is_manual = TRUE,
            last_resolved_at = NOW(),
            updated_at = NOW();
        """,
        (normalized_plate, int(selected_database["id"]), provider_key, raw_id),
    )
```

Insertar antes del `conn.commit()` existente (≈línea 2685). Asegurar que `vehicle_provider_bindings` existe: llamar a `_ensure_performance_tables(conn)` al inicio de la función (antes de cualquier `cur.execute`). Importar `from app.services.rendimientos import _ensure_performance_tables`. Si el ciclo de import se hace circular, exponer una pequeña función pública `ensure_performance_tables(conn)` en `rendimientos.py` y consumirla.

### 2.5. Caso sin database (customer_database_id vacío)

En el bloque anterior a la línea 2613 (`if not payload.customer_database_id:`) el payload también puede traer `provider_vehicle_id`. Ese caso **NO** persiste nada — ignorar el campo silenciosamente (no hay database → no hay binding posible). Comentar en el código: `# Sin database: ignoramos provider_vehicle_id (el binding requiere customer_database_id).`

### 2.6. Tests unitarios mínimos

Crear `backend/tests/test_provider_vehicle_bindings.py`. Tests esperados (usando el mismo estilo de `backend/tests/conftest.py`):

1. `test_assign_vehicle_database_with_provider_vehicle_id_inserts_manual_binding` — asigna database geotab + `provider_vehicle_id="b1234"`, verifica que existe una fila en `vehicle_provider_bindings` con `is_manual = TRUE`.
2. `test_assign_vehicle_database_with_empty_string_clears_manual_binding` — pre-insert binding manual, PUT con `provider_vehicle_id=""`, verifica que la fila manual fue borrada.
3. `test_assign_vehicle_database_with_null_preserves_binding` — pre-insert binding manual, PUT sin el campo (None), verifica que la fila sigue.
4. `test_list_vehicle_assignments_surfaces_manual_binding` — verifica que la respuesta incluye `provider_vehicle_id` y `is_provider_vehicle_id_manual = true`.

---

## FASE 3 — Backend: consumir el binding con prioridad

Objetivo: los 3 adapters en `performance_providers.py` respetan `is_manual = TRUE` sin re-resolver ni sobreescribir.

### 3.1. Nueva función helper en `performance_providers.py`

Añadir al inicio del módulo, justo después de los imports:

```python
def _select_binding(
    *,
    bindings: dict[tuple[str, int, str], "BindingSnapshot"],
    target: "PerformanceTarget",
) -> tuple[str | None, bool]:
    """Devuelve (provider_vehicle_id, is_manual) desde el mapa de bindings.

    - Si no hay binding para el target, devuelve (None, False).
    - is_manual solo es True cuando el binding vino marcado como manual.
    """
    snapshot = bindings.get((target.provider_key, target.customer_database_id, target.plate))
    if not snapshot:
        return None, False
    return snapshot.provider_vehicle_id, getattr(snapshot, "is_manual", False)
```

### 3.2. Extender `BindingSnapshot`

Editar `backend/app/services/performance_types.py`:

```python
@dataclass(frozen=True)
class BindingSnapshot:
    provider_vehicle_id: str | None
    binding_status: str
    is_manual: bool = False
```

### 3.3. Leer `is_manual` al cargar bindings

Editar `backend/app/services/rendimientos.py` → función `_load_binding_map` (≈línea 254):

- En el `SELECT` añadir `is_manual`.
- En el dict comprehension pasar `is_manual=bool(row.get("is_manual"))` al construir `BindingSnapshot`.

### 3.4. Nunca sobreescribir bindings manuales

Editar `backend/app/services/rendimientos.py` → función `_upsert_binding` (≈línea 283). Cambiar el `ON CONFLICT DO UPDATE` para que **preserve** `is_manual` y que no pise `provider_vehicle_id` cuando el registro existente ya es manual:

```sql
ON CONFLICT (plate, customer_database_id, provider)
DO UPDATE SET
    provider_vehicle_id = CASE
        WHEN vehicle_provider_bindings.is_manual THEN vehicle_provider_bindings.provider_vehicle_id
        ELSE EXCLUDED.provider_vehicle_id
    END,
    provider_plate = EXCLUDED.provider_plate,
    provider_customer_id = EXCLUDED.provider_customer_id,
    binding_status = CASE
        WHEN vehicle_provider_bindings.is_manual THEN 'resolved'
        ELSE EXCLUDED.binding_status
    END,
    last_resolved_at = EXCLUDED.last_resolved_at,
    last_error = CASE
        WHEN vehicle_provider_bindings.is_manual THEN NULL
        ELSE EXCLUDED.last_error
    END,
    -- is_manual intencionalmente NO se toca en auto-upserts.
    updated_at = NOW();
```

Es crítico: los adapters llaman `_upsert_binding` para persistir lo que auto-resolvieron. Si no se protege, pisa el manual.

### 3.5. Ajustar `ArtimoMonthlyPerformanceProvider`

Editar `backend/app/services/performance_providers.py` → `ArtimoMonthlyPerformanceProvider.calculate_database_rows` (≈línea 280).

Cambios exactos:

1. **Borrar** las líneas 285-290 (lectura de `legacy_bindings` desde `provider_config`).
2. **Borrar** `or get_legacy_provider_vehicle_id(target)` (línea 294) y el import correspondiente (`from app.services.legacy_provider_bootstrap import get_legacy_provider_vehicle_id` al inicio del archivo).
3. Reemplazar el bloque de resolución:

```python
bound_id, is_manual = _select_binding(bindings=bindings, target=target)
if is_manual:
    provider_vehicle_id = bound_id
else:
    provider_vehicle_id = (
        bound_id
        or extract_provider_vehicle_id(current_trip)
        or extract_provider_vehicle_id(previous_trip)
    )
```

4. Dentro del `BindingUpsert` exitoso (≈línea 318), si `is_manual=True` no hace falta persistir (ya está en DB con valor correcto); igual se llama `_upsert_binding` pero **el SQL del 3.4 protege contra sobreescritura**. Dejar la llamada tal cual; el SQL hace el trabajo.

### 3.6. Ajustar `GeotabMonthlyPerformanceProvider`

Editar `backend/app/services/performance_providers.py` → `GeotabMonthlyPerformanceProvider.calculate_database_rows` (≈línea 525).

Cambios exactos alrededor de la línea 551:

```python
bound_id, is_manual = _select_binding(bindings=bindings, target=target)
if is_manual:
    device_id = bound_id  # respeta el manual, no llama a find_device_by_plate
else:
    device_id = bound_id
    if not device_id:
        device = find_device_by_plate(api, target.plate)
        device_id = str(device.get("id") or "").strip() if device else None
```

El resto (if `not device_id: unbound`) no cambia.

### 3.7. Ajustar `FrotcomMonthlyPerformanceProvider`

Editar `backend/app/services/performance_providers.py` → `FrotcomMonthlyPerformanceProvider.calculate_database_rows`.

Igual patrón que Geotab:

```python
bound_id, is_manual = _select_binding(bindings=bindings, target=target)
if is_manual:
    vehicle_id = bound_id  # no llama a list_frotcom_vehicles
else:
    vehicle_id = bound_id
    if not vehicle_id:
        # lógica actual de vehicles_cache + find_frotcom_vehicle_id_by_plate
        ...
```

### 3.8. Tests de prioridad

Crear `backend/tests/test_monthly_performance_manual_binding.py`:

1. `test_manual_binding_bypasses_geotab_lookup` — mockea `find_device_by_plate` para que devuelva un ID distinto del manual y assert que el adapter usa el manual. Verifica que `find_device_by_plate` no fue llamado (porque `is_manual=True` corta antes).
2. `test_auto_binding_does_not_clobber_manual` — pre-insert binding manual con `provider_vehicle_id='M1'`, corre el cálculo con un mock que devuelve `'AUTO'`, verifica en DB que el valor sigue siendo `'M1'` y `is_manual=TRUE`.
3. `test_artimo_without_legacy_bootstrap` — verifica que sin binding manual y sin `legacy_bindings` en config, Artimo cae a extraer desde `trips` (el comportamiento 4).

---

## FASE 4 — Frontend: mostrar y editar el ID externo

### 4.1. Extender el API cliente

Editar `frontend/src/api/vehicleApi.js` → función `assignVehicleDatabase` (≈línea 252). La firma actual ya acepta `payload` arbitrario, así que NO cambia el código del wrapper. Sí se documenta (JSDoc) que `payload.provider_vehicle_id` es opcional: `string | null | undefined`.

### 4.2. Utilidad compartida: ¿este provider usa ID manual?

Editar `frontend/src/features/customers/providerCatalog.js`. Añadir al final:

```javascript
const PROVIDERS_WITH_MANUAL_ID = new Set(["geotab", "artimo", "frotcom"]);

export function providerSupportsManualVehicleId(connectionType) {
  return PROVIDERS_WITH_MANUAL_ID.has(connectionType);
}
```

### 4.3. Campo nuevo en `VehicleAssignmentModal`

Editar `frontend/src/features/vehicles/components/VehicleAssignmentModal.jsx`:

1. Importar el helper:
   ```javascript
   import {
     getDatabaseTypeLabel,
     providerSupportsManualVehicleId,
   } from "../../customers/providerCatalog";
   ```

2. Añadir estado local (junto a los demás `useState` al inicio del componente, ≈línea 57):
   ```javascript
   const [providerVehicleId, setProviderVehicleId] = useState("");
   const [providerVehicleIdTouched, setProviderVehicleIdTouched] = useState(false);
   ```

3. En el `useEffect` de `open` (≈línea 61), resetear y precargar:
   ```javascript
   setProviderVehicleId(vehicle?.provider_vehicle_id || "");
   setProviderVehicleIdTouched(false);
   ```

4. Añadir memo para el provider efectivo de la database seleccionada (después de `availableDatabases`, ≈línea 103):
   ```javascript
   const selectedDatabase = useMemo(
     () => availableDatabases.find((db) => String(db.id) === selectedDatabaseId) || null,
     [availableDatabases, selectedDatabaseId]
   );
   const showProviderVehicleIdField = !!selectedDatabase &&
     providerSupportsManualVehicleId(selectedDatabase.connection_type);
   ```

5. En `handleSubmit` (≈línea 128), adjuntar el campo al payload sólo si el usuario lo tocó o si hay cambio respecto al inicial:
   ```javascript
   const shouldSendProviderId =
     providerVehicleIdTouched ||
     providerVehicleId.trim() !== (vehicle?.provider_vehicle_id || "");
   const basePayload = {
     /* ... los campos existentes ... */
     customer_database_id: selectedDatabaseId ? Number(selectedDatabaseId) : null,
   };
   if (shouldSendProviderId && showProviderVehicleIdField) {
     basePayload.provider_vehicle_id = providerVehicleId.trim() || null;
   }
   await onSubmit(basePayload);
   ```
   Aplicar lo mismo en ambos branches del `if (motors.length > 0 && motorMode === "existing" && ...)`.

6. Renderizar el campo dentro del `<form>`, justo después del `<div className="form-field">` que contiene el selector de database (≈línea 356, después del `</div>` del campo de database). Mostrarlo sólo si `showProviderVehicleIdField`:

```jsx
{showProviderVehicleIdField ? (
  <div className="form-field">
    <label htmlFor="assign-provider-vehicle-id">
      ID externo del vehiculo <span className="form-optional">(opcional)</span>
    </label>
    <input
      id="assign-provider-vehicle-id"
      value={providerVehicleId}
      onChange={(event) => {
        setProviderVehicleId(event.target.value);
        setProviderVehicleIdTouched(true);
      }}
      placeholder={
        selectedDatabase.connection_type === "geotab"
          ? "Ej: b1234 (opcional; por defecto se busca por placa)"
          : "Ej: 386804 (opcional; por defecto se resuelve por placa)"
      }
      autoComplete="off"
    />
    <small className="support-copy">
      Se usara tal cual para consultar {getDatabaseTypeLabel(selectedDatabase.connection_type)}.
      {vehicle?.is_provider_vehicle_id_manual
        ? " Este vehiculo tiene un ID manual guardado."
        : " Deja vacio para que se resuelva automaticamente por placa."}
    </small>
  </div>
) : null}
```

### 4.4. Mostrar el ID en la vista de detalle

Mismo archivo, en el bloque `<div className="data-grid">` (≈línea 183). Añadir un `<DataItem>` al final para que sea visible sin entrar en modo edición:

```jsx
{vehicle.provider_vehicle_id ? (
  <DataItem
    label={`ID externo${vehicle.is_provider_vehicle_id_manual ? " (manual)" : ""}`}
    value={vehicle.provider_vehicle_id}
  />
) : null}
```

### 4.5. Exponer el campo en `VehiclesPage` si se necesita

Editar `frontend/src/pages/VehiclesPage.jsx` → `handleUpdateVehicle` (≈línea 150). El payload que el modal entrega a `onSubmit` ahora puede traer `provider_vehicle_id`. Pasar-through sin modificar:

```javascript
await assignVehicleDatabase(selectedVehicle.plate, {
  customer_database_id: payload.customer_database_id,
  ...(Object.prototype.hasOwnProperty.call(payload, "provider_vehicle_id")
    ? { provider_vehicle_id: payload.provider_vehicle_id }
    : {}),
});
```

Esto garantiza que `undefined` se omite, `""` y `null` llegan explícitos al backend, y el backend distingue entre los 3 casos (Fase 2.4).

---

## FASE 5 — Regresión y limpieza

### 5.1. Ejecutar migración local

```bash
docker compose exec backend alembic upgrade head
```

Verificar en DB:
```sql
SELECT plate, provider, provider_vehicle_id, is_manual
FROM vehicle_provider_bindings
WHERE is_manual = TRUE
ORDER BY plate;
```
Debe mostrar las 10 placas TLK520..TLK529 si la database de Opperar/Artimo existe en ese entorno.

### 5.2. Correr tests

```bash
docker compose exec backend pytest backend/tests/test_provider_vehicle_bindings.py -v
docker compose exec backend pytest backend/tests/test_monthly_performance_manual_binding.py -v
```

### 5.3. Smoke test manual (UI)

1. Login como admin. Entrar a **Vehículos**, abrir cualquier vehículo asociado a una database Geotab.
2. Verificar que aparece el campo "ID externo del vehículo" bajo el selector de database.
3. Guardar con un valor inventado (p.ej. `b9999`). Recargar la lista — el vehículo muestra el `ID externo (manual): b9999` en la data-grid del modal.
4. Abrir Rendimientos, recalcular para esa placa. Verificar en logs que **no** se llamó `find_device_by_plate` para esa placa (añadir `_logger.debug` temporal si hace falta; **remover antes de commit**).
5. Volver al modal y vaciar el campo. Guardar. Recalcular rendimientos: ahora sí debe llamar la resolución automática.
6. Repetir para un vehículo Frotcom (usa `/v2/vehicles`) y Artimo (usa `extract_provider_vehicle_id`).

### 5.4. Búsqueda final de residuos

```bash
grep -rn "legacy_provider_bootstrap\|get_legacy_provider_vehicle_id\|legacy_bindings" backend/ frontend/
```
Debe devolver 0 matches. Si el archivo de plan (este documento) los menciona, es aceptable — ignorar `docs/`.

---

## Criterios de aceptación

- [ ] `vehicle_provider_bindings.is_manual` existe y los 10 TLK* están como manual tras `alembic upgrade head`.
- [ ] `backend/app/services/legacy_provider_bootstrap.py` fue borrado.
- [ ] `PUT /api/v1/vehicle/{plate}/database` acepta `provider_vehicle_id` y persiste con `is_manual=TRUE` (o borra si `""`).
- [ ] `GET /api/v1/vehicle` retorna `provider_vehicle_id` y `is_provider_vehicle_id_manual` para cada vehículo.
- [ ] Los 3 adapters (Geotab, Artimo, Frotcom) usan `is_manual` con máxima prioridad y no sobreescriben bindings manuales cuando hacen auto-resolución.
- [ ] El modal de vehículos muestra/edita el ID externo sólo para databases Geotab/Artimo/Frotcom, no para `database` genérico.
- [ ] Los tests nuevos (4 en Fase 2.6 + 3 en Fase 3.8) pasan.
- [ ] `grep -rn "legacy_bindings\|legacy_provider_bootstrap"` en `backend/` y `frontend/` devuelve 0 matches.

## Consideraciones fuera del alcance

- Soporte de múltiples proveedores por vehículo simultáneamente (p.ej. Geotab GPS + Frotcom CAN). La tabla lo permite (UNIQUE por provider), pero la UI sólo gestiona el provider de la database actualmente asignada. Dejar documentado para una iteración futura.
- Resolver automáticamente el binding al cambiar de database (p.ej. "mover" el binding Artimo cuando se reasigna a Frotcom). Actualmente cambiar la database no elimina bindings antiguos — quedan huérfanos. Aceptable: `ON DELETE CASCADE` ya limpia si se borra la database entera.
- Bulk edit (subir Excel con plate → provider_vehicle_id). No en este plan.
