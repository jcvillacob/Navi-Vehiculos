from __future__ import annotations

from alembic import op


revision = "20260416_0004"
down_revision = "20260416_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (codename, description) VALUES
            ('dashboard.view', 'Ver dashboard'),
            ('motors.list', 'Listar motores'),
            ('motors.create', 'Crear motor'),
            ('motors.edit', 'Editar motor'),
            ('motors.delete', 'Eliminar motor'),
            ('motors.attachments', 'Gestionar adjuntos'),
            ('vehicles.list', 'Listar vehiculos'),
            ('vehicles.edit', 'Editar vehiculo'),
            ('vehicles.refresh', 'Refrescar datos Geotab'),
            ('customers.list', 'Listar clientes'),
            ('customers.create', 'Crear cliente'),
            ('customers.edit', 'Editar cliente y databases'),
            ('rendimientos.view', 'Ver rendimientos'),
            ('rendimientos.refresh', 'Refrescar rendimientos'),
            ('users.list', 'Listar usuarios'),
            ('users.create', 'Crear usuario'),
            ('users.edit', 'Editar usuario'),
            ('audit.view', 'Ver auditoria'),
            ('engine_lookup.use', 'Consultar motor por placa')
        ON CONFLICT (codename) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role, permission) VALUES
            ('admin', 'dashboard.view'),
            ('admin', 'motors.list'),
            ('admin', 'motors.create'),
            ('admin', 'motors.edit'),
            ('admin', 'motors.delete'),
            ('admin', 'motors.attachments'),
            ('admin', 'vehicles.list'),
            ('admin', 'vehicles.edit'),
            ('admin', 'vehicles.refresh'),
            ('admin', 'customers.list'),
            ('admin', 'customers.create'),
            ('admin', 'customers.edit'),
            ('admin', 'rendimientos.view'),
            ('admin', 'rendimientos.refresh'),
            ('admin', 'users.list'),
            ('admin', 'users.create'),
            ('admin', 'users.edit'),
            ('admin', 'audit.view'),
            ('admin', 'engine_lookup.use'),
            ('editor', 'dashboard.view'),
            ('editor', 'motors.list'),
            ('editor', 'motors.create'),
            ('editor', 'motors.edit'),
            ('editor', 'motors.attachments'),
            ('editor', 'vehicles.list'),
            ('editor', 'vehicles.edit'),
            ('editor', 'vehicles.refresh'),
            ('editor', 'customers.list'),
            ('editor', 'customers.create'),
            ('editor', 'customers.edit'),
            ('editor', 'rendimientos.view'),
            ('editor', 'rendimientos.refresh'),
            ('editor', 'engine_lookup.use'),
            ('viewer', 'dashboard.view'),
            ('viewer', 'motors.list'),
            ('viewer', 'vehicles.list'),
            ('viewer', 'customers.list'),
            ('viewer', 'rendimientos.view'),
            ('viewer', 'engine_lookup.use')
        ON CONFLICT (role, permission) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE (role, permission) IN (
            ('admin', 'dashboard.view'),
            ('admin', 'motors.list'),
            ('admin', 'motors.create'),
            ('admin', 'motors.edit'),
            ('admin', 'motors.delete'),
            ('admin', 'motors.attachments'),
            ('admin', 'vehicles.list'),
            ('admin', 'vehicles.edit'),
            ('admin', 'vehicles.refresh'),
            ('admin', 'customers.list'),
            ('admin', 'customers.create'),
            ('admin', 'customers.edit'),
            ('admin', 'rendimientos.view'),
            ('admin', 'rendimientos.refresh'),
            ('admin', 'users.list'),
            ('admin', 'users.create'),
            ('admin', 'users.edit'),
            ('admin', 'audit.view'),
            ('admin', 'engine_lookup.use'),
            ('editor', 'dashboard.view'),
            ('editor', 'motors.list'),
            ('editor', 'motors.create'),
            ('editor', 'motors.edit'),
            ('editor', 'motors.attachments'),
            ('editor', 'vehicles.list'),
            ('editor', 'vehicles.edit'),
            ('editor', 'vehicles.refresh'),
            ('editor', 'customers.list'),
            ('editor', 'customers.create'),
            ('editor', 'customers.edit'),
            ('editor', 'rendimientos.view'),
            ('editor', 'rendimientos.refresh'),
            ('editor', 'engine_lookup.use'),
            ('viewer', 'dashboard.view'),
            ('viewer', 'motors.list'),
            ('viewer', 'vehicles.list'),
            ('viewer', 'customers.list'),
            ('viewer', 'rendimientos.view'),
            ('viewer', 'engine_lookup.use')
        );
        """
    )
    op.execute(
        """
        DELETE FROM permissions
        WHERE codename IN (
            'dashboard.view',
            'motors.list',
            'motors.create',
            'motors.edit',
            'motors.delete',
            'motors.attachments',
            'vehicles.list',
            'vehicles.edit',
            'vehicles.refresh',
            'customers.list',
            'customers.create',
            'customers.edit',
            'rendimientos.view',
            'rendimientos.refresh',
            'users.list',
            'users.create',
            'users.edit',
            'audit.view',
            'engine_lookup.use'
        );
        """
    )
