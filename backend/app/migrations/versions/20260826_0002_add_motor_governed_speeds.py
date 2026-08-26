"""Velocidades gobernadas por motor (`motor_catalog`).

Dos datos de placa del motor que vienen de la hoja tecnica del fabricante:

- `governed_speed_rpm`: velocidad nominal gobernada sin carga (ej. X13E6 = 2100).
- `max_overspeed_rpm`: capacidad maxima de sobrevelocidad (ej. X13E6 = 2250).

Ambas son NULL mientras no se capturen: el consumidor debe tolerar su ausencia.
"""
from __future__ import annotations

from alembic import op


revision = "20260826_0002"
down_revision = "20260826_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # motor_catalog la crea el bootstrap runtime (_ensure_motor_tables), no una
    # migracion: en un entorno nuevo aun no existe cuando corre alembic.
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('motor_catalog') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    op.execute(
        """
        ALTER TABLE motor_catalog
        ADD COLUMN IF NOT EXISTS governed_speed_rpm INTEGER NULL;
        """
    )
    op.execute(
        """
        ALTER TABLE motor_catalog
        ADD COLUMN IF NOT EXISTS max_overspeed_rpm INTEGER NULL;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_motor_catalog_governed_speeds'
            ) THEN
                ALTER TABLE motor_catalog
                ADD CONSTRAINT ck_motor_catalog_governed_speeds
                CHECK (
                    (governed_speed_rpm IS NULL OR governed_speed_rpm > 0)
                    AND (max_overspeed_rpm IS NULL OR max_overspeed_rpm > 0)
                    AND (
                        governed_speed_rpm IS NULL
                        OR max_overspeed_rpm IS NULL
                        OR max_overspeed_rpm >= governed_speed_rpm
                    )
                );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE motor_catalog DROP CONSTRAINT IF EXISTS ck_motor_catalog_governed_speeds;"
    )
    op.execute("ALTER TABLE motor_catalog DROP COLUMN IF EXISTS max_overspeed_rpm;")
    op.execute("ALTER TABLE motor_catalog DROP COLUMN IF EXISTS governed_speed_rpm;")
