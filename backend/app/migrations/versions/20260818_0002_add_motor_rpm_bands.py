"""Rangos de RPM por motor (`motor_rpm_bands`).

Los clientes en `range_mode = 'rpm'` no derivan las bandas de las reglas Geotab
sino de tramos del eje de revoluciones definidos por motor. Un motor sin filas
aqui queda "sin configurar": el consumidor debe saltarse esos vehiculos y
reportarlo como problema de calidad de datos, nunca inventar cortes.
"""
from __future__ import annotations

from alembic import op


revision = "20260818_0002"
down_revision = "20260818_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # motor_catalog la crea el bootstrap runtime (_ensure_motor_tables), no una
    # migracion: en un entorno nuevo aun no existe cuando corre alembic.
    bind = op.get_bind()
    table_exists = bind.exec_driver_sql(
        "SELECT to_regclass('motor_catalog') IS NOT NULL;"
    ).scalar()
    if not table_exists:
        return

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS motor_rpm_bands (
            id BIGSERIAL PRIMARY KEY,
            motor_id BIGINT NOT NULL REFERENCES motor_catalog(id) ON DELETE CASCADE,
            band TEXT NOT NULL,
            rpm_min INTEGER NOT NULL,
            rpm_max INTEGER NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_motor_rpm_bands_motor_band UNIQUE (motor_id, band),
            CONSTRAINT ck_motor_rpm_bands_band CHECK (band IN (
                'rango_bajo', 'rango_economico', 'rango_balanceado',
                'rango_potencia', 'rango_potencia_ineficiente', 'exceso_rpm'
            )),
            CONSTRAINT ck_motor_rpm_bands_min CHECK (rpm_min >= 0),
            CONSTRAINT ck_motor_rpm_bands_max CHECK (rpm_max IS NULL OR rpm_max > rpm_min)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_motor_rpm_bands_motor
            ON motor_rpm_bands (motor_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS motor_rpm_bands;")
