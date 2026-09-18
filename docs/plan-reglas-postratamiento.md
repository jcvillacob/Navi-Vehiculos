# Plan — Reglas de sistema de postratamiento (nueva categoría de reglas Geotab)

> Documento hermano: [contrato-integracion-portal-clientes.md](contrato-integracion-portal-clientes.md)
> (contrato que consume Portal Clientes; este plan lo extiende en §7).
>
> Estado: **ejecutado** el 2026-09-18. Fases 1 a 4 en este repo; fase 5 en
> `Navi-Portal-Clientes`, rama `feat/reglas-postratamiento` (puntos 1 a 4 de §8, sin
> mergear). Queda encender `INTEGRATION_EXPORT_POSTRATAMIENTO` tras ese despliegue y
> resolver el punto 5 de §8, el ETL. Las decisiones de §10 se resolvieron según la
> propuesta de este documento; §10.1 (el enum) sigue sujeta a validación del negocio.

## 1. Contexto y objetivo

Hoy una regla de Geotab registrada en Navi Vehículos pertenece a una de dos categorías:

| Categoría | Alcance | Clasificación explícita | Se exporta a Portal Clientes |
|---|---|---|---|
| `operacion` | por motor (`motor_id` obligatorio) | `band` / `is_descenso` (7 bandas de RPM) | sí |
| `habito_seguro` | global a la db (`motor_id` NULL) | `description` (enum cerrado de 6 hábitos) | sí |

Se necesita una **tercera categoría**: reglas del **sistema de postratamiento** (DEF/urea,
DPF, SCR, derates). No son hábitos del conductor ni bandas de RPM del motor: son
eventos del sistema de emisiones. Igual que las otras dos, se registran por su `rule_id`
nativo de Geotab en la pantalla de Clientes y viajan en el snapshot `/integration`
para que Portal Clientes extraiga los `ExceptionEvent` y los presente en sus reportes.

Slug de la categoría: **`postratamiento`** (sin acento, mismo estilo que `habito_seguro`).

## 2. Estado actual (lo que hay que tocar)

Todo el dominio de reglas vive en el bootstrap runtime, no en un modelo ORM:

