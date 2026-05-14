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
from app.services.rendimientos import calculate_monthly_performance

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

    logger.info(
        "Starting daily performance calculation — months: %s (Colombia date: %s)",
        months_to_calculate,
        now.strftime("%Y-%m-%d"),
    )

    for month in months_to_calculate:
        logger.info("Calculating month %s ...", month)
        try:
            result = calculate_monthly_performance(
                MonthlyPerformanceCalculateRequest(
                    month=month,
                    force_recalculate=True,
                )
            )
            s = result.summary
            logger.info(
                "Month %s done — total: %d, calculated: %d, partial: %d, unbound: %d, no_data: %d, error: %d",
                month,
                s.total,
                s.calculated,
                s.partial,
                s.unbound,
                s.no_data,
                s.error,
            )
        except Exception:
            logger.exception("Failed to calculate month %s", month)

    logger.info("Daily performance calculation finished.")


if __name__ == "__main__":
    _run()
