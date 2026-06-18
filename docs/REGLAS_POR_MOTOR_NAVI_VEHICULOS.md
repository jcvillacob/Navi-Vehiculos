# Reglas de operación por tipo de motor — cambios en Navi Vehículos

> Espejo de lo ya implementado en **Portal Clientes** (rama `feat/reportes-analytics`).
> Navi Vehículos es la **fuente de verdad**: Portal Clientes solo replica vía el
> snapshot y resuelve el cruce. Para que coincidan, Navi Vehículos debe exponer
> `motor_type` en reglas y vehículos con el mismo vocabulario y reglas de validación.

## El problema

Un cliente puede tener **una sola database física** con vehículos de **distinto
tipo de motor**. Las reglas de **operación** (RPM, ralentí, etc.) dependen del
motor: la regla "RPM > 2200" de un ISD no aplica a un X15. Las de **hábito
seguro** (frenada/aceleración/velocidad) aplican a toda la db sin importar motor.

**Decisión:** el grano de una regla es `database_key` **+** `motor_type`. Cada
vehículo sabe su motor, cada regla `operacion` sabe a qué motor aplica, y la
consulta resuelve la intersección. **No se duplican reglas por vehículo.**

## Modelo de datos (lo que debe quedar en Navi Vehículos)

### 1. Catálogo controlado de motores
Tabla `motor_catalog` (o equivalente). Vocabulario único: `ISD`, `X15`, `B6.7`,
`OM906`, ... Tanto `geotab_rules.motor_type` como `vehicles.motor_type` deben ser
**FK a este catálogo** (`ON DELETE RESTRICT`). Motivo: si es texto libre, `"ISD"`
vs `"isd"` vs `"ISD "` rompe el cruce en silencio.

### 2. `geotab_rules.motor_type` (nuevo)
```sql
ALTER TABLE geotab_rules
  ADD COLUMN motor_type TEXT NULL
    REFERENCES motor_catalog(motor_type) ON DELETE RESTRICT;

-- unique pasa a incluir motor_type:
ALTER TABLE geotab_rules DROP CONSTRAINT <unique_actual>;
ALTER TABLE geotab_rules
  ADD CONSTRAINT uq_geotab_rule_db_rule_cat
  UNIQUE (database_id, rule_id, category, motor_type);

-- operacion => motor obligatorio; habito_seguro => motor NULL:
ALTER TABLE geotab_rules
  ADD CONSTRAINT ck_geotab_rule_motor_by_category CHECK (
    (category = 'operacion'     AND motor_type IS NOT NULL) OR
    (category = 'habito_seguro' AND motor_type IS NULL)
  );

CREATE INDEX idx_rules_motor_type ON geotab_rules (motor_type);
```

### 3. `vehicles.motor_type`
Debe ser **fuente de verdad en Navi Vehículos** y FK a `motor_catalog`. Hoy en
Portal Clientes es una extensión local del ETL; al venir en el snapshot, Portal
Clientes deja de inventarlo y solo lo replica.

```sql
ALTER TABLE vehicles
  ADD COLUMN motor_type TEXT NULL
    REFERENCES motor_catalog(motor_type) ON DELETE RESTRICT;
CREATE INDEX idx_vehicles_motor_type ON vehicles (motor_type);
```

## Contrato / snapshot (lo que se manda a Portal Clientes)

**Regla** — nuevo campo `motor_type`:
```json
{ "id": 101, "rule_id": "aB1cD2eF3gH", "name": "RPM > 2200 (ISD)",
  "category": "operacion", "motor_type": "ISD" }
{ "id": 102, "rule_id": "aX9yZ8wV7uT", "name": "Frenada brusca",
  "category": "habito_seguro", "motor_type": null }
```

**Vehículo** — nuevo campo `motor_type`:
```json
{ "plate": "ABC123", "customer_database_id": 31, "motor_type": "ISD" }
```

## Resolución (cómo se cruza, ya implementado en Portal Clientes)

