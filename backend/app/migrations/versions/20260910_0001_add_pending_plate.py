"""Placa pendiente en `vehicle_motor_assignments`.

Una consulta por VIN que Fenix no resuelve a placa dejaba al vehiculo sin
registrar: se perdia el cliente, la database y las credenciales, y no aparecia
en el listado. Ahora se registra con una placa temporal (``P-000001``) marcada
``plate_pending``, y despues solo hay que completar la placa real.

- ``plate_pending BOOLEAN NOT NULL DEFAULT FALSE``.
- Secuencia ``vehicle_pending_plate_seq`` para las placas temporales.
- Todas las FK que apuntan a ``vehicle_motor_assignments(plate)`` se recrean
  con ``ON UPDATE CASCADE``: completar la placa es un UPDATE del PK y las
  tablas hijas (bindings, rendimientos, disponibilidad, logs) deben seguirlo
  sin perder historial.

La tabla la crea el bootstrap runtime (``motor_catalog._ensure_motor_tables``),
no una migracion: si aun no existe (entorno nuevo, DB de tests) se sale sin
hacer nada. El runtime replica esta DDL de forma idempotente.

Downgrade: quita el cascade y la secuencia; la columna se conserva a proposito
(tiene default y no rompe el codigo anterior).
"""
from __future__ import annotations

import logging

from alembic import op


revision = "20260910_0001"
down_revision = "20260902_0001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

TABLE = "vehicle_motor_assignments"
SEQUENCE = "vehicle_pending_plate_seq"

FK_QUERY = f"""
    SELECT
        con.conname,
        child.relname AS child_table,
        pg_get_constraintdef(con.oid) AS definition
    FROM pg_constraint con
    INNER JOIN pg_class child ON child.oid = con.conrelid
    INNER JOIN pg_class parent ON parent.oid = con.confrelid
    WHERE con.contype = 'f'
      AND parent.relname = '{TABLE}'
      AND con.confupdtype <> 'c';
"""


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

    bind.exec_driver_sql(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS plate_pending BOOLEAN NOT NULL DEFAULT FALSE;"
    )
    bind.exec_driver_sql(f"CREATE SEQUENCE IF NOT EXISTS {SEQUENCE};")

    stale = bind.exec_driver_sql(FK_QUERY).mappings().all()
    for constraint in stale:
        definition = constraint["definition"]
        if "ON UPDATE" in definition:
            # Alguien eligio otra accion a proposito: no la pisamos.
            logger.info(
                "%s: %s.%s ya declara ON UPDATE; se deja como esta",
                revision,
                constraint["child_table"],
                constraint["conname"],
            )
            continue
        child_table = constraint["child_table"]
        conname = constraint["conname"]
        bind.exec_driver_sql(f'ALTER TABLE {child_table} DROP CONSTRAINT "{conname}";')
        bind.exec_driver_sql(
            f'ALTER TABLE {child_table} ADD CONSTRAINT "{conname}" {definition} ON UPDATE CASCADE;'
        )
        logger.info(
            "%s: %s.%s recreada con ON UPDATE CASCADE", revision, child_table, conname
        )


def downgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind):
        return

    cascaded = bind.exec_driver_sql(
        f"""
        SELECT
            con.conname,
            child.relname AS child_table,
            pg_get_constraintdef(con.oid) AS definition
        FROM pg_constraint con
        INNER JOIN pg_class child ON child.oid = con.conrelid
        INNER JOIN pg_class parent ON parent.oid = con.confrelid
        WHERE con.contype = 'f'
          AND parent.relname = '{TABLE}'
          AND con.confupdtype = 'c';
        """
    ).mappings().all()

    for constraint in cascaded:
        definition = constraint["definition"].replace(" ON UPDATE CASCADE", "")
        child_table = constraint["child_table"]
        conname = constraint["conname"]
        bind.exec_driver_sql(f'ALTER TABLE {child_table} DROP CONSTRAINT "{conname}";')
        bind.exec_driver_sql(
            f'ALTER TABLE {child_table} ADD CONSTRAINT "{conname}" {definition};'
        )

    bind.exec_driver_sql(f"DROP SEQUENCE IF EXISTS {SEQUENCE};")
