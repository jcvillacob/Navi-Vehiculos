from __future__ import annotations

from alembic import op


revision = "20260416_0006"
down_revision = "20260416_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE refresh_tokens
        ADD COLUMN IF NOT EXISTS ip_address TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE refresh_tokens
        DROP COLUMN IF EXISTS ip_address;
        """
    )
