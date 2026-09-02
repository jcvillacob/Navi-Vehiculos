from datetime import datetime

from app.jobs import rendimientos_cron as cron


def test_refreshes_previous_month_during_consolidation_window():
    now = datetime(2026, 9, 2, 5, 0, tzinfo=cron.COL_TZ_OFFSET)
    assert cron._months_to_calculate(now) == ["2026-08", "2026-09"]


def test_stops_refreshing_previous_month_after_window():
    now = datetime(
        2026,
        9,
        cron.PREVIOUS_MONTH_REFRESH_DAYS + 1,
        5,
        0,
        tzinfo=cron.COL_TZ_OFFSET,
    )
    assert cron._months_to_calculate(now) == ["2026-09"]
