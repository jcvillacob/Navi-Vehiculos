from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "20260521_0001"
down_revision = "20260422_0001"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return bool(bind.execute(text(f"SELECT to_regclass('public.{name}') IS NOT NULL")).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    users_exists = _table_exists(bind, "users")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS performance_calculation_jobs (
            id BIGSERIAL PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','done','error')),
            month TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            customer_id BIGINT NULL,
            customer_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            customer_database_id BIGINT NULL,
            force_recalculate BOOLEAN NOT NULL DEFAULT TRUE,
            total_targets INTEGER NOT NULL DEFAULT 0,
            processed_targets INTEGER NOT NULL DEFAULT 0,
            summary JSONB NULL,
            error_message TEXT NULL,
            triggered_by TEXT NOT NULL DEFAULT 'ui',
            created_by_user_id BIGINT NULL{' REFERENCES users(id) ON DELETE SET NULL' if users_exists else ''},
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at TIMESTAMPTZ NULL,
            finished_at TIMESTAMPTZ NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS performance_calculation_jobs_active_unique
            ON performance_calculation_jobs (month, scope_key)
            WHERE status IN ('queued','running');
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS performance_calculation_jobs_status_idx
            ON performance_calculation_jobs (status, updated_at DESC);
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS performance_calculation_jobs_user_idx
            ON performance_calculation_jobs (created_by_user_id, created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS performance_calculation_jobs;")
