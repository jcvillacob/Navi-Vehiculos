from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.clients.frotcom_client import (
    FrotcomAuthError,
    FrotcomConfig,
    FrotcomRequestError,
    _AUTH_FAILURE_CACHE,
    _TOKEN_CACHE,
    derive_trip_odometers,
    fetch_trips_range,
    get_frotcom_month_range,
    get_frotcom_month_range_utc_bounds,
    sort_trips_chronologically,
    trip_distance,
    trip_start_datetime,
)


CONFIG = FrotcomConfig(username="user", password="pass")


@pytest.fixture(autouse=True)
def _clear_frotcom_caches():
    _TOKEN_CACHE.clear()
    _AUTH_FAILURE_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()
    _AUTH_FAILURE_CACHE.clear()


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class TestFetchTripsRangeChunking:
    def test_blocks_never_exceed_seven_days(self):
        calls: list[dict] = []

        def fake_get(config, path, params, **kwargs):
            calls.append(params)
            return []

        with patch("app.clients.frotcom_client._frotcom_get", side_effect=fake_get):
            fetch_trips_range(CONFIG, "v1", _utc(2026, 6, 1), _utc(2026, 6, 21))

        assert calls
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        prev_df = None
        for params in calls:
            df = datetime.strptime(params["df"], fmt)
            dt = datetime.strptime(params["dt"], fmt)
            assert (dt - df).days <= 7
            if prev_df is not None:
                assert (df - prev_df).days == 6
            prev_df = df
        # Cobertura completa del rango
        assert calls[0]["df"] == "2026-06-01T00:00:00Z"
        assert calls[-1]["dt"] == "2026-06-21T00:00:00Z"

    def test_last_window_clipped_to_end(self):
        calls: list[dict] = []

        def fake_get(config, path, params, **kwargs):
            calls.append(params)
            return []

        with patch("app.clients.frotcom_client._frotcom_get", side_effect=fake_get):
            fetch_trips_range(CONFIG, "v1", _utc(2026, 6, 1), _utc(2026, 6, 9))

        assert calls[-1]["dt"] == "2026-06-09T00:00:00Z"

    def test_dedup_by_trip_id_across_blocks(self):
        blocks = [
            [{"id": 5, "started": "2026-06-06T10:00:00Z"}],
            [{"id": 5, "started": "2026-06-06T10:00:00Z"}, {"id": 6, "started": "2026-06-08T10:00:00Z"}],
        ]
        with patch("app.clients.frotcom_client._frotcom_get", side_effect=blocks):
            trips, warnings = fetch_trips_range(CONFIG, "v1", _utc(2026, 6, 1), _utc(2026, 6, 10))

        assert sorted(str(t["id"]) for t in trips) == ["5", "6"]
        assert warnings == []

    def test_trips_without_id_are_kept(self):
        blocks = [
            [{"started": "2026-06-02T10:00:00Z"}, {"started": "2026-06-03T10:00:00Z"}],
        ]
        with patch("app.clients.frotcom_client._frotcom_get", side_effect=blocks):
            trips, _ = fetch_trips_range(CONFIG, "v1", _utc(2026, 6, 1), _utc(2026, 6, 5))

        assert len(trips) == 2

    def test_no_content_block_is_ignored(self):
        with patch("app.clients.frotcom_client._frotcom_get", return_value=None):
            trips, warnings = fetch_trips_range(CONFIG, "v1", _utc(2026, 6, 1), _utc(2026, 6, 10))

        assert trips == []
        assert warnings == []

    def test_block_error_does_not_abort(self):
        blocks = [
            [{"id": 1, "started": "2026-06-02T10:00:00Z"}],
            RuntimeError("Frotcom GET /trips fallo (HTTP 500)"),
            [{"id": 2, "started": "2026-06-14T10:00:00Z"}],
        ]
        with patch("app.clients.frotcom_client._frotcom_get", side_effect=blocks):
            trips, warnings = fetch_trips_range(CONFIG, "v1", _utc(2026, 6, 1), _utc(2026, 6, 16))

        assert sorted(str(t["id"]) for t in trips) == ["1", "2"]
        assert len(warnings) == 1
        assert "fallo" in warnings[0]

    def test_auth_error_propagates(self):
        with patch(
            "app.clients.frotcom_client._frotcom_get",
            side_effect=FrotcomAuthError("credenciales invalidas"),
        ):
            with pytest.raises(FrotcomAuthError):
                fetch_trips_range(CONFIG, "v1", _utc(2026, 6, 1), _utc(2026, 6, 10))

    def test_unsupported_trips_endpoint_stops_after_first_block(self):
        with patch(
            "app.clients.frotcom_client._frotcom_get",
            side_effect=FrotcomRequestError("/v2/vehicles/v1/trips", 404),
        ) as request:
            trips, warnings = fetch_trips_range(
                CONFIG, "v1", _utc(2026, 6, 1), _utc(2026, 7, 1)
            )

        assert trips == []
        assert len(warnings) == 1
        assert "HTTP 404" in warnings[0]
        request.assert_called_once()


