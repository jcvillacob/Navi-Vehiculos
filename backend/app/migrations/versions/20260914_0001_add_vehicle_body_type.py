"""Overrides de carroceria y ejes en `vehicle_motor_assignments`.

La carroceria (tractocamion, volqueta, mixer, camion...) se deriva en lectura
del texto de ``nombre_vehiculo`` que Fenix ya entrega; ver
``app/services/vehicle_body_type.py``. Estas dos columnas guardan SOLO el
override manual:

- ``body_type TEXT NULL`` — NULL = usar la carroceria derivada del nombre.
- ``axle_config TEXT NULL`` — NULL = usar la configuracion de ejes derivada.

Asi los ~95 % de vehiculos que Fenix describe quedan clasificados sin backfill,
y el override cubre los usados sin ficha ("VEHICULO MARCA FOTON USADO") y los
vehiculos de otras marcas que nunca aparecen en Fenix.

La tabla la crea el bootstrap runtime (``motor_catalog._ensure_motor_tables``),
no una migracion: si aun no existe (entorno nuevo, DB de tests) se sale sin
hacer nada. El runtime replica esta DDL de forma idempotente.

Downgrade: elimina ambas columnas. Se pierden los overrides manuales; el valor
derivado se sigue calculando desde ``nombre_vehiculo``.
"""
from __future__ import annotations

import logging

from alembic import op


revision = "20260914_0001"
down_revision = "20260910_0001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

TABLE = "vehicle_motor_assignments"
COLUMNS = ("body_type", "axle_config")


def _table_exists(bind) -> bool:
    return bool(
        bind.exec_driver_sql(f"SELECT to_regclass('{TABLE}') IS NOT NULL;").scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind):
        logger.info(
            "%s: tabla %s no existe todavia; se omite (la crea el runtime)", revision, TABLE
        )
        return

    for column in COLUMNS:
        bind.exec_driver_sql(
            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {column} TEXT NULL;"
        )


def downgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind):
        return

    for column in COLUMNS:
        bind.exec_driver_sql(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {column};")
