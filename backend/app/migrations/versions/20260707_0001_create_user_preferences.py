from __future__ import annotations

from alembic import op


revision = "20260707_0001"
down_revision = "20260601_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, key)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS user_preferences_user_id_idx
        ON user_preferences (user_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS user_preferences_user_id_idx;")
    op.execute("DROP TABLE IF EXISTS user_preferences;")
