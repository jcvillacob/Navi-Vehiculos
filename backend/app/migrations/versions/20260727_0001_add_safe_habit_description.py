"""Clasificacion explicita de aplicaciones de habito seguro.

Agrega `description` como enum cerrado y nullable. El nullable conserva las
aplicaciones historicas; las nuevas altas se validan en el servicio.
"""
from __future__ import annotations

from alembic import op


revision = "20260727_0001"
down_revision = "20260724_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('geotab_rule_applications') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    op.execute(
        """
        ALTER TABLE geotab_rule_applications
            ADD COLUMN IF NOT EXISTS description TEXT NULL;
        """
    )
    # Consolida el modelo historico: Exceso RPM se administra como banda de
    # operacion del motor y conserva una aplicacion derivada de habito seguro
    # para los consumidores de la API.
    op.execute(
        """
        INSERT INTO geotab_rule_applications (
            geotab_rule_id, category, motor_id, event_type,
            description, band, is_descenso
        )
        SELECT
            safe.geotab_rule_id, 'operacion', safe.motor_id, NULL,
            NULL, 'exceso_rpm', FALSE
        FROM geotab_rule_applications safe
        WHERE safe.category = 'habito_seguro'
          AND safe.event_type = 'exceso_rpm'
          AND safe.motor_id IS NOT NULL
        ON CONFLICT DO NOTHING;

        UPDATE geotab_rule_applications operation
        SET band = 'exceso_rpm', is_descenso = FALSE
        FROM geotab_rule_applications safe
        WHERE safe.geotab_rule_id = operation.geotab_rule_id
          AND safe.category = 'habito_seguro'
          AND safe.event_type = 'exceso_rpm'
          AND operation.category = 'operacion'
          AND operation.event_type IS NULL
          AND operation.motor_id = safe.motor_id;

        INSERT INTO geotab_rule_applications (
            geotab_rule_id, category, motor_id, event_type,
            description, band, is_descenso
        )
        SELECT
            operation.geotab_rule_id, 'habito_seguro', operation.motor_id,
            'exceso_rpm', 'Excesos de RPM', NULL, FALSE
        FROM geotab_rule_applications operation
        WHERE operation.category = 'operacion'
          AND operation.band = 'exceso_rpm'
          AND operation.motor_id IS NOT NULL
        ON CONFLICT DO NOTHING;

        UPDATE geotab_rule_applications safe
        SET description = 'Excesos de RPM', band = NULL, is_descenso = FALSE
        FROM geotab_rule_applications operation
        WHERE operation.geotab_rule_id = safe.geotab_rule_id
          AND operation.category = 'operacion'
          AND operation.band = 'exceso_rpm'
          AND safe.category = 'habito_seguro'
          AND safe.event_type = 'exceso_rpm'
          AND safe.motor_id = operation.motor_id;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rule_app_description_habito_only'
            ) THEN
                ALTER TABLE geotab_rule_applications
                ADD CONSTRAINT ck_geotab_rule_app_description_habito_only
                CHECK (description IS NULL OR category = 'habito_seguro');
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rule_app_description'
            ) THEN
                ALTER TABLE geotab_rule_applications
                ADD CONSTRAINT ck_geotab_rule_app_description
                CHECK (description IS NULL OR description IN (
                    'Excesos de velocidad', 'Giros bruscos', 'Excesos de RPM',
                    'Frenadas bruscas', 'Baches o Resaltos fuertes',
                    'Aceleraciones bruscas'
                ));
            END IF;
        END $$;
        """
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
            DROP CONSTRAINT IF EXISTS ck_geotab_rule_app_description,
            DROP CONSTRAINT IF EXISTS ck_geotab_rule_app_description_habito_only,
            DROP COLUMN IF EXISTS description;
        """
    )
