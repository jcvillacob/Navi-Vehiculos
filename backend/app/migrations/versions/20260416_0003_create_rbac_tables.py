from __future__ import annotations

from alembic import op


revision = "20260416_0003"
down_revision = "20260416_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS permissions (
            id SERIAL PRIMARY KEY,
            codename TEXT NOT NULL UNIQUE,
            description TEXT
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS role_permissions (
            role TEXT NOT NULL,
            permission TEXT NOT NULL REFERENCES permissions(codename) ON DELETE CASCADE,
            PRIMARY KEY (role, permission)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS role_permissions;")
    op.execute("DROP TABLE IF EXISTS permissions;")