```sql
SELECT r.*
FROM vehicles v
JOIN geotab_databases d   ON d.id = v.database_id
JOIN geotab_databases sib ON sib.database_key = d.database_key  -- §3.1 regla de oro
JOIN geotab_rules r       ON r.database_id = sib.id
WHERE r.is_active
  AND (
    r.category = 'habito_seguro'                                   -- toda la db
    OR (r.category = 'operacion' AND r.motor_type = v.motor_type)  -- solo su motor
  );
```

## Validaciones / operación (no omitir)

1. **UI de reglas en Navi Vehículos:** al crear regla `operacion`, exigir
   `motor_type` (dropdown del catálogo). En `habito_seguro` ocultarlo / forzar
   null. El check constraint lo respalda en DB.
2. **Owner de `vehicles.motor_type`:** definir **quién lo llena** en Navi
   Vehículos. Sin owner queda null y el vehículo no recibe reglas de operación.
3. **Reporte de huérfanos** (riesgo real del modelo):
   - Vehículos con `motor_type IS NULL` (solo reciben hábito seguro).
   - Vehículos cuyo `motor_type` **no tiene** ninguna regla `operacion` en su
     `database_key` (intersección vacía → cero reglas de operación, en silencio).

## Frontera con `motor_rules` / `rpm_rules` (config local del ETL)

Portal Clientes mantiene `motor_rules`/`rpm_rules`/`motor_catalog` como **config
de transformación del ETL de InformesRendimiento** — NO son la fuente de las
reglas aplicables. La verdad de "qué regla aplica a qué vehículo" es
`geotab_rules` (sync desde Navi Vehículos). No duplicar esa verdad en ambos lados.

## Estado en Navi Vehículos (lo realmente implementado)

> Navi Vehículos **ya** modela "qué regla aplica a qué motor", pero con un grano
> distinto al de Portal Clientes: en vez de una columna `geotab_rules.motor_type`,
> usa **grupos de motor** (`geotab_rule_groups(database_id, motor_id, …)` +
> `geotab_rule_group_rules`), donde `motor_id` → `motor_catalog` (que ya tiene
> `technical_number` único + `engine_name`). El vehículo conoce su motor por
> `vehicle_motor_assignments.technical_number` → `motor_catalog`.

Por eso **no se agregaron columnas `motor_type` ni check-constraints** (serían una
segunda fuente de verdad que pelea con los grupos). La corrección fue **exponer
`motor_type` en el snapshot**, derivándolo de lo que ya existe:

- **`motor_type` = `motor_catalog.engine_name`** (la familia: `ISD`, `X15`, …),
  normalizado con trim en ambos lados para que el cruce no se rompa por espacios.
- **Regla**: `engine_name` del motor del grupo al que pertenece la regla
  (`geotab_rule_group_rules` → `geotab_rule_groups.motor_id` → `motor_catalog`).
  `habito_seguro` y `operacion` aún sin grupo → `motor_type = null`.
- **Vehículo**: `engine_name` del motor cuyo `technical_number` coincide; `null` si
  el `technical_number` no está en el catálogo.

Implementado en `app/services/integration_export.py` (`_export_customers` para
reglas, `_export_vehicles` para vehículos, helper `_normalize_motor_type`). Cubierto
por `tests/test_portal_clientes_integration.py::test_snapshot_exposes_motor_type`.
El contrato actualizado vive en `docs/contrato-integracion-portal-clientes.md` §2.3.

**Owner de `vehicles.motor_type`** (§ Validaciones, punto 2): lo determina el
`technical_number` del vehículo + el alta del motor en el catálogo (`motor_catalog`).
Un vehículo cuyo `technical_number` no esté en el catálogo viaja con `motor_type =
null` (solo recibe hábito seguro) → es el caso huérfano a vigilar.

## Estado en Portal Clientes (referencia)

Ya hecho en esta rama:
- `geotab_rules.motor_type` + unique + 2 checks — migración
  `g7d4e6f80007_geotab_rules_motor_type.py`.
- `GeotabRuleRead.motor_type` (API) y `GeotabRule.motor_type` (web) + badge de
  motor en las reglas de operación.
- Seed demo `seed_demo_master_data.py`: catálogo de motores, vehículos con motor
  (ISD/X15/B6.7/OM906) y reglas de operación por motor. La base demo `el_roble_sa`
  mezcla ISD y X15; `WLP664` (OM906) demuestra el caso huérfano.
