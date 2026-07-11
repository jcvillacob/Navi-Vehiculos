from __future__ import annotations

from alembic import op


revision = "20260709_0001"
down_revision = "20260707_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (codename, description) VALUES
            ('cpk_cph.view', 'Ver CPK/CPH'),
            ('cpk_cph.manage', 'Gestionar cierres CPK/CPH')
        ON CONFLICT (codename) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role, permission) VALUES
            ('admin', 'cpk_cph.view'),
            ('admin', 'cpk_cph.manage'),
            ('editor', 'cpk_cph.view'),
            ('editor', 'cpk_cph.manage'),
            ('viewer', 'cpk_cph.view')
        ON CONFLICT (role, permission) DO NOTHING;
        """
    )
    # customers solo existe via DDL runtime (_ensure_motor_tables); en una base
    # recien migrada (tests) estas tablas las crea _ensure_cpk_tables al vuelo.
    op.execute(
        """
        DO $$
        BEGIN
        IF to_regclass('public.customers') IS NULL THEN
            RETURN;
        END IF;
        CREATE TABLE IF NOT EXISTS cpk_cph_reports (
            id BIGSERIAL PRIMARY KEY,
            customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            period_month TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            current_version INTEGER NOT NULL DEFAULT 0,
            created_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
            updated_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
            approved_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
            approved_at TIMESTAMPTZ NULL,
            reopened_from_version INTEGER NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (customer_id, period_month)
        );
        CREATE TABLE IF NOT EXISTS cpk_cph_report_versions (
            id BIGSERIAL PRIMARY KEY,
            report_id BIGINT NOT NULL REFERENCES cpk_cph_reports(id) ON DELETE CASCADE,
            version_number INTEGER NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            created_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
            approved_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
            approved_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (report_id, version_number)
        );
        CREATE TABLE IF NOT EXISTS cpk_cph_report_rows (
            id BIGSERIAL PRIMARY KEY,
            report_id BIGINT NOT NULL REFERENCES cpk_cph_reports(id) ON DELETE CASCADE,
            version_number INTEGER NOT NULL DEFAULT 0,
            plate VARCHAR(32) NOT NULL,
            cutoff_start_at TEXT NOT NULL,
            cutoff_end_at TEXT NOT NULL,
            cutoff_start_utc TEXT NULL,
            cutoff_end_utc TEXT NULL,
            client_name TEXT NULL,
            database_name TEXT NULL,
            source_provider TEXT NULL,
            provider_vehicle_id TEXT NULL,
            vocacional BOOLEAN NOT NULL DEFAULT FALSE,
            km_client DOUBLE PRECISION NULL,
            odo_start DOUBLE PRECISION NULL,
            odo_end DOUBLE PRECISION NULL,
            horo_start DOUBLE PRECISION NULL,
            horo_end DOUBLE PRECISION NULL,
            kms_ecm_geotab DOUBLE PRECISION NULL,
            kms_gps DOUBLE PRECISION NULL,
            hours_ecm DOUBLE PRECISION NULL,
            hours_gps DOUBLE PRECISION NULL,
            fuel_gallons DOUBLE PRECISION NULL,
            km_adjustment DOUBLE PRECISION NULL DEFAULT 0,
            hour_adjustment DOUBLE PRECISION NULL DEFAULT 0,
            kms_ecm_approved DOUBLE PRECISION NULL,
            hours_ecm_approved DOUBLE PRECISION NULL,
            km_difference DOUBLE PRECISION NULL,
            km_difference_pct DOUBLE PRECISION NULL,
            hour_difference DOUBLE PRECISION NULL,
            hour_difference_pct DOUBLE PRECISION NULL,
            calculation_status TEXT NOT NULL DEFAULT 'pending',
            warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
            correction_note TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_cpk_cph_report_rows_report_version
        ON cpk_cph_report_rows (report_id, version_number);
        CREATE INDEX IF NOT EXISTS idx_cpk_cph_reports_month_customer
        ON cpk_cph_reports (period_month, customer_id);
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cpk_cph_report_rows;")
    op.execute("DROP TABLE IF EXISTS cpk_cph_report_versions;")
    op.execute("DROP TABLE IF EXISTS cpk_cph_reports;")
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE permission IN ('cpk_cph.view', 'cpk_cph.manage');
        """
    )
    op.execute(
        """
        DELETE FROM permissions
        WHERE codename IN ('cpk_cph.view', 'cpk_cph.manage');
        """
    )
