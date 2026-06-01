from __future__ import annotations

from alembic import op


revision = "20260601_0002"
down_revision = "20260601_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE refresh_tokens
        ADD COLUMN IF NOT EXISTS user_agent TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE refresh_tokens
        DROP COLUMN IF EXISTS user_agent;
        """
    )