| Pieza | Dónde | Detalle relevante |
|---|---|---|
| DDL + CHECKs | [motor_catalog.py](../backend/app/services/motor_catalog.py) `_run_motor_tables_ddl_inner` (~L1010-1145) | `geotab_rules.category` y `geotab_rule_applications.category` tienen CHECK `IN ('operacion','habito_seguro')`. `description` tiene dos CHECKs: solo `habito_seguro` y enum de 6 valores. Los CHECKs se crean con `IF NOT EXISTS` **por nombre**: cambiar el texto del CHECK sin cambiar el nombre no hace nada. |
| Índice único de scope | mismo archivo (~L1300) | `uq_geotab_rule_applications_scope (geotab_rule_id, category, COALESCE(motor_id,0), COALESCE(event_type,''))` → una aplicación por (regla, categoría, motor, evento). |
| Reconciliación de aplicaciones | mismo archivo (~L1355) | `INSERT ... WHERE (gr.category <> 'operacion' OR grg.motor_id IS NOT NULL)` crea la aplicación default de toda regla sin aplicación. Sirve tal cual para la nueva categoría. |
| Constantes y normalizadores | mismo archivo L162-215 | `RULE_CATEGORIES`, `SAFE_HABIT_DESCRIPTIONS`, `_normalize_rule_category`, `_normalize_safe_habit_description`, `_resolve_band_fields`. |
| Alta de regla | `create_geotab_rule` (~L4453) | Valida por categoría, inserta `geotab_rules` (si no existe en la db física ni en hermanas) + `geotab_rule_applications`, y hace UPDATE de `description` si la aplicación ya existía. |
| Edición de aplicación | `update_geotab_rule_application` (~L4620) | Rama `operacion` (banda) y rama `else` (= hábito seguro, cambia `description`). |
| Grupos de motor | `create_geotab_rule_group` (~L4845) | Rechaza reglas cuya `category != 'operacion'`; el mensaje dice "Reglas de habito seguro". |
| Schemas | [vehicle.py](../backend/app/schemas/vehicle.py) L690-790 | `GeotabRuleCreateRequest`, `GeotabRuleApplicationUpdateRequest`, `GeotabRuleApplicationRecord`, `GeotabRuleRecord`. `category` es `str` libre con descripción "'operacion' o 'habito_seguro'". |
| Rutas | [customer.py](../backend/app/api/routes/customer.py) L260-360 | `POST /customers/databases/{id}/rules`, `PATCH /customers/rules/applications/{id}`, resolve, inspection, delete. **No cambian.** |
| Export | [integration_export.py](../backend/app/services/integration_export.py) `_export_customers` (~L180-270) | Query `WHERE gra.category <> 'operacion' OR gra.motor_id IS NOT NULL`; ya incluiría la nueva categoría sin tocar nada. |
| Frontend | [CustomersPage.jsx](../frontend/src/pages/CustomersPage.jsx) | `SAFE_HABIT_DESCRIPTIONS` (L49), `formatRuleApplicationLabel` (L96), `RuleBandControl` (L1217), `SafeHabitControl` (L1289), formulario de alta (L1790-1905), memo `safeHabitRules` (L1409), sección "Reglas de hábito seguro" (L2256-2330). |
| API JS | [vehicleApi.js](../frontend/src/api/vehicleApi.js) L1014-1042 | `createGeotabRule`, `updateGeotabRuleApplication`. **No cambian.** |
| Tests | [test_portal_clientes_integration.py](../backend/tests/test_portal_clientes_integration.py) | Fixtures `geotab_db`, `rule_motor_id`, `_fake_inspection`; tests de categoría, enum de hábitos y snapshot. Requiere PostgreSQL real (`DATABASE_URL`). |
| Contrato | [contrato-integracion-portal-clientes.md](contrato-integracion-portal-clientes.md) §2.1, tabla de campos, §2.4, §3 | Enumera `operacion` / `habito_seguro` y el enum de `description`. |

### 2.1 Cómo lo consume Portal Clientes hoy (repo `Navi-Portal-Clientes`, fuera de este plan)

- `apps/api/app/models/master_data.py`: CHECK `ck_geotab_rule_app_category IN ('operacion','habito_seguro')` y CHECK de `description` con los 6 hábitos.
- `apps/api/app/services/sync_service.py`: valida y upserta aplicaciones; una categoría desconocida **rompe el upsert por el CHECK y el sync falla cerrado** (toda la réplica de reglas queda sin actualizar).
- `apps/web/src/lib/types.ts` L106: `category: 'operacion' | 'habito_seguro'`.
- `apps/web/src/lib/rule-visibility.ts`: filtra hábitos seguros administrativos.

**Consecuencia:** Navi Vehículos **no puede emitir `postratamiento` en el snapshot hasta que
Portal Clientes acepte la categoría**. Por eso el export va detrás de una bandera (§6).

## 3. Diseño

### 3.1 Modelo

Se reutiliza el modelo de `habito_seguro` en vez de inventar columnas nuevas:

| Campo de `geotab_rule_applications` | Valor para `postratamiento` |
|---|---|
| `category` | `'postratamiento'` |
| `motor_id` | `NULL` (global a la db, como los hábitos). Ver decisión §10.2 |
| `event_type` | `NULL` |
| `description` | **clasificación cerrada** de postratamiento (§3.2), obligatoria en el alta |
| `band` / `is_descenso` | `NULL` / `FALSE` (no aplican) |

Razones:
- `description` ya existe, ya se exporta y Portal Clientes ya la replica como "clasificación
  estable independiente del nombre de la regla". Cambiar su enum es un cambio aditivo.
- Una regla física (`geotab_rules`) puede tener aplicaciones en varias categorías; el índice
  único de scope garantiza **una** aplicación `postratamiento` por regla.
