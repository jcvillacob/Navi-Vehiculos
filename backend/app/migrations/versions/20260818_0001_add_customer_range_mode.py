"""Modo de rangos por cliente (reglas | rpm).

Agrega `range_mode` a `customers`: define si Portal Clientes arma los rangos de
las flotas Geotab desde las reglas ('reglas', default) o desde los rangos de RPM
del motor ('rpm').
"""
from __future__ import annotations

from alembic import op


revision = "20260818_0001"
down_revision = "20260729_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # La tabla customers la crea el bootstrap runtime (_ensure_motor_tables), no
    # una migracion: en un entorno nuevo aun no existe cuando corre alembic.
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('customers') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    op.execute(
        """
        ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS range_mode TEXT NOT NULL DEFAULT 'reglas';
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_customers_range_mode'
            ) THEN
                ALTER TABLE customers
                ADD CONSTRAINT ck_customers_range_mode
                CHECK (range_mode IN ('reglas', 'rpm'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('customers') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    op.execute(
        "ALTER TABLE customers DROP CONSTRAINT IF EXISTS ck_customers_range_mode;"
    )
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS range_mode;")
