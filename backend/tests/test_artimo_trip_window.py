from __future__ import annotations

from datetime import datetime

from app.clients.artimo_client import (
    ArtimoClient,
    ArtimoConfig,
    select_trips_in_window,
)
from app.services.performance_providers import _calculate_vehicle_record
from app.services.performance_types import PerformanceTarget


def _client(**overrides) -> ArtimoClient:
    config = ArtimoConfig(
        username="u",
        password="p",
        customer_id="c",
        group_name="g",
        **overrides,
    )
    return ArtimoClient(config)


def _trip(start: str, end: str, *, odometer: float, horometer: float, distance: float, hours: float, liters: float):
    return {
        "plate": "TLK520",
        "startdate": start,
        "enddate": end,
        "odometer": str(odometer),
        "horometer": str(horometer),
        "distance": str(distance),
        "enginetime": str(hours),
        "consumption": str(liters),
    }


def _target() -> PerformanceTarget:
    return PerformanceTarget(
        customer_id=1,
        customer_database_id=1,
        client_name="Opperar",
        database_name="Ártimo",
        provider_key="artimo",
        provider_config={},
        username="u",
        password="p",
        plate="TLK520",
        technical_number=None,
        engine_name=None,
    )


def test_month_range_covers_the_whole_local_month():
    start, end = _client().get_month_range(2026, 6)

    assert start == "2026-06-01T05:00:00.000Z"
    assert end == "2026-07-01T04:59:59.999Z"


def test_month_range_rolls_over_december():
    start, end = _client().get_month_range(2026, 12)

    assert start == "2026-12-01T05:00:00.000Z"
    assert end == "2027-01-01T04:59:59.999Z"


def test_local_month_bounds_are_midnight_to_midnight():
    start_local, end_local = _client().get_local_month_bounds(2026, 6)

    assert start_local == datetime(2026, 6, 1, 0, 0)
    assert end_local == datetime(2026, 6, 30, 23, 59, 59, 999000)


def test_trip_lookback_widens_only_the_start():
    start, end = _client().get_trip_lookback_range(2026, 6, days=2)

    assert start == "2026-05-30T05:00:00.000Z"
    assert end == "2026-07-01T04:59:59.999Z"


def test_window_closes_with_the_last_trip_that_ends_inside_the_month():
    """El viaje que cruza la medianoche del 30 cierra en julio, no en junio."""
    rows = [
        _trip("2026-06-30 20:24:59", "2026-06-30 21:44:11", odometer=53535.58, horometer=1691.65, distance=53.64, hours=1.35, liters=22.5),
        _trip("2026-06-30 21:57:50", "2026-07-01 02:19:01", odometer=53685.06, horometer=1695.85, distance=149.48, hours=4.2, liters=72),
    ]

    window = select_trips_in_window(
        rows,
        window_start_local=datetime(2026, 6, 1),
        window_end_local=datetime(2026, 6, 30, 23, 59, 59, 999000),
    )

    assert window.odometer == 53535.58
    assert window.horometer == 1691.65
    assert window.trip_count == 1
    assert window.trips_after_window == 1


def test_window_adopts_the_trip_that_started_the_previous_month():
    """Reparto por fecha de fin: ningún kilómetro se pierde entre meses."""
    rows = [
        _trip("2026-05-31 22:00:00", "2026-06-01 03:00:00", odometer=47100, horometer=1505, distance=100, hours=5, liters=40),
        _trip("2026-06-02 08:00:00", "2026-06-02 10:00:00", odometer=47250, horometer=1507, distance=150, hours=2, liters=60),
    ]

    window = select_trips_in_window(
        rows,
        window_start_local=datetime(2026, 6, 1),
        window_end_local=datetime(2026, 6, 30, 23, 59, 59, 999000),
    )

    assert window.trip_count == 2
    assert window.distance_km == 250
    assert window.engine_hours == 7
    assert window.fuel_liters == 100
    assert window.odometer == 47250


def test_window_without_trips_reports_no_close():
    window = select_trips_in_window(
        [_trip("2026-06-30 21:57:50", "2026-07-01 02:19:01", odometer=1, horometer=1, distance=1, hours=1, liters=1)],
        window_start_local=datetime(2026, 6, 1),
        window_end_local=datetime(2026, 6, 30, 23, 59, 59, 999000),
    )

    assert window.close_trip is None
    assert window.odometer is None
    assert window.trips_after_window == 1


def test_record_uses_window_totals_and_warns_about_the_spilled_trip():
    rows = [
        _trip("2026-06-01 08:00:00", "2026-06-01 12:00:00", odometer=47500, horometer=1510, distance=300, hours=4, liters=120),
        _trip("2026-06-30 20:24:59", "2026-06-30 21:44:11", odometer=53535.58, horometer=1691.65, distance=53.64, hours=1.35, liters=22.5),
        _trip("2026-06-30 21:57:50", "2026-07-01 02:19:01", odometer=53685.06, horometer=1695.85, distance=149.48, hours=4.2, liters=72),
    ]
    window = select_trips_in_window(
        rows,
        window_start_local=datetime(2026, 6, 1),
        window_end_local=datetime(2026, 6, 30, 23, 59, 59, 999000),
    )
    previous_close = _trip(
        "2026-05-31 10:00:00", "2026-05-31 18:00:00", odometer=47671.637, horometer=1513.3, distance=200, hours=8, liters=80
    )

    record = _calculate_vehicle_record(
        target=_target(),
        month="2026-06",
        current_trip=window.close_trip,
        previous_trip=previous_close,
        previous_record=None,
        provider_vehicle_id="resource-1",
        gps_rows=[],
        trip_window=window,
    )

    assert record.odo_end == 53535.58
    assert record.horo_end == 1691.65
    assert record.hours_gps == 5.35
    assert record.fuel_gallons is not None and round(record.fuel_gallons, 2) == round(142.5 / 3.78541, 2)
    assert any("terminan despues del corte" in warning for warning in record.warnings)


def test_paged_report_splits_when_the_response_hits_the_row_cap(monkeypatch):
    client = _client()
    calls: list[tuple[str, str]] = []

    def fake_get_report(report_type, start_date, end_date, **kwargs):
        calls.append((start_date, end_date))
        if (start_date, end_date) == ("2026-06-01T05:00:00.000Z", "2026-07-01T04:59:59.999Z"):
            return [{"date": f"row-{index}"} for index in range(50000)]
        return [{"date": f"{start_date}-{index}"} for index in range(10)]

    monkeypatch.setattr(client, "get_report", fake_get_report)
    page = client.get_report_paged("gps", "2026-06-01T05:00:00.000Z", "2026-07-01T04:59:59.999Z")

    assert len(calls) == 3
    assert len(page.rows) == 20
    assert page.truncated is False


def test_paged_report_flags_truncation_when_it_cannot_split_further(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        client,
        "get_report",
        lambda *args, **kwargs: [{"date": f"row-{index}"} for index in range(50000)],
    )

    page = client.get_report_paged("gps", "2026-06-01T05:00:00.000Z", "2026-06-01T05:30:00.000Z")

    assert page.truncated is True
