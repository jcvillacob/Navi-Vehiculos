# Revision critica — fases 1 a 4 del plan `provider_vehicle_id` manual

## Alcance revisado

Se revisaron los cambios actuales contra `docs/plan-provider-vehicle-id-manual.md`, enfocando:

- migracion y backfill
- persistencia/lectura del binding manual
- prioridad de consumo en providers mensuales
- UI del modal de vehiculos
- tests agregados para fases 2 y 3

## Veredicto

La implementacion va bien encaminada en la idea base, pero **todavia no la cerraria**. Hay al menos:

- 2 regresiones funcionales reales
- 1 error frontend que deja el modal roto
- 1 bloque de tests de fase 3 que hoy no valida lo que afirma validar
- 1 riesgo de seguridad severo
- 1 drift de esquema entre Alembic y la creacion runtime de tablas

Debajo dejo los hallazgos en orden de severidad y la correccion propuesta con el mismo nivel de detalle.

---

## Hallazgo 1 — Se rompio el flujo existente de “sin database”

### Evidencia

Archivo: `backend/app/services/motor_catalog.py`

- `assign_vehicle_database` alrededor de `2608-2626`
- diff actual muestra que se elimino el bloque:

```python
if not payload.customer_database_id:
    ...
    UPDATE vehicle_motor_assignments
    SET access_url = %s,
        updated_at = NOW()
    WHERE plate = %s;
```

Y fue reemplazado por:

```python
# Sin database: ignoramos provider_vehicle_id (el binding requiere customer_database_id).
    conn.commit()
    return AssignedDatabaseSummary()
```

Eso deja el `return` dentro del `if existing_vehicle is None`, o sea: **inalcanzable** en el caso correcto y, peor aun, ya no existe el branch real para `customer_database_id = null`.

### Impacto

Esto es regresion funcional, no solo deuda:

- si el usuario guarda un vehiculo “sin database”, ya no corre la ruta esperada
- el codigo sigue hacia el `SELECT ... FROM customer_databases WHERE cd.id = %s` con `None`
- el resultado es `selected_database is None`
- el endpoint termina devolviendo `La database seleccionada no existe`

En otras palabras: se rompio la operacion previa de limpiar/quitar database.

### Correccion requerida

Reintroducir explicitamente el branch `if not payload.customer_database_id:` antes del `SELECT` de `customer_databases`.

### Correccion sugerida paso a paso

1. En `assign_vehicle_database`, despues de validar que el vehiculo existe, restaurar el `if not payload.customer_database_id:`.
2. Dentro de ese branch, volver a ejecutar el `UPDATE vehicle_motor_assignments` que ya existia antes del cambio.
3. Mantener el comentario nuevo sobre `provider_vehicle_id`, pero dentro del branch correcto:
   `# Sin database: ignoramos provider_vehicle_id (el binding requiere customer_database_id).`
4. Hacer `conn.commit()` y `return AssignedDatabaseSummary()` dentro de ese branch.
5. Verificar que no se tocan bindings manuales al dejar el vehiculo sin database. Eso es consistente con el out-of-scope documentado en el plan.

### Test minimo faltante

Agregar un test explicito:

- `test_assign_vehicle_database_without_database_preserves_existing_behavior`

Debe verificar que `PUT /api/v1/vehicle/{plate}/database` con `{"customer_database_id": null}`:

- responde `200`
- no intenta buscar una database
- actualiza `access_url` segun la semantica existente
- no falla con “La database seleccionada no existe”

---

## Hallazgo 2 — `VehicleAssignmentModal` quedo roto por referencias no declaradas

### Evidencia

Archivo: `frontend/src/features/vehicles/components/VehicleAssignmentModal.jsx`

- `103-109`: se usa `selectedDatabase` para calcular `showProviderVehicleIdField`
- `117-123`: se usa `availableDatabases`
- `368`: se itera `availableDatabases.map(...)`
- `392` y `399`: se vuelve a usar `selectedDatabase.connection_type`

Pero en el diff actual se borro:

```javascript
const availableDatabases = selectedCustomer?.databases || [];
```

Y no se agrego la declaracion de:

```javascript
const selectedDatabase = ...
```

### Impacto

Este cambio deja el modal inconsistente y potencialmente inutilizable:

- `availableDatabases` no existe
- `selectedDatabase` no existe
- el render y el submit dependen de ambas

Aunque no pude correr `vite build` en este entorno porque faltan dependencias instaladas, este archivo **no esta en un estado seguro** para frontend.

### Correccion requerida

Restaurar `availableDatabases` y declarar `selectedDatabase` exactamente como pedía el plan.

### Correccion sugerida paso a paso

1. Despues de `selectedCustomer`, reponer:

```javascript
const availableDatabases = selectedCustomer?.databases || [];
```

2. Agregar el memo faltante:

```javascript
const selectedDatabase = useMemo(
  () => availableDatabases.find((db) => String(db.id) === selectedDatabaseId) || null,
  [availableDatabases, selectedDatabaseId]
);
```

3. Dejar `showProviderVehicleIdField` como:

