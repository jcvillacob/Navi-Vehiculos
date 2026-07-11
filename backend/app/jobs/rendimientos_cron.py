"""
Daily performance calculation job.

Runs the monthly performance calculation for all eligible clients.
- Always calculates the current month.
- On the 1st of the month, also calculates the previous month first
  (so odometer/horometer carry-over is up to date before the current month runs).

Usage:
    python -m app.jobs.rendimientos_cron
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone

from app.schemas.vehicle import MonthlyPerformanceCalculateRequest
from app.services.motor_catalog import save_connection_snapshot
from app.services.rendimientos_jobs import JobAlreadyRunning, create_job, run_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [rendimientos-cron] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

COL_TZ_OFFSET = timezone(timedelta(hours=-5))


def _run() -> None:
    now = datetime.now(COL_TZ_OFFSET)
    current_month = now.strftime("%Y-%m")
    is_first_day = now.day == 1

    months_to_calculate: list[str] = []

    if is_first_day:
        first_of_current = now.replace(day=1)
        previous = first_of_current - timedelta(days=1)
        months_to_calculate.append(previous.strftime("%Y-%m"))

    months_to_calculate.append(current_month)

    # --- Connection snapshot ---
    logger.info("Running Geotab connection snapshot for %s ...", now.strftime("%Y-%m-%d"))
    try:
        snap = save_connection_snapshot()
        logger.info(
            "Connection snapshot done — total: %d, connected: %d, disconnected: %d, not_found: %d, errors: %d",
            snap.get("total", 0),
            snap.get("connected", 0),
            snap.get("disconnected", 0),
            snap.get("not_found", 0),
            snap.get("errors", 0),
        )
    except Exception:
        logger.exception("Connection snapshot failed — continuing with performance calculation")

    # --- Performance calculation ---
    logger.info(
        "Starting daily performance calculation — months: %s (Colombia date: %s)",
        months_to_calculate,
        now.strftime("%Y-%m-%d"),
    )

    for month in months_to_calculate:
        logger.info("Calculating month %s (con disponibilidad) ...", month)
        # La fase de disponibilidad agrega ~2 min al job diario y mantiene fresco el dashboard de Disponibilidad.
        payload = MonthlyPerformanceCalculateRequest(month=month, force_recalculate=True, compute_availability=True)
        try:
            job = create_job(payload, triggered_by="cron", user_id=None)
            logger.info("Job %s created for month %s — running ...", job.id, month)
        except JobAlreadyRunning as exc:
            logger.warning(
                "Month %s ya tiene un job activo (id=%s, status=%s); reusando ese job.",
                month,
                exc.job.id,
                exc.job.status,
            )
            job = exc.job
        except Exception:
            logger.exception("No fue posible crear el job para el mes %s", month)
            continue

        try:
            final = run_job(job.id)
        except Exception:
            logger.exception("Job %s fallo para el mes %s", job.id, month)
            continue

        s = final.summary
        if s is None:
            logger.warning(
                "Month %s termino sin summary (estado=%s, error=%s)",
                month,
                final.status,
                final.error_message,
            )
            continue

        logger.info(
            "Month %s done — job=%s status=%s total: %d, calculated: %d, partial: %d, unbound: %d, no_data: %d, error: %d",
            month,
            job.id,
            final.status,
            s.total,
            s.calculated,
            s.partial,
            s.unbound,
            s.no_data,
            s.error,
        )

    logger.info("Daily performance calculation finished.")


if __name__ == "__main__":
    _run()
