from __future__ import annotations

from alembic import op


revision = "20260709_0002"
down_revision = "20260709_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE IF EXISTS monthly_vehicle_performance
            ADD COLUMN IF NOT EXISTS geotab_regression_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS geotab_regression_total_km DOUBLE PRECISION NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS geotab_regression_total_hours DOUBLE PRECISION NOT NULL DEFAULT 0;

        ALTER TABLE IF EXISTS cpk_cph_report_rows
            ADD COLUMN IF NOT EXISTS geotab_regression_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS geotab_regression_total_km DOUBLE PRECISION NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS geotab_regression_total_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS suggested_adjustment DOUBLE PRECISION NOT NULL DEFAULT 0;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE IF EXISTS cpk_cph_report_rows
            DROP COLUMN IF EXISTS suggested_adjustment,
            DROP COLUMN IF EXISTS geotab_regression_total_hours,
            DROP COLUMN IF EXISTS geotab_regression_total_km,
            DROP COLUMN IF EXISTS geotab_regression_count;

        ALTER TABLE IF EXISTS monthly_vehicle_performance
            DROP COLUMN IF EXISTS geotab_regression_total_hours,
            DROP COLUMN IF EXISTS geotab_regression_total_km,
            DROP COLUMN IF EXISTS geotab_regression_count;
        """
    )
