from __future__ import annotations

from alembic import op


revision = "20260417_0001"
down_revision = "20260416_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (codename, description) VALUES
            ('engine_lookup.batch', 'Consultar motores en lote')
        ON CONFLICT (codename) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role, permission) VALUES
            ('admin', 'engine_lookup.batch'),
            ('editor', 'engine_lookup.batch')
        ON CONFLICT (role, permission) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE (role, permission) IN (
            ('admin', 'engine_lookup.batch'),
            ('editor', 'engine_lookup.batch')
        );
        """
    )
    op.execute(
        """
        DELETE FROM permissions
        WHERE codename = 'engine_lookup.batch';
        """
    )