class TestTripHelpers:
    def test_started_field_has_priority(self):
        trip = {"started": "2026-06-05T08:00:00Z", "startDate": "2026-06-01T00:00:00Z"}
        assert trip_start_datetime(trip) == datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)

    def test_fallback_to_start_date(self):
        trip = {"startDate": "2026-06-03T12:30:00"}
        assert trip_start_datetime(trip) == datetime(2026, 6, 3, 12, 30)

    def test_unparseable_date_returns_none(self):
        assert trip_start_datetime({"started": "no-es-fecha"}) is None
        assert trip_start_datetime({}) is None

    def test_distance_aliases(self):
        assert trip_distance({"distance": 12.5}) == 12.5
        assert trip_distance({"mileage": 7}) == 7.0
        assert trip_distance({"km": "9.5"}) == 9.5

    def test_distance_invalid_values(self):
        assert trip_distance({"distance": "n/a"}) is None
        assert trip_distance({}) is None

    def test_sort_chronologically_and_count_undated(self):
        trips = [
            {"id": 2, "started": "2026-06-10T00:00:00Z"},
            {"id": 3},
            {"id": 1, "started": "2026-06-02T00:00:00Z"},
        ]
        ordered, undated = sort_trips_chronologically(trips)
        assert [t["id"] for t in ordered] == [1, 2]
        assert undated == 1

    def test_sort_mixes_naive_and_aware_dates(self):
        trips = [
            {"id": 2, "started": "2026-06-10T00:00:00"},
            {"id": 1, "started": "2026-06-02T00:00:00Z"},
        ]
        ordered, undated = sort_trips_chronologically(trips)
        assert [t["id"] for t in ordered] == [1, 2]
        assert undated == 0


class TestDeriveTripOdometers:
    def test_reconstruction_of_initial_odometer(self):
        trips = [
            {"distance": 10.0},
            {"distance": 5.0},
            {"startOdometer": 1000.0, "endOdometer": 1100.0},
            {"endOdometer": 1150.0},
        ]
        result = derive_trip_odometers(trips)
        assert result.odo_start == 985.0
        assert result.odo_end == 1150.0
        assert result.reconstructed_start is True
        assert result.reconstruction_incomplete is False
        assert any("reconstruido" in w for w in result.warnings)

    def test_previous_trip_without_distance_marks_incomplete(self):
        trips = [
            {},
            {"startOdometer": 500.0, "endOdometer": 520.0},
        ]
        result = derive_trip_odometers(trips)
        assert result.odo_start == 500.0
        assert result.reconstruction_incomplete is True

    def test_no_odometers_returns_none(self):
        result = derive_trip_odometers([{"distance": 10.0}, {"distance": 4.0}])
        assert result.odo_start is None
        assert result.odo_end is None
        assert result.reconstructed_start is False

    def test_first_trip_with_odometer_needs_no_reconstruction(self):
        trips = [
            {"startOdometer": 100.0, "endOdometer": 150.0},
            {"endOdometer": 200.0},
        ]
        result = derive_trip_odometers(trips)
        assert result.odo_start == 100.0
        assert result.odo_end == 200.0
        assert result.reconstructed_start is False
        assert result.warnings == []

    def test_empty_trips(self):
        result = derive_trip_odometers([])
        assert result.odo_start is None
        assert result.odo_end is None
        assert result.trip_count == 0


class TestMonthRangeUtc:
    def test_month_range_iso_in_utc(self):
        df_iso, dt_iso, start, end = get_frotcom_month_range(2026, 6)
        assert df_iso == "2026-06-01T05:00:00Z"
        assert dt_iso == "2026-07-01T04:59:59Z"
        # 3ro/4to elemento siguen siendo locales naive para el barrido CAN
        assert start == datetime(2026, 6, 1, 0, 0, 0)
        assert end == datetime(2026, 6, 30, 23, 59, 59)
        assert start.tzinfo is None and end.tzinfo is None

    def test_december_rollover(self):
        df_iso, dt_iso, _, _ = get_frotcom_month_range(2026, 12)
        assert df_iso == "2026-12-01T05:00:00Z"
        assert dt_iso == "2027-01-01T04:59:59Z"

    def test_utc_bounds_are_aware(self):
        start_utc, end_utc = get_frotcom_month_range_utc_bounds(2026, 6)
        assert start_utc == datetime(2026, 6, 1, 5, 0, 0, tzinfo=timezone.utc)
        assert end_utc == datetime(2026, 7, 1, 4, 59, 59, tzinfo=timezone.utc)
