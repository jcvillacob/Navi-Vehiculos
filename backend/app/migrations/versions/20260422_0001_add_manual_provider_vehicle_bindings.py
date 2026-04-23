from __future__ import annotations

from alembic import op
from sqlalchemy import text


revision = "20260422_0001"
down_revision = "20260417_0001"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return bool(bind.execute(text(f"SELECT to_regclass('public.{name}') IS NOT NULL")).scalar())


def upgrade() -> None:
    bind = op.get_bind()

    # customer_databases y vehicle_motor_assignments son creadas en runtime por el
    # bootstrap de la app (_ensure_performance_tables). Si aun no existen, el resto
    # de esta migracion no puede correr: la app las creara junto con is_manual al
    # primer arranque.
    if not _table_exists(bind, "customer_databases") or not _table_exists(
        bind, "vehicle_motor_assignments"
    ):
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_provider_bindings (
            id BIGSERIAL PRIMARY KEY,
            plate VARCHAR(10) NOT NULL,
            customer_database_id BIGINT NOT NULL REFERENCES customer_databases(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            provider_vehicle_id TEXT NULL,
            provider_plate TEXT NULL,
            provider_customer_id TEXT NULL,
            binding_status TEXT NOT NULL DEFAULT 'unknown',
            last_resolved_at TIMESTAMPTZ NULL,
            last_error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (plate, customer_database_id, provider)
        );
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'vehicle_provider_bindings_plate_fkey'
            ) THEN
                ALTER TABLE vehicle_provider_bindings
                ADD CONSTRAINT vehicle_provider_bindings_plate_fkey
                FOREIGN KEY (plate) REFERENCES vehicle_motor_assignments(plate) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        ALTER TABLE vehicle_provider_bindings
        ADD COLUMN IF NOT EXISTS is_manual BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )

    op.execute(
        """
        WITH target AS (
            SELECT cd.id AS database_id
            FROM customer_databases cd
            INNER JOIN customers c ON c.id = cd.customer_id
            WHERE LOWER(c.name) = 'opperar'
              AND LOWER(cd.database_name) LIKE '%artimo%'
            LIMIT 1
        ),
        pairs(plate, provider_vehicle_id) AS (
            VALUES
                ('TLK520', '68065b1e-f1a2-4510-b1b5-2fc9571b2b18'),
                ('TLK521', '44c21d53-2969-4b60-8d9e-22e837782ec5'),
                ('TLK522', 'c1140340-25f5-4dd4-835d-19dd78cd6059'),
                ('TLK523', '9d3c9acc-5bd9-47fd-a981-dd026087cd65'),
                ('TLK524', '1f4c222d-c79f-4ce2-b0b8-ce053f0f9469'),
                ('TLK525', '2406b72d-6a39-47e3-aaa7-60f50c14f696'),
                ('TLK526', 'a09748d5-d392-425f-8e5e-3090ada7632a'),
                ('TLK527', '0884ba93-f1a7-4639-98b6-17ab2bddedc4'),
                ('TLK528', '2063037e-4652-4d18-adb4-c1563e44c2b8'),
                ('TLK529', '53bc1c18-a38b-4bb1-a026-a2ea65916bab')
        )
        INSERT INTO vehicle_provider_bindings (
            plate, customer_database_id, provider,
            provider_vehicle_id, binding_status,
            last_resolved_at, is_manual
        )
        SELECT
            p.plate,
            t.database_id,
            'artimo',
            p.provider_vehicle_id,
            'resolved',
            NOW(),
            TRUE
        FROM pairs p
        CROSS JOIN target t
        WHERE EXISTS (SELECT 1 FROM target)
          AND EXISTS (
              SELECT 1 FROM vehicle_motor_assignments a WHERE a.plate = p.plate
          )
        ON CONFLICT (plate, customer_database_id, provider)
        DO UPDATE SET
            provider_vehicle_id = EXCLUDED.provider_vehicle_id,
            binding_status = 'resolved',
            is_manual = TRUE,
            last_resolved_at = NOW(),
            updated_at = NOW();
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "vehicle_provider_bindings"):
        return
    op.execute(
        """
        ALTER TABLE vehicle_provider_bindings
        DROP COLUMN IF EXISTS is_manual;
        """
    )
