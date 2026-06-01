from __future__ import annotations

from alembic import op


revision = "20260601_0001"
down_revision = "20260521_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Tabla `roles` con seed de los tres roles de sistema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS roles (
            id SERIAL PRIMARY KEY,
            key TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            description TEXT,
            is_system BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute(
        """
        INSERT INTO roles (key, label, description, is_system) VALUES
            ('admin',  'Administrador', 'Acceso total al sistema.',            TRUE),
            ('editor', 'Editor',        'Puede editar catalogos y vehiculos.',  TRUE),
            ('viewer', 'Visualizador',  'Solo lectura.',                        TRUE)
        ON CONFLICT (key) DO NOTHING;
        """
    )

    # 2) Eliminar el CHECK del campo `role` en `users` (los valores válidos
    #    pasan a ser los `key` de la tabla `roles`).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'users'
                  AND constraint_name = 'users_role_check'
            ) THEN
                ALTER TABLE users DROP CONSTRAINT users_role_check;
            END IF;
        END
        $$;
        """
    )

    # 3) FK users.role -> roles.key (ON UPDATE CASCADE por si renombramos
    #    la key; ON DELETE RESTRICT para no perder trazabilidad).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'users'
                  AND constraint_name = 'users_role_fk'
            ) THEN
                ALTER TABLE users
                    ADD CONSTRAINT users_role_fk
                    FOREIGN KEY (role) REFERENCES roles(key)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT;
            END IF;
        END
        $$;
        """
    )

    # 4) FK role_permissions.role -> roles.key (ON DELETE CASCADE: si borramos
    #    un rol, se borra su matriz de permisos).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'role_permissions'
                  AND constraint_name = 'role_permissions_role_fk'
            ) THEN
                ALTER TABLE role_permissions
                    ADD CONSTRAINT role_permissions_role_fk
                    FOREIGN KEY (role) REFERENCES roles(key)
                    ON UPDATE CASCADE
                    ON DELETE CASCADE;
            END IF;
        END
        $$;
        """
    )

    # 5) Permiso `roles.manage` y asignación a admin.
    op.execute(
        """
        INSERT INTO permissions (codename, description)
        VALUES ('roles.manage', 'Gestionar roles y permisos')
        ON CONFLICT (codename) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role, permission) VALUES
            ('admin', 'roles.manage')
        ON CONFLICT (role, permission) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE permission = 'roles.manage';"
    )
    op.execute(
        "DELETE FROM permissions WHERE codename = 'roles.manage';"
    )

    op.execute(
        """
        ALTER TABLE role_permissions
        DROP CONSTRAINT IF EXISTS role_permissions_role_fk;
        """
    )

    op.execute(
        """
        ALTER TABLE users
        DROP CONSTRAINT IF EXISTS users_role_fk;
        """
    )

    op.execute(
        """
        ALTER TABLE users
        ADD CONSTRAINT users_role_check
        CHECK (role IN ('admin', 'editor', 'viewer'));
        """
    )

    op.execute("DROP TABLE IF EXISTS roles;")