```javascript
const showProviderVehicleIdField =
  !!selectedDatabase && providerSupportsManualVehicleId(selectedDatabase.connection_type);
```

4. Verificar que el `useEffect` que valida `selectedDatabaseId` siga operando contra `availableDatabases`.
5. Revisar que el placeholder y el texto de ayuda no intenten acceder a `selectedDatabase.connection_type` cuando `selectedDatabase` sea `null`.

### Test/UI check minimo faltante

Hacer smoke manual del modal con tres casos:

- database `geotab` -> el campo aparece
- database `frotcom` -> el campo aparece
- database generica -> el campo no aparece

---

## Hallazgo 3 — Los tests de fase 3 no validan la ruta critica y hoy estan mal planteados

### Evidencia

Archivo: `backend/tests/test_monthly_performance_manual_binding.py`

#### 3.1. Falta import de `psycopg`

El test usa `psycopg.connect(...)` en `123`, pero el modulo no importa `psycopg` en la cabecera.

#### 3.2. `test_manual_binding_bypasses_geotab_lookup` no aisla dependencias externas

En `73-96` solo se parchea `find_device_by_plate`.

Pero `GeotabMonthlyPerformanceProvider.calculate_database_rows(...)` todavia llama a:

- `get_authenticated_client(...)`
- `_calculate_geotab_vehicle_record(...)`
- y dentro de ese flujo se consultan lecturas/trips

O sea: el test no esta verdaderamente unitario y puede depender de red o de SDKs externos.

#### 3.3. `test_auto_binding_does_not_clobber_manual` verifica persistencia en una capa que no persiste

En `98-137` el test:

- llama `provider.calculate_database_rows(...)`
- luego consulta `vehicle_provider_bindings` en la DB

Eso es conceptualmente incorrecto para esta capa. `calculate_database_rows(...)` solo retorna `binding_updates`; la persistencia sucede despues en `rendimientos.py` via `_upsert_binding(...)`.

En su estado actual, el test no demuestra “no clobber”; mezcla capa provider con capa de escritura a DB.

### Impacto

Los tests nuevos de fase 3 hoy no son una red de seguridad confiable:

- pueden fallar por motivos equivocados
- pueden no correr
- y aunque corran, no prueban la responsabilidad correcta

Eso debilita justo la parte mas sensible del cambio: la prioridad de `is_manual`.

### Correccion requerida

Separar tests por nivel:

- unitarios para `_select_binding` y el branching de cada provider
- integracion para la proteccion real de `_upsert_binding`

### Correccion sugerida paso a paso

#### 3.A. Arreglar imports basicos

Agregar en la cabecera:

```python
import psycopg
```

#### 3.B. Corregir `test_manual_binding_bypasses_geotab_lookup`

Parchear al menos:

- `get_authenticated_client`
- `_calculate_geotab_vehicle_record`
- `find_device_by_plate`

Y verificar:

- `find_device_by_plate` no fue llamado
- el record usa `provider_vehicle_id == "M1"`

Eso si prueba el branch correcto sin depender de llamadas externas.

#### 3.C. Rehacer `test_auto_binding_does_not_clobber_manual`

Hay dos opciones validas:

1. Testear `_upsert_binding(...)` directamente:
   - seed de fila manual en `vehicle_provider_bindings`
   - llamar `_upsert_binding(...)` con un valor auto distinto
   - verificar que el valor guardado sigue siendo el manual

2. O subir un nivel y testear `calculate_monthly_performance(...)` end-to-end:
   - seed de target + binding manual
   - mock del provider para devolver `BindingUpsert(provider_vehicle_id="AUTO", ...)`
   - verificar que en DB sigue `M1`

La opcion 1 es mas simple y mas alineada con la regla critica.

#### 3.D. Mantener `test_artimo_without_legacy_bootstrap`, pero cerrando el assertion relevante

Hoy solo verifica:

```python
assert result.records[0].calculation_status in ("calculated", "partial")
```

Eso es flojo. Deberia verificar ademas que el `provider_vehicle_id` del record o del `binding_update` proviene del `trip`, no de una ruta legacy.

---

## Hallazgo 4 — Se subieron credenciales reales de Frotcom al codigo fuente

### Evidencia

Archivo: `backend/app/clients/frotcom_client.py`

- `11`: `FROTCOM_USERNAME = "julian.sierra"`
- `12`: `FROTCOM_PASSWORD = "Navitrans.2025"`

### Impacto

Esto es severo:

- filtra credenciales en el repositorio
- expone acceso fuera del sistema
- obliga a rotar secretos
- dificulta separar entornos

Aunque el comentario funcional de Frotcom hable de “credenciales hardcoded”, **no deberia quedarse asi** en codigo versionado.

### Correccion requerida

Mover esas credenciales a configuracion segura.

### Correccion sugerida paso a paso

1. Llevar `FROTCOM_USERNAME` y `FROTCOM_PASSWORD` a `backend/app/core/config.py`.
2. Leerlas desde variables de entorno, por ejemplo:
   - `FROTCOM_USERNAME`
   - `FROTCOM_PASSWORD`
