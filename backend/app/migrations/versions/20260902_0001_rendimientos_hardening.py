"""Hardening de Rendimientos (`monthly_vehicle_performance`).

Agrega trazabilidad de fuentes y plausibilidad al registro mensual por placa:

- Columnas: ``*_source`` (de donde salio cada lectura de odometro/horometro),
  ``fuel_end``, ``validation_flags`` (banderas de plausibilidad), ``source_meta``,
  ``job_id`` (job que produjo el registro; sin FK porque los jobs se purgan),
  ``last_error`` e ``is_stale``.
- Indices de consulta: (period_month, customer_id), (plate, period_month), (job_id).
- CHECKs: estado valido, formato del periodo y metricas no negativas. Se crean
  ``NOT VALID`` y luego se intenta ``VALIDATE CONSTRAINT``; si hay filas legacy
  que violan la regla, la migracion NO falla: deja el constraint NOT VALID
  (aplica solo a filas nuevas/actualizadas) y lo registra en el log.

La tabla la crea el bootstrap runtime (``rendimientos._ensure_performance_tables``),
no una migracion: si aun no existe (entorno nuevo, DB de tests) se sale sin
hacer nada, igual que ``20260709_0002`` y ``20260826_0002``. El runtime debe
replicar estas columnas con ``ADD COLUMN IF NOT EXISTS`` para ese escenario.

Downgrade: elimina indices y constraints; las columnas se conservan a proposito
(son nullable o con default, no rompen el codigo anterior y evitan perder
datos de trazabilidad en un rollback).
"""
from __future__ import annotations

import logging

from alembic import op


revision = "20260902_0001"
down_revision = "20260826_0002"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

TABLE = "monthly_vehicle_performance"

INDEXES: dict[str, str] = {
    f"{TABLE}_period_customer_idx": "(period_month, customer_id)",
    f"{TABLE}_plate_period_idx": "(plate, period_month)",
    f"{TABLE}_job_idx": "(job_id)",
}

CONSTRAINTS: dict[str, str] = {
    "mvp_status_chk": (
        "CHECK (calculation_status IN ('calculated','partial','unbound','no_data','error'))"
    ),
    "mvp_period_chk": r"CHECK (period_month ~ '^\d{4}-(0[1-9]|1[0-2])$')",
    "mvp_nonneg_chk": (
        "CHECK ("
        "COALESCE(kms_ecm,0) >= 0 AND COALESCE(kms_gps,0) >= 0 "
        "AND COALESCE(hours_ecm,0) >= 0 AND COALESCE(hours_gps,0) >= 0 "
        "AND COALESCE(fuel_gallons,0) >= 0"
        ")"
    ),
}


def _table_exists(bind) -> bool:
    return bool(bind.exec_driver_sql(f"SELECT to_regclass('public.{TABLE}') IS NOT NULL;").scalar())


def _constraint_exists(bind, name: str) -> bool:
    return bool(
        bind.exec_driver_sql(
            "SELECT 1 FROM pg_constraint WHERE conname = %(name)s AND conrelid = %(table)s::regclass;",
            {"name": name, "table": TABLE},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind):
        logger.info("%s: tabla %s no existe todavia; se omite (la crea el runtime)", revision, TABLE)
        return

    # --- Columnas ---
    op.execute(
        f"""
        ALTER TABLE {TABLE}
            ADD COLUMN IF NOT EXISTS odo_start_source TEXT NULL,
            ADD COLUMN IF NOT EXISTS odo_end_source TEXT NULL,
            ADD COLUMN IF NOT EXISTS horo_start_source TEXT NULL,
            ADD COLUMN IF NOT EXISTS horo_end_source TEXT NULL,
            ADD COLUMN IF NOT EXISTS fuel_end DOUBLE PRECISION NULL,
            ADD COLUMN IF NOT EXISTS validation_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS source_meta JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ADD COLUMN IF NOT EXISTS job_id BIGINT NULL,
            ADD COLUMN IF NOT EXISTS last_error TEXT NULL,
            ADD COLUMN IF NOT EXISTS is_stale BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )

    # --- Indices ---
    for name, cols in INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {TABLE} {cols};")

    # --- Constraints: NOT VALID + VALIDATE tolerante a datos legacy ---
    for name, definition in CONSTRAINTS.items():
        if _constraint_exists(bind, name):
            continue
        op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT {name} {definition} NOT VALID;")
        # VALIDATE en un savepoint: si falla, el ALTER ADD ... NOT VALID de
        # arriba se conserva y la transaccion de la migracion sigue sana.
        savepoint = f"sp_validate_{name}"
        bind.exec_driver_sql(f"SAVEPOINT {savepoint};")
        try:
            bind.exec_driver_sql(f"ALTER TABLE {TABLE} VALIDATE CONSTRAINT {name};")
            bind.exec_driver_sql(f"RELEASE SAVEPOINT {savepoint};")
        except Exception as exc:  # noqa: BLE001 - nunca romper la migracion por datos legacy
            bind.exec_driver_sql(f"ROLLBACK TO SAVEPOINT {savepoint};")
            bind.exec_driver_sql(f"RELEASE SAVEPOINT {savepoint};")
            logger.warning(
                "%s: filas existentes violan %s; el constraint queda NOT VALID "
                "(aplica solo a filas nuevas). Corrige los datos y ejecuta "
                "'ALTER TABLE %s VALIDATE CONSTRAINT %s'. Detalle: %s",
                revision,
                name,
                TABLE,
                name,
                str(exc).splitlines()[0] if str(exc) else exc,
            )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind):
        return
    for name in CONSTRAINTS:
        op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name};")
    for name in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name};")
    # Las columnas se conservan deliberadamente (ver docstring).