- Los grupos de motor siguen siendo exclusivos de `operacion` (no hay cambio en
  `geotab_rule_groups`).

### 3.2 Enum de clasificación (`AFTERTREATMENT_DESCRIPTIONS`)

Propuesta inicial en el mismo estilo (español, legible, cerrado). **Confirmar con el
negocio antes de implementar** (§10.1); el código debe centralizarlo en una sola tupla
para que cambiar la lista sea trivial.

```python
AFTERTREATMENT_DESCRIPTIONS = (
    "Nivel bajo de DEF",
    "Calidad de DEF",
    "Regeneracion DPF requerida",
    "Regeneracion DPF inhibida",
    "Nivel alto de hollin DPF",
    "Temperatura alta de escape",
    "Falla SCR o sensor NOx",
    "Derate por postratamiento",
)
```

(Sin acentos en los valores persistidos para evitar el gotcha de normalización que ya
tiene `_normalize_safe_habit_description` con `casefold`; el label visible en la UI puede
llevar acento si se prefiere, mapeando valor → etiqueta en el frontend.)

### 3.3 Constraints resultantes

```sql
-- categoría (ambas tablas)
CHECK (category IN ('operacion', 'habito_seguro', 'postratamiento'))

-- description ligada a la categoría (reemplaza a los dos CHECKs actuales)
CHECK (
    description IS NULL
    OR (category = 'habito_seguro' AND description IN (<6 hábitos>))
    OR (category = 'postratamiento' AND description IN (<enum §3.2>))
)

-- scope de postratamiento: global y sin banda
CHECK (category <> 'postratamiento' OR (motor_id IS NULL AND band IS NULL))
```

`description IS NULL` se conserva para las aplicaciones históricas de hábito seguro
(igual que hoy). El servicio exige `description` en el alta de ambas categorías.

## 4. Fase 1 — Backend: DDL, migración y servicio

### 4.1 Bootstrap DDL (`_run_motor_tables_ddl_inner`)

El bootstrap corre en cada `_ensure_motor_tables`, así que tiene que ser idempotente y
barato. Como los CHECKs actuales se crean con `IF NOT EXISTS` por nombre, hay que
**reemplazarlos** (drop del nombre viejo + add del nuevo) dentro de un `DO $$` que
consulte `pg_constraint`:

1. `ck_geotab_rules_category` → `DROP CONSTRAINT IF EXISTS` + re-crear con los 3 valores.
   Mismo tratamiento para `ck_geotab_rule_applications_category`. Para que el drop+add
   no ocurra en cada arranque, comprobar primero si el CHECK ya admite el valor
   (`pg_get_constraintdef(oid) LIKE '%postratamiento%'`) y saltar si es así.
2. `ck_geotab_rule_app_description_habito_only` y `ck_geotab_rule_app_description` →
   `DROP CONSTRAINT IF EXISTS` ambos; crear `ck_geotab_rule_app_description_by_category`
   con el CHECK de §3.3 (`IF NOT EXISTS` por el nombre nuevo).
