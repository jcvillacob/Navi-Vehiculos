from __future__ import annotations

from alembic import op


revision = "20260715_0001"
down_revision = "20260709_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (codename, description) VALUES
            ('backups.list', 'Listar backups de PostgreSQL'),
            ('backups.create', 'Crear backup de PostgreSQL')
        ON CONFLICT (codename) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role, permission) VALUES
            ('admin', 'backups.list'),
            ('admin', 'backups.create')
        ON CONFLICT (role, permission) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE (role, permission) IN (
            ('admin', 'backups.list'),
            ('admin', 'backups.create')
        );
        """
    )
    op.execute(
        """
        DELETE FROM permissions
        WHERE codename IN ('backups.list', 'backups.create');
        """
    )
