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
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.jobs.rendimientos_cron import _run as run_rendimientos_cron
from app.services.auth_service import cleanup_expired_refresh_tokens
from app.services.geotab_taller import sweep_expired_grace as _sweep_taller_grace

logger = logging.getLogger(__name__)

# Hora fija solicitada: 05:00 Colombia (UTC-5)
_CRON_TZ = "America/Bogota"
_CRON_HOUR = 5
_CRON_MINUTE = 0
_CLEANUP_INTERVAL_MINUTES = 60
_TALLER_SWEEP_MINUTES = 5
_TALLER_PREWARM_MINUTES = 10

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
    scheduler.add_job(
        _safe_cleanup_refresh_tokens,
        trigger=IntervalTrigger(minutes=_CLEANUP_INTERVAL_MINUTES),
        id="refresh_tokens_cleanup",
        name="Cleanup expired/revoked refresh tokens",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _safe_sweep_taller_grace,
        trigger=IntervalTrigger(minutes=_TALLER_SWEEP_MINUTES),
        id="taller_grace_sweep",
        name="Sweep taller grace states older than TALLER_GRACE_HOURS",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _safe_prewarm_taller_orders,
        trigger=IntervalTrigger(minutes=_TALLER_PREWARM_MINUTES),
        id="taller_orders_prewarm",
        name="Pre-warm cache of active taller orders",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _safe_operational_alerts_digest,
        trigger=CronTrigger(hour=6, minute=0, timezone=_CRON_TZ),
        id="operational_alerts_digest",
        name="Daily operational alerts digest",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
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


def _safe_cleanup_refresh_tokens() -> int:
    """Wrapper que loggea excepciones del job periodico."""
    try:
        deleted = cleanup_expired_refresh_tokens()
        if deleted:
            logger.info("Limpieza de refresh_tokens: %d filas eliminadas.", deleted)
        return deleted
    except Exception:
        logger.exception("Fallo en cleanup de refresh_tokens")
        return 0


def _safe_sweep_taller_grace() -> int:
    """Wrapper que loggea excepciones del job periodico."""
    try:
        purged = _sweep_taller_grace()
        if purged:
            logger.info("Sweep taller grace: %d placas purgadas.", purged)
        return purged
    except Exception:
        logger.exception("Fallo en sweep de taller grace")
        return 0


def _safe_prewarm_taller_orders() -> dict[str, Any] | None:
    """
    Wrapper que mantiene caliente el cache de ordenes activas. Elimina los
    ~55s de primera carga de /ordenes-taller cuando un usuario abre la pagina.
    """
    try:
        from app.services.taller_ordenes import get_active_orders

        result = get_active_orders(force_refresh=True)
        summary = result.get("summary") or {}
        logger.info(
            "Pre-warm ordenes activas: %d ordenes en cache "
            "(overdue=%d, pending_closure_30d=%d).",
            summary.get("total_active", 0),
            summary.get("overdue", 0),
            summary.get("pending_closure_30d", 0),
        )
        return result
    except Exception:
        logger.exception("Fallo en pre-warm de ordenes activas")
        return None


def _safe_operational_alerts_digest() -> dict[str, Any] | None:
    """
    Wrapper del digest diario de alertas operativas. Corre a las 06:00 Bogota,
    despues del cron de rendimientos de las 05:00. v1 solo loggea; aqui se
    enchufaria el envio por SMTP en el futuro.
    """
    try:
        from app.services.operational_alerts import get_operational_alerts

        payload = get_operational_alerts()
        counts = payload.get("counts") or {}
        alerts = payload.get("alerts") or []
        logger.info(
            "Digest alertas operativas (%s): %d critical, %d warning.",
            payload.get("month"),
            counts.get("critical", 0),
            counts.get("warning", 0),
        )
        for alert in alerts:
            logger.info(
                " - [%s] %s: %s",
                alert.get("severity", "?").upper(),
                alert.get("title"),
                alert.get("detail"),
            )
        return payload
    except Exception:
        logger.exception("Fallo en digest de alertas operativas")
        return None
