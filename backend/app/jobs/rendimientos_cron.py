"""
Daily performance calculation job.

Runs the monthly performance calculation for all eligible clients.
- Always calculates the current month.
- During the first days of the month, also recalculates the previous month
  first so late provider data and odometer/hourmeter carry-over stay current.

Usage:
    python -m app.jobs.rendimientos_cron
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from app.schemas.vehicle import MonthlyPerformanceCalculateRequest, PerformanceCalculationJob
from app.services.motor_catalog import save_connection_snapshot
from app.services.rendimientos_jobs import (
    JobAlreadyRunning,
    create_job,
    reap_stale_jobs,
    run_job,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [rendimientos-cron] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

COL_TZ_OFFSET = timezone(timedelta(hours=-5))

# Marcador estable para grep/alertas sobre los logs del cron.
ALERT_MARKER = "[ALERTA rendimientos]"
# Fraccion de placas en error a partir de la cual el mes se considera degradado.
ALERT_ERROR_RATIO = 0.2
# Frotcom y otros proveedores pueden consolidar viajes despues del primer
# cierre. Durante estos primeros dias se vuelve a calcular el mes anterior
# antes del actual para incorporar datos tardios y mantener la continuidad.
PREVIOUS_MONTH_REFRESH_DAYS = max(
    1, int(os.getenv("RENDIMIENTOS_PREVIOUS_MONTH_REFRESH_DAYS", "3"))
)


def _check_month_alert(month: str, final: PerformanceCalculationJob) -> bool:
    """
    Loggea ERROR con marcador `[ALERTA rendimientos]` si el job termino en
    error o si mas del 20% de las placas quedaron en error. Devuelve True si
    se emitio alerta (util para tests y para un futuro digest).
    """
    if final.status == "error":
        logger.error(
            "%s Month %s job=%s termino en error: %s",
            ALERT_MARKER,
            month,
            final.id,
            final.error_message,
        )
        return True
    s = final.summary
    if s is not None and s.total > 0 and (s.error / s.total) > ALERT_ERROR_RATIO:
        logger.error(
            "%s Month %s job=%s con %d/%d placas en error (%.0f%% > %.0f%%)",
            ALERT_MARKER,
            month,
            final.id,
            s.error,
            s.total,
            (s.error / s.total) * 100.0,
            ALERT_ERROR_RATIO * 100.0,
        )
        return True
    return False


def _months_to_calculate(now: datetime) -> list[str]:
    current_month = now.strftime("%Y-%m")
    months_to_calculate: list[str] = []
    if now.day <= PREVIOUS_MONTH_REFRESH_DAYS:
        first_of_current = now.replace(day=1)
        previous = first_of_current - timedelta(days=1)
        months_to_calculate.append(previous.strftime("%Y-%m"))
    months_to_calculate.append(current_month)
    return months_to_calculate


def _run() -> None:
    now = datetime.now(COL_TZ_OFFSET)
    months_to_calculate = _months_to_calculate(now)

    # --- Reaper de jobs huerfanos ---
    # Un job 'running' de un proceso muerto bloquearia el scope del mes (indice
    # unico) y create_job devolveria JobAlreadyRunning para siempre.
    try:
        reaped = reap_stale_jobs()
        if reaped:
            logger.warning("Reaper: %d job(s) huerfanos marcados como error antes del cron.", reaped)
    except Exception:
        logger.exception("Reaper de jobs huerfanos fallo — continuando con el cron")

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
            # No reejecutar: el job activo ya corre en otro worker (UI/BackgroundTask
            # o un cron anterior). Si estuviera huerfano, el reaper lo cierra y el
            # proximo cron lo recrea.
            logger.warning(
                "Month %s ya tiene un job activo (id=%s, status=%s); se omite este mes.",
                month,
                exc.job.id,
                exc.job.status,
            )
            continue
        except Exception:
            logger.exception("No fue posible crear el job para el mes %s", month)
            continue

        try:
            final = run_job(job.id)
        except Exception:
            logger.exception("%s Job %s fallo para el mes %s", ALERT_MARKER, job.id, month)
            continue

        _check_month_alert(month, final)

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
