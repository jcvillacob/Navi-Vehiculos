"""Exige motor en toda aplicacion de regla de operacion."""

from __future__ import annotations

from alembic import op


revision = "20260729_0001"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('geotab_rule_applications') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    # Repara aplicaciones legacy desde su grupo. Si ya existe la aplicacion
    # correcta para ese motor, descarta solo el duplicado sin motor.
    op.execute(
        """
        DELETE FROM geotab_rule_applications invalid
        USING geotab_rule_group_rules grgr, geotab_rule_groups grg
        WHERE invalid.geotab_rule_id = grgr.geotab_rule_id
          AND grgr.group_id = grg.id
          AND invalid.category = 'operacion'
          AND invalid.motor_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM geotab_rule_applications valid
              WHERE valid.geotab_rule_id = invalid.geotab_rule_id
                AND valid.category = 'operacion'
                AND valid.motor_id = grg.motor_id
                AND valid.event_type IS NOT DISTINCT FROM invalid.event_type
          );

        UPDATE geotab_rule_applications invalid
        SET motor_id = grg.motor_id
        FROM geotab_rule_group_rules grgr
        INNER JOIN geotab_rule_groups grg ON grg.id = grgr.group_id
        WHERE invalid.geotab_rule_id = grgr.geotab_rule_id
          AND invalid.category = 'operacion'
          AND invalid.motor_id IS NULL;

        DELETE FROM geotab_rule_applications
        WHERE category = 'operacion'
          AND motor_id IS NULL;

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rule_app_motor_by_category'
            ) THEN
                ALTER TABLE geotab_rule_applications
                ADD CONSTRAINT ck_geotab_rule_app_motor_by_category
                CHECK (category <> 'operacion' OR motor_id IS NOT NULL);
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
        DROP CONSTRAINT IF EXISTS ck_geotab_rule_app_motor_by_category;
        """
    )
