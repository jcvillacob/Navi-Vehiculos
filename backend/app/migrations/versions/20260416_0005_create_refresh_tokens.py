from __future__ import annotations

from alembic import op


revision = "20260416_0005"
down_revision = "20260416_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id UUID PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked BOOLEAN NOT NULL DEFAULT FALSE
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS refresh_tokens_token_hash_idx
        ON refresh_tokens (token_hash);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS refresh_tokens_user_id_idx
        ON refresh_tokens (user_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS refresh_tokens_user_id_idx;")
    op.execute("DROP INDEX IF EXISTS refresh_tokens_token_hash_idx;")
    op.execute("DROP TABLE IF EXISTS refresh_tokens;")
