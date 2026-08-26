"""Reconcilia Exceso de RPM como operacion y habito seguro derivado.

Una regla fisica de Exceso de RPM se consulta una sola vez en Geotab, pero tiene
dos usos semanticos: banda de operacion para rangos y habito seguro derivado.
Esta migracion repara datos legacy que conservaron solo el segundo uso.
"""

from __future__ import annotations

from alembic import op


revision = "20260826_0001"
down_revision = "20260818_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables_exist = bind.exec_driver_sql(
        """
        SELECT
            to_regclass('geotab_rules') IS NOT NULL
            AND to_regclass('geotab_rule_applications') IS NOT NULL
            AND to_regclass('customer_databases') IS NOT NULL
            AND to_regclass('customers') IS NOT NULL;
        """
    ).scalar()
    if not tables_exist:
        return

    op.execute(
        """
        UPDATE geotab_rules physical
        SET category = 'operacion'
        WHERE physical.category IS DISTINCT FROM 'operacion'
          AND EXISTS (
              SELECT 1
              FROM geotab_rule_applications safe
              WHERE safe.geotab_rule_id = physical.id
                AND safe.category = 'habito_seguro'
                AND safe.event_type = 'exceso_rpm'
                AND safe.motor_id IS NOT NULL
          );

        INSERT INTO geotab_rule_applications (
            geotab_rule_id, category, motor_id, event_type,
            description, band, is_descenso
        )
        SELECT
            safe.geotab_rule_id, 'operacion', safe.motor_id, NULL,
            NULL, 'exceso_rpm',
            POSITION('descenso' IN LOWER(physical.name)) > 0
        FROM geotab_rule_applications safe
        INNER JOIN geotab_rules physical ON physical.id = safe.geotab_rule_id
        WHERE safe.category = 'habito_seguro'
          AND safe.event_type = 'exceso_rpm'
          AND safe.motor_id IS NOT NULL
        ON CONFLICT DO NOTHING;

        UPDATE geotab_rule_applications operation
        SET band = 'exceso_rpm',
            is_descenso = operation.is_descenso
                OR POSITION('descenso' IN LOWER(physical.name)) > 0
        FROM geotab_rule_applications safe
        INNER JOIN geotab_rules physical ON physical.id = safe.geotab_rule_id
        WHERE safe.geotab_rule_id = operation.geotab_rule_id
          AND safe.category = 'habito_seguro'
          AND safe.event_type = 'exceso_rpm'
          AND safe.motor_id IS NOT NULL
          AND operation.category = 'operacion'
          AND operation.event_type IS NULL
          AND operation.motor_id = safe.motor_id
          AND (
              operation.band IS DISTINCT FROM 'exceso_rpm'
              OR (
                  POSITION('descenso' IN LOWER(physical.name)) > 0
                  AND NOT operation.is_descenso
              )
          );

        INSERT INTO geotab_rule_applications (
            geotab_rule_id, category, motor_id, event_type,
            description, band, is_descenso
        )
        SELECT
            operation.geotab_rule_id, 'habito_seguro', operation.motor_id,
            'exceso_rpm', 'Excesos de RPM', NULL, FALSE
        FROM geotab_rule_applications operation
        WHERE operation.category = 'operacion'
          AND operation.band = 'exceso_rpm'
          AND operation.motor_id IS NOT NULL
        ON CONFLICT DO NOTHING;

        UPDATE geotab_rule_applications safe
        SET description = 'Excesos de RPM', band = NULL, is_descenso = FALSE
        FROM geotab_rule_applications operation
        WHERE operation.geotab_rule_id = safe.geotab_rule_id
          AND operation.category = 'operacion'
          AND operation.band = 'exceso_rpm'
          AND operation.motor_id IS NOT NULL
          AND safe.category = 'habito_seguro'
          AND safe.event_type = 'exceso_rpm'
          AND safe.motor_id = operation.motor_id;

        -- El export incremental decide por customers.updated_at. Tocar solo los
        -- clientes afectados hace que la reparacion viaje aun sin un full sync.
        UPDATE customers c
        SET updated_at = NOW()
        WHERE c.id IN (
            SELECT DISTINCT cd.customer_id
            FROM customer_databases cd
            INNER JOIN geotab_rules gr ON gr.database_id = cd.id
            INNER JOIN geotab_rule_applications gra
                ON gra.geotab_rule_id = gr.id
            WHERE gra.category = 'operacion'
              AND gra.band = 'exceso_rpm'
              AND gra.motor_id IS NOT NULL
        );
        """
    )


def downgrade() -> None:
    # La reconciliacion corrige datos validos y no debe volver a eliminar una de
    # las dos aplicaciones al bajar la revision.
    pass
