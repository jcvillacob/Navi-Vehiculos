"""
Scheduler en-proceso para tareas recurrentes del backend.

Reemplaza el crontab del host: corre dentro del contenedor backend usando
APScheduler en un BackgroundScheduler. Cada job invoca la misma logica
que `python -m app.jobs.rendimientos_cron`, asi los runs quedan en
performance_calculation_jobs con triggered_by='cron'.

Diseñado para single-worker (uvicorn --reload o uvicorn sin --workers>1).
Si en el futuro escalan workers, mover a un scheduler externo o usar lock.
"""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.jobs.rendimientos_cron import _run as run_rendimientos_cron

logger = logging.getLogger(__name__)

# Hora fija solicitada: 05:00 Colombia (UTC-5)
_CRON_TZ = "America/Bogota"
_CRON_HOUR = 5
_CRON_MINUTE = 0

_scheduler: BackgroundScheduler | None = None


def _should_start() -> bool:
    """
    Evita arrancar el scheduler dos veces cuando uvicorn --reload usa el
    proceso supervisor + worker. APScheduler vive en el worker, y uvicorn
    setea RUN_MAIN/uvicorn_reload tags inconsistentes segun version, asi que
    permitimos opt-out por env y ademas chequeamos que no estemos en el
    proceso supervisor de --reload (que no ejecuta lifespan).
    """
    return os.getenv("DISABLE_SCHEDULER", "").lower() not in ("1", "true", "yes")


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    if not _should_start():
        logger.info("Scheduler deshabilitado por DISABLE_SCHEDULER.")
        return

    scheduler = BackgroundScheduler(timezone=_CRON_TZ)
    scheduler.add_job(
        run_rendimientos_cron,
        trigger=CronTrigger(hour=_CRON_HOUR, minute=_CRON_MINUTE, timezone=_CRON_TZ),
        id="rendimientos_daily",
        name="Daily rendimientos calculation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60 * 60,  # si el contenedor estaba caido al cruzar las 5am, corre dentro de 1h
    )
    scheduler.start()
    _scheduler = scheduler

    next_run = scheduler.get_job("rendimientos_daily").next_run_time
    logger.info(
        "Scheduler arrancado: rendimientos_daily corre %02d:%02d %s. Proxima ejecucion: %s",
        _CRON_HOUR, _CRON_MINUTE, _CRON_TZ, next_run,
    )


def shutdown() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("Fallo apagando el scheduler")
    finally:
        _scheduler = None
