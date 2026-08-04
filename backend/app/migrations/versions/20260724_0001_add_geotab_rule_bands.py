"""Bandas de RPM explicitas en geotab_rule_applications.

Agrega `band` (enum cerrado, nullable) e `is_descenso` (boolean not null) a las
aplicaciones de reglas Geotab, con CHECKs, y backfillea las aplicaciones 'operacion'
existentes usando el sugeridor por palabra clave (app/services/rule_bands.py).
"""
from __future__ import annotations

from alembic import op

from app.services.rule_bands import suggest_band, suggest_is_descenso


revision = "20260724_0001"
down_revision = "20260715_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # La tabla geotab_rule_applications la crea el bootstrap runtime
    # (_ensure_motor_tables), no una migracion. En un entorno nuevo aun no existe
    # cuando corre alembic, y alli el propio _ensure_motor_tables agrega columnas y
    # CHECKs de forma idempotente. Por eso todo va guardado por to_regclass.
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('geotab_rule_applications') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    op.execute(
        """
        ALTER TABLE geotab_rule_applications
            ADD COLUMN IF NOT EXISTS band TEXT NULL,
            ADD COLUMN IF NOT EXISTS is_descenso BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rule_applications_band'
            ) THEN
                ALTER TABLE geotab_rule_applications
                ADD CONSTRAINT ck_geotab_rule_applications_band
                CHECK (band IS NULL OR band IN (
                    'rango_bajo', 'rango_economico', 'rango_balanceado',
                    'rango_potencia', 'rango_potencia_ineficiente',
                    'exceso_rpm', 'ralenti'
                ));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rule_applications_descenso'
            ) THEN
                ALTER TABLE geotab_rule_applications
                ADD CONSTRAINT ck_geotab_rule_applications_descenso
                CHECK (
                    is_descenso = FALSE
                    OR (band IS NOT NULL AND band <> 'ralenti')
                );
            END IF;
        END $$;
        """
    )

    # Backfill: solo aplicaciones 'operacion' aun sin banda. Se calcula en Python con
    # el sugeridor canonico para respetar el orden de prioridad de solapamientos.
    rows = bind.exec_driver_sql(
        """
        SELECT gra.id AS application_id, gr.name AS rule_name
        FROM geotab_rule_applications gra
        INNER JOIN geotab_rules gr ON gr.id = gra.geotab_rule_id
        WHERE gra.category = 'operacion'
          AND gra.band IS NULL;
        """
    ).fetchall()

    for application_id, rule_name in rows:
        band = suggest_band(rule_name)
        if band is None:
            continue  # no inventar: se queda sin banda
        is_descenso = suggest_is_descenso(rule_name, band)
        bind.exec_driver_sql(
            """
            UPDATE geotab_rule_applications
            SET band = %s, is_descenso = %s
            WHERE id = %s;
            """,
            (band, is_descenso, application_id),
        )


def downgrade() -> None:
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('geotab_rule_applications') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return
    op.execute(
        """
        ALTER TABLE geotab_rule_applications
            DROP CONSTRAINT IF EXISTS ck_geotab_rule_applications_descenso,
            DROP CONSTRAINT IF EXISTS ck_geotab_rule_applications_band,
            DROP COLUMN IF EXISTS is_descenso,
            DROP COLUMN IF EXISTS band;
        """
    )