3. Nuevo `ck_geotab_rule_app_postratamiento_scope` (`IF NOT EXISTS`).
4. Actualizar el comentario de `band` ("solo aplica a category 'operacion'; para
   'habito_seguro' y 'postratamiento' queda NULL").

### 4.2 Migración Alembic

`backend/app/migrations/versions/20260917_0001_add_aftertreatment_rule_category.py`,
`down_revision = "20260914_0001"`. Mismo patrón que `20260727_0001`:

- Guard `to_regclass('geotab_rules') IS NOT NULL AND to_regclass('geotab_rule_applications') IS NOT NULL`
  → `return` si falta (las tablas las crea el bootstrap, no una migración).
- `upgrade`: exactamente los mismos drop/add de §4.1 (compartir el SQL con el bootstrap
  vía constante módulo o duplicarlo literal; el repo hoy duplica).
- `downgrade`: `DELETE FROM geotab_rule_applications WHERE category = 'postratamiento'`,
  `DELETE FROM geotab_rules WHERE category = 'postratamiento'`, y restaurar los CHECKs
  originales por su nombre original (para que el bootstrap viejo los reconozca).

### 4.3 Servicio `motor_catalog.py`

- `RULE_CATEGORIES = ("operacion", "habito_seguro", "postratamiento")`.
- `AFTERTREATMENT_DESCRIPTIONS` (§3.2) junto a `SAFE_HABIT_DESCRIPTIONS`.
- `_normalize_rule_category`: mensaje de error con los 3 valores.
- Nuevo `_normalize_aftertreatment_description(value)` clonando el de hábitos (casefold,
  colapsar espacios, error "La clasificacion de postratamiento no es valida.").
  Alternativa más limpia: un único `_normalize_description(value, *, allowed, label)` y
  dos wrappers finos, para no duplicar.
- `create_geotab_rule`:
  - rama `postratamiento`: `description` obligatoria (`"Debe seleccionar la clasificacion
    de postratamiento."`), `motor_id` debe ser `None` (`"Las reglas de postratamiento se
    aplican a toda la database; no llevan motor."`), `band`/`is_descenso` deben venir
    vacíos (`"La banda de RPM solo aplica a reglas de operacion."`), `event_type = None`.
  - la validación actual `elif normalized_description is not None: raise "La clasificacion
    de habito seguro no aplica a reglas de operacion."` debe quedar solo para `operacion`.
  - el `UPDATE ... SET description` post-INSERT (rama `else`) hoy filtra
    `category = 'habito_seguro'`; parametrizar con `normalized_category` para que también
    sirva a `postratamiento`.
- `update_geotab_rule_application`:
  - la rama `else` (no operación) asume hábito seguro. Separar: si `category ==
    'postratamiento'` → validar `description` con el normalizador de postratamiento,
    rechazar `motor_id`/`band`/`is_descenso`, `UPDATE description`. La lógica especial de
    "Excesos de RPM" queda solo en hábito seguro.
- `create_geotab_rule_group`: el mensaje "Reglas de habito seguro: …" pasa a "Reglas que
  no son de operacion: …".
- `delete_geotab_rule` y `delete_geotab_rule_group`: sin cambios (borran por id / por
  `category = 'operacion'`).
- Revisar `grep -n "habito_seguro" backend/app/services/motor_catalog.py` completo al
  terminar: cualquier `!= 'operacion'` que implícitamente signifique "hábito seguro" debe
  distinguir ahora las dos categorías globales.

### 4.4 Schemas `vehicle.py`

- `GeotabRuleCreateRequest.category`, `GeotabRuleApplicationRecord.category`,
  `GeotabRuleRecord.category`: descripción "'operacion', 'habito_seguro' o 'postratamiento'".
- `GeotabRuleCreateRequest.description` y `GeotabRuleApplicationUpdateRequest.description`:
  "Clasificacion explicita (enum cerrado) para habito_seguro o postratamiento".
- Opcional: exponer los enums en un endpoint (`GET /customers/rules/catalog`) para que el
  frontend no duplique la lista. **No recomendado ahora**: el repo ya duplica
  `SAFE_HABIT_DESCRIPTIONS` y `RULE_BANDS` en el frontend; mantener el mismo patrón.

## 5. Fase 2 — Frontend (`CustomersPage.jsx`)

Objetivo: el usuario da de alta una regla eligiendo "Postratamiento", escoge su
clasificación, y la ve listada en una sección propia, editable igual que los hábitos.

1. Constantes: `AFTERTREATMENT_DESCRIPTIONS` (mismos valores que el backend) junto a
   `SAFE_HABIT_DESCRIPTIONS`. Opcional `AFTERTREATMENT_LABELS` valor → etiqueta con acentos.
2. `formatRuleApplicationLabel`: si `category === "postratamiento"` devolver
   `description || "Postratamiento"`.
3. Estado del formulario: `ruleAftertreatmentDescription` (o generalizar
   `ruleSafeHabitDescription` a `ruleDescription`, un solo estado que cambia de opciones
   según la categoría; **recomendado**, menos estados).
4. `useEffect([ruleCategory])`: limpiar motor, description, band, descenso al cambiar.
5. `<select aria-label="Categoria de la regla">`: tercera `<option value="postratamiento">
   Postratamiento</option>`.
6. Bloque condicional `ruleCategory === "postratamiento"`: un `<select required>` con las
   clasificaciones. Sin selector de motor ni banda.
7. `handleAddRule`: guard `postratamiento && !description → return`; payload
   `{ rule_id, category, motor_id: null, description, band: null, is_descenso: false }`.
   `disabled` del botón Agregar incluye la nueva condición.
8. Memo `aftertreatmentRules` (clon de `safeHabitRules` con `category === "postratamiento"`).
9. Nueva sección "Reglas de postratamiento" debajo de "Reglas de hábito seguro". La
   sección de hábitos son ~80 líneas de JSX; **extraer un componente `GlobalRulesSection`**
   (`title`, `items`, `openRuleKey`, `editingRuleKey`, control de edición, borrado) y usarlo
   dos veces en vez de copiar. Si se prefiere no refactorizar, copiar y ajustar `ruleKey`
   prefix (`aftertreatment-…`).
10. Editor inline: generalizar `SafeHabitControl` → recibir `options` y `category`
    (mantener la lógica de "Excesos de RPM"/motor solo cuando `category === "habito_seguro"`),
    o crear `AftertreatmentControl` mínimo (select + Guardar/Cancelar) que llame a
    `onUpdate(application.id, { description })`.
11. `motorGroupCards` no requiere cambios (salta aplicaciones sin `motor_id`).
12. CSS en `styles.css`: reutilizar `.rule-app-tag`. Opcional: variante
    `.rule-app-tag.is-postratamiento` con `var(--blue)` (#185979, reservado secundario) para
    distinguirla del tag de hábitos. Respetar la regla del proyecto: todo en `styles.css`.
    **Ejecución:** se reutilizó `.rule-app-tag` sin tocar `styles.css`, que tenía cambios
    sin commitear de otra sesión.

## 6. Fase 3 — Export a Portal Clientes (con bandera)

- Nueva variable de entorno `INTEGRATION_EXPORT_POSTRATAMIENTO` (`"1"`/`"true"` = on;
  default **off**). Leerla con `os.getenv` en `integration_export.py`, igual que
  `INTEGRATION_API_KEYS` en `core/dependencies.py`.
- En `_export_customers`, si la bandera está apagada añadir `AND gra.category <>
  'postratamiento'` al `WHERE` de la query de reglas. Con la bandera encendida no hay
  cambio: la query ya incluye toda categoría `<> 'operacion'`.
- Documentar la variable en `.env.example` (si existe) y en el contrato (§7).
- Cuando Portal Clientes despliegue su migración (§8), se enciende la bandera y se puede
  eliminar en un commit posterior.

## 7. Fase 4 — Contrato `docs/contrato-integracion-portal-clientes.md`

- §2.1 ejemplo: agregar una tercera regla:
  ```json
  {
    "id": 103, "rule_id": "aDef1Low", "name": "Nivel bajo de DEF",
    "category": "postratamiento", "motor_type": null, "event_type": null,
    "description": "Nivel bajo de DEF", "band": null, "is_descenso": false,
    "created_at": "2026-09-17T09:00:00Z"
  }
  ```
- Tabla de campos: `rules[].category` → `operacion` | `habito_seguro` | `postratamiento`;
  `rules[].description` → enum por categoría (listar los dos enums); `rules[].motor_type`
  → "las categorías globales (`habito_seguro`, `postratamiento`) traen `null`".
- §2.3 query SQL de resolución: `r.category IN ('habito_seguro','postratamiento')` en la
  rama "toda la db".
- §2.4: "`band` solo aplica a `operacion`; en `habito_seguro` y `postratamiento` siempre
  es `null`".
- Nueva §2.7 "Reglas de sistema de postratamiento": qué son, alcance global, enum de
  `description`, que el consumidor debe tolerar categorías desconocidas (fail-open por
  fila, no fail-closed de todo el sync), y la bandera de despliegue.
- §3 DDL sugerido: ampliar los CHECK de `category` y `description`.

## 8. Fase 5 (repo `Navi-Portal-Clientes`, **fuera de este plan**, solo para dejarlo anotado)

1. Migración Alembic: ampliar `ck_geotab_rule_app_category` (y el de `geotab_rules` si
   existe) y `ck_geotab_rule_app_description` / `..._habito_only`.
2. `sync_service.py`: `_normalize_safe_habit_description` → normalizador por categoría;
   idealmente **ignorar con warning** categorías desconocidas en vez de romper el sync.
3. `types.ts`: `category: 'operacion' | 'habito_seguro' | 'postratamiento'`.
4. `rule-visibility.ts`: helper `administrativeAftertreatmentRules`.
5. ETL/reportes: dataset o sección que extraiga `ExceptionEvent` de esas reglas y las
   presente (ya existen datasets `alertas` y `consumo_def` como referencia).
6. Encender `INTEGRATION_EXPORT_POSTRATAMIENTO` en Navi Vehículos una vez desplegado.

**Ejecución (2026-09-18).** Puntos 1 a 4 hechos en la rama `feat/reglas-postratamiento`
de `Navi-Portal-Clientes` (migración `c8d9e0f00067`, sync tolerante a categorías
desconocidas, tipos y tarjeta de base de datos), más su copia del contrato. El punto 5
**no** se tocó por dos razones: el ETL vive en `InformesRendimiento/`, que es **otro
repositorio** (`hagudelomnavi/InformesRendimiento`, ignorado en el `.gitignore` del
portal), y antes hay que decidir si los eventos de postratamiento aterrizan en
`fact_habito_event` o en un hecho propio, y cómo se reparten con los datasets `alertas`
y `consumo_def` que ya existen. Mezclarlos con hábitos seguros metería fallas de
emisiones dentro de indicadores de conducción del conductor. Mientras tanto el ETL los
ignora sin riesgo: sus consultas filtran por igualdad (`= 'operacion'`,
`= 'habito_seguro'`), nunca por negación.

## 9. Tests (backend, PostgreSQL real)

Agregar en `test_portal_clientes_integration.py` (fixtures existentes `geotab_db`,
`rule_motor_id`, `_fake_inspection`, `client`):

| Test | Verifica |
|---|---|
| `test_create_aftertreatment_rule` | alta con `category="postratamiento"`, `description="Nivel bajo de DEF"` → aplicación con `motor_id None`, `band None`, `description` correcta |
| `test_aftertreatment_rule_requires_description` | sin `description` → `ValueError` |
| `test_aftertreatment_rule_rejects_motor_band_and_safe_habit_description` | `motor_id`, `band`, o `description="Frenadas bruscas"` → `ValueError` |
| `test_aftertreatment_description_enum` (parametrizado) + rechazo de valor desconocido | normalizador |
| `test_update_aftertreatment_application_description` | `PATCH` cambia la clasificación; rechaza `band`/`motor_id` |
| `test_rule_group_rejects_aftertreatment_rules` | grupos de motor solo operación |
| `test_database_rejects_aftertreatment_with_motor` | INSERT directo con `motor_id` → `CheckViolation` (prueba el CHECK de scope) |
| `test_snapshot_hides_aftertreatment_without_flag` | export sin bandera → no aparece |
| `test_snapshot_exposes_aftertreatment_with_flag` | `monkeypatch.setenv(...)` → aparece con `category`, `description`, `motor_type null` |
| `test_legacy_constraints_replaced` | tras `_run_runtime_reconciliation()` no existen `ck_geotab_rule_app_description_habito_only` ni `ck_geotab_rule_app_description`; sí existe `ck_geotab_rule_app_description_by_category` y el CHECK de categoría admite `postratamiento` |

Actualizar el docstring de cabecera del archivo (lista de categorías). Correr también
`test_rule_bands.py` (no debería cambiar). Recordar que la suite tiene fallos
preexistentes ajenos (test_roles, test_provider_vehicle_bindings…) y que pytest en
paralelo contra la misma DB da falsos rojos (ver memoria "Revisión Rendimientos").

Frontend: no hay suite de tests de páginas; verificar manualmente en `/clientes` con una
db Geotab: alta de regla postratamiento, listado en la nueva sección, edición inline,
borrado, y que el alta de operación/hábito sigue igual.

## 10. Decisiones abiertas (confirmar antes o durante la ejecución)

1. **Enum de clasificación (§3.2).** Implementado tal cual; el negocio todavía debe
   validar nombres y cobertura (¿se separa "Nivel bajo de DEF" por etapa de inducement?
   ¿hace falta "Lampara MIL"?). Cambiar la lista es tocar `AFTERTREATMENT_DESCRIPTIONS`
   en el servicio, el CHECK del bootstrap, una migración nueva y la constante del
   frontend.
2. **Alcance global vs por motor.** Este plan las hace **globales** (`motor_id NULL`),
   como los hábitos seguros: los eventos de DEF/DPF son del vehículo y no dependen de las
   bandas del motor. Si más adelante se necesita por motor, basta relajar el CHECK de
   scope y reutilizar la lógica de `operacion`; no hay que rediseñar.
3. **Bandera de export vs orden de despliegue.** El plan usa bandera (§6) porque un
   valor desconocido rompe el sync completo de Portal Clientes. Alternativa: sin bandera
   y desplegar Portal primero; más simple pero frágil.
4. **Etiqueta visible.** "Postratamiento" (corta) vs "Sistema de postratamiento". El plan
   usa la corta en selects/tags y la larga en el título de la sección.

## 11. Orden de ejecución y commits sugeridos

Conventional Commits en español, **sin líneas de atribución al asistente** (regla del
repo).

1. `feat(reglas): admitir la categoria postratamiento en el modelo y el servicio` —
   §4.1, §4.2, §4.3, §4.4 + tests de servicio/constraints (§9).
2. `feat(clientes): registrar y editar reglas de postratamiento en la pantalla de clientes`
   — §5.
3. `feat(integracion): exportar las reglas de postratamiento detras de una bandera` —
   §6 + tests de snapshot.
4. `docs(integracion): reglas de postratamiento en el contrato de Portal Clientes` — §7.

Cada commit deja la app funcional por sí solo (el frontend sin el backend simplemente
recibe 409 al elegir la categoría nueva; el export sin bandera no cambia).

## 12. Gotchas para quien ejecute

- El bootstrap `_ensure_motor_tables` corre en **cada conexión**: cualquier `ALTER` nuevo
  debe estar protegido para no ejecutarse siempre (comprobar `pg_constraint` / definición).
- `IF NOT EXISTS` por nombre **no actualiza** un CHECK existente: hay que dropear el nombre
  viejo o usar nombre nuevo. Aplica tanto al bootstrap como a la migración.
- La migración debe llevar guard `to_regclass(...)` porque las tablas no las crea Alembic.
- Slug sin acento (`postratamiento`), valores del enum sin acento; el `casefold` del
  normalizador no quita tildes.
- `uq_geotab_rule_applications_scope` implica que registrar dos veces la misma regla como
  postratamiento hace `ON CONFLICT DO NOTHING` y luego el `UPDATE description`: es el
  comportamiento deseado (idempotente), no un error.
- Reglas compartidas entre databases hermanas (`_sibling_database_ids`): la regla física se
  busca en todas las hermanas; la aplicación nueva se cuelga de la existente. Sin cambios,
  pero probar el flujo con `sibling_geotab_db`.
- No tocar el repo `Navi-Portal-Clientes` desde esta tarea (§8 es solo referencia).
- Tests necesitan `DATABASE_URL` real; receta en la memoria "gotchas-repos-navi".
