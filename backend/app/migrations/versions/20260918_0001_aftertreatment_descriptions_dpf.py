"""Clasificacion de postratamiento segun las reglas reales de Geotab.

La lista original salia de una taxonomia teorica del postratamiento (DEF, SCR,
derates). Las reglas que Navitrans tiene configuradas son todas de DPF: tres
fallas J1939 (3251 presion diferencial, 5397 regeneracion demasiado frecuente,
3936 sistema DPF), dos de regeneracion manual y cinco escalones de saturacion
por carga de hollin.

El umbral va en el nombre porque es lo unico que distingue una regla de
saturacion de la siguiente; un motor con otros cortes agrega valores nuevos.

Se reemplaza el CHECK entero: la categoria es tan reciente que no hay ninguna
aplicacion registrada con los valores viejos.
"""
from __future__ import annotations

from alembic import op


revision = "20260918_0001"
down_revision = "20260917_0001"
branch_labels = None
depends_on = None


_LEGACY_DESCRIPTIONS = (
    "Nivel bajo de DEF",
    "Calidad de DEF",
    "Regeneracion DPF requerida",
    "Regeneracion DPF inhibida",
    "Nivel alto de hollin DPF",
    "Temperatura alta de escape",
    "Falla SCR o sensor NOx",
    "Derate por postratamiento",
)
_DESCRIPTIONS = (
    "Falla de presion diferencial DPF",
    "Regeneracion DPF demasiado frecuente",
    "Falla del sistema DPF",
    "Regeneracion manual activa",
    "Regeneracion manual inactiva con lampara DPF encendida",
    "Saturacion DPF 110%",
    "Saturacion DPF 120%",
    "Saturacion DPF 130%",
    "Saturacion DPF 144%",
    "Saturacion DPF 155%",
)
_SAFE_HABIT_DESCRIPTIONS = (
    "Excesos de velocidad",
    "Giros bruscos",
    "Excesos de RPM",
    "Frenadas bruscas",
    "Baches o Resaltos fuertes",
    "Aceleraciones bruscas",
)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _rebuild_check(aftertreatment: tuple[str, ...]) -> str:
    return f"""
        ALTER TABLE geotab_rule_applications
            DROP CONSTRAINT IF EXISTS ck_geotab_rule_app_description_by_category;
        ALTER TABLE geotab_rule_applications
        ADD CONSTRAINT ck_geotab_rule_app_description_by_category
        CHECK (
            description IS NULL
            OR (
                category = 'habito_seguro'
                AND description IN ({_in_list(_SAFE_HABIT_DESCRIPTIONS)})
            )
            OR (
                category = 'postratamiento'
                AND description IN ({_in_list(aftertreatment)})
            )
        );
    """


def upgrade() -> None:
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('geotab_rule_applications') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    # Ninguna aplicacion deberia tener los valores viejos, pero si un entorno
    # alcanzo a registrar una, se limpia la clasificacion en vez de fallar: la
    # regla sobrevive y se reclasifica desde la interfaz.
    op.execute(
        f"""
        UPDATE geotab_rule_applications
        SET description = NULL
        WHERE category = 'postratamiento'
          AND description IN ({_in_list(_LEGACY_DESCRIPTIONS)});
        """
    )
    op.execute(_rebuild_check(_DESCRIPTIONS))


def downgrade() -> None:
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('geotab_rule_applications') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    op.execute(
        f"""
        UPDATE geotab_rule_applications
        SET description = NULL
        WHERE category = 'postratamiento'
          AND description IN ({_in_list(_DESCRIPTIONS)});
        """
    )
    op.execute(_rebuild_check(_LEGACY_DESCRIPTIONS))