3. Hacer fail-fast al iniciar o al primer uso si faltan.
4. Actualizar `.env.example` con placeholders, nunca con valores reales.
5. Rotar inmediatamente las credenciales ya expuestas en este commit/worktree.

### Nota

Si por ahora necesitas mantener un provider global para Frotcom, eso es compatible con usar env vars. Lo que no es aceptable es persistir el secreto en el repo.

---

## Hallazgo 5 — Hay drift de esquema entre Alembic y `_ensure_performance_tables`

### Evidencia

Archivos:

- `backend/app/migrations/versions/20260422_0001_add_manual_provider_vehicle_bindings.py`
- `backend/app/services/rendimientos.py`

La migracion crea:

```sql
plate VARCHAR(10) NOT NULL
```

pero `_ensure_performance_tables` crea:

```sql
plate VARCHAR(10) NOT NULL REFERENCES vehicle_motor_assignments(plate) ON DELETE CASCADE
```

### Impacto

En una base fresca creada por Alembic:

- `vehicle_provider_bindings` queda sin FK hacia `vehicle_motor_assignments`
- no hay `ON DELETE CASCADE`
- se pierde integridad referencial respecto a la version runtime

Eso significa que el esquema real depende del camino por el que se haya creado la tabla, lo cual es exactamente lo que deberiamos evitar.

### Correccion requerida

Alinear Alembic y runtime para que el contrato de tabla sea uno solo.

### Correccion sugerida paso a paso

1. Corregir la migracion para agregar la FK faltante si la tabla fue creada por Alembic.
2. Si necesitas compatibilidad con tablas ya existentes, hacerlo con SQL condicional sobre `pg_constraint`.
3. Mantener `_ensure_performance_tables` alineado con la misma definicion.
4. Verificar que borrar una fila en `vehicle_motor_assignments` elimina sus bindings asociados.

### Validacion recomendada

Despues de ajustar la migracion:

```sql
SELECT conname
FROM pg_constraint
WHERE conrelid = 'vehicle_provider_bindings'::regclass;
```

Debe existir una FK hacia `vehicle_motor_assignments(plate)`.

---

## Hallazgo 6 — El contrato documentado de `provider_vehicle_id` no coincide con la implementacion

### Evidencia

Archivo: `backend/app/schemas/vehicle.py`

En `189-196` el campo se documenta asi:

```python
description="ID externo del vehiculo ... Si es None se limpia el binding manual."
```

Pero la implementacion real en `backend/app/services/motor_catalog.py` hace:

- `None` => no-op
- `""` => borra binding manual
- string no vacio => upsert manual

El JSDoc del frontend en `frontend/src/api/vehicleApi.js` ya describe correctamente la semantica real.

### Impacto

No rompe ejecucion, pero si rompe contrato:

- la documentacion OpenAPI queda falsa
- otro cliente podria enviar `null` esperando borrar el binding
- el comportamiento observado no coincidiria con el schema

### Correccion requerida

Actualizar la descripcion Pydantic para que refleje el contrato real.

### Texto sugerido

```python
description="ID externo del vehiculo para el proveedor GPS de la database asignada. None = no modificar el binding manual; string vacio = borrar binding manual; string no vacio = guardar binding manual."
```

---

## Estado por fase

### Fase 1

Parcialmente bien:

- `is_manual` si fue agregado
- se elimino `legacy_provider_bootstrap`
- se limpio `legacy_bindings`

Pendiente/corregir:

- alinear el esquema de la tabla entre Alembic y runtime

### Fase 2

Bien encaminada:

- schemas extendidos
- lectura del binding en `list_vehicle_assignments`
- persistencia manual en `assign_vehicle_database`

Pero no cerrada:

- se rompio el caso `customer_database_id = null`
- la documentacion del request quedo inconsistente

### Fase 3

La logica principal va en la direccion correcta:

- `BindingSnapshot` ahora carga `is_manual`
- `_upsert_binding` protege contra sobreescritura
- providers usan `_select_binding`

Pero no la daria por validada todavia:

- los tests nuevos no cubren correctamente la ruta critica
- Frotcom introduce un problema severo de manejo de secretos

### Fase 4

La intencion de UI esta bien y el pass-through en `VehiclesPage` esta correcto.

Pero el modal no esta listo:

- faltan `availableDatabases`
- falta `selectedDatabase`

Hasta arreglar eso, no cerraria la fase 4.

---

## Cierre recomendado antes de seguir a fase 5

Orden recomendado:

1. Arreglar la regresion de `assign_vehicle_database` para el caso “sin database”.
2. Corregir `VehicleAssignmentModal` reponiendo `availableDatabases` y `selectedDatabase`.
3. Rehacer los tests de fase 3 para que prueben la capa correcta.
4. Sacar credenciales de Frotcom del repo y rotarlas.
5. Alinear el esquema Alembic/runtime de `vehicle_provider_bindings`.
6. Ajustar la descripcion del schema Pydantic.

Con esos seis puntos, la base quedaria mucho mas confiable para pasar a smoke tests y cierre.
