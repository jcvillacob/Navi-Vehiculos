from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from psycopg.rows import dict_row

from app.clients.artimo_client import ArtimoReportPage
from app.services.performance_providers import (
    ArtimoMonthlyPerformanceProvider,
    FrotcomMonthlyPerformanceProvider,
    GeotabMonthlyPerformanceProvider,
    _select_binding,
)
from app.services.performance_types import BindingSnapshot, PerformanceTarget
from app.schemas.vehicle import MonthlyPerformanceRecord


def _db_url() -> str:
    raw = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL_TEST", "").strip()
    parsed = urlparse(raw.replace("postgresql+psycopg://", "postgresql://", 1))
    return urlunparse(parsed)


def _make_target(
    provider_key: str = "geotab",
    customer_database_id: int = 1,
    plate: str = "TEST001",
    **overrides,
) -> PerformanceTarget:
    defaults = dict(
        provider_key=provider_key,
        customer_id=1,
        customer_database_id=customer_database_id,
        client_name="Test",
        database_name="test_db",
        plate=plate,
        technical_number="TEC001",
        engine_name="Test Motor",
        username="user",
        password="pass",
        provider_config={},
    )
    defaults.update(overrides)
    return PerformanceTarget(**defaults)


def _make_monthly_record(target: PerformanceTarget, provider_vehicle_id: str) -> MonthlyPerformanceRecord:
    return MonthlyPerformanceRecord(
        customer_id=target.customer_id,
        customer_database_id=target.customer_database_id,
        client_name=target.client_name,
        database_name=target.database_name,
        source_provider=target.provider_key,
        plate=target.plate,
        provider_vehicle_id=provider_vehicle_id,
        technical_number=target.technical_number,
        engine_name=target.engine_name,
        period_month="2026-01",
        calculation_status="calculated",
        warnings=[],
    )


class TestSelectBinding:
    def test_returns_none_when_no_binding(self):
        bindings = {}
        target = _make_target()
        vid, is_manual = _select_binding(bindings=bindings, target=target)
        assert vid is None
        assert is_manual is False

    def test_returns_auto_binding(self):
        bindings = {("geotab", 1, "TEST001"): BindingSnapshot("auto123", "resolved", is_manual=False)}
        target = _make_target()
        vid, is_manual = _select_binding(bindings=bindings, target=target)
        assert vid == "auto123"
        assert is_manual is False

    def test_returns_manual_binding(self):
        bindings = {("geotab", 1, "TEST001"): BindingSnapshot("manual456", "resolved", is_manual=True)}
        target = _make_target()
        vid, is_manual = _select_binding(bindings=bindings, target=target)
        assert vid == "manual456"
        assert is_manual is True

    def test_wrong_provider_key_returns_none(self):
        bindings = {("artimo", 1, "TEST001"): BindingSnapshot("artimo_id", "resolved", is_manual=True)}
        target = _make_target(provider_key="geotab")
        vid, is_manual = _select_binding(bindings=bindings, target=target)
        assert vid is None
        assert is_manual is False


class TestGeotabManualBinding:
    def test_manual_binding_bypasses_geotab_lookup(self):
        provider = GeotabMonthlyPerformanceProvider()
        target = _make_target(provider_key="geotab", plate="TEST001")
        bindings = {("geotab", 1, "TEST001"): BindingSnapshot("M1", "resolved", is_manual=True)}
        previous_records = {}

        mock_devices = MagicMock(return_value=[])
        mock_api = MagicMock()
        fake_record = _make_monthly_record(target, "M1")

        with patch("app.services.performance_providers.get_cached_devices", mock_devices), patch(
            "app.services.performance_providers.get_authenticated_client",
            return_value=mock_api,
        ), patch(
            "app.services.performance_providers._calculate_geotab_vehicle_record",
            return_value=fake_record,
        ) as mock_calculate:
            result = provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target],
                previous_records=previous_records,
                bindings=bindings,
            )

        # El inventario se descarga una vez por database; el matching local se salta para binding manual.
        assert mock_devices.call_count == 1
        mock_calculate.assert_called_once()
        assert mock_calculate.call_args.kwargs["device_id"] == "M1"
        assert len(result.records) == 1
        assert result.records[0].provider_vehicle_id == "M1"

    def test_auto_binding_is_refreshed_from_current_geotab_database(self):
        provider = GeotabMonthlyPerformanceProvider()
        target = _make_target(provider_key="geotab", plate="TEST001")
        bindings = {("geotab", 1, "TEST001"): BindingSnapshot("STALE_ID", "resolved", is_manual=False)}
        previous_records = {}

        mock_devices = MagicMock(return_value=[{"id": "AUTO_DEVICE_ID", "licensePlate": "TEST001"}])
        mock_api = MagicMock()
        fake_record = _make_monthly_record(target, "AUTO_DEVICE_ID")

        with patch("app.services.performance_providers.get_cached_devices", mock_devices), patch(
            "app.services.performance_providers.get_authenticated_client",
            return_value=mock_api,
        ), patch(
            "app.services.performance_providers._calculate_geotab_vehicle_record",
            return_value=fake_record,
        ) as mock_calculate:
            result = provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target],
                previous_records=previous_records,
                bindings=bindings,
            )

        mock_devices.assert_called_once_with("user", "pass", "test_db")
        mock_calculate.assert_called_once()
        assert mock_calculate.call_args.kwargs["device_id"] == "AUTO_DEVICE_ID"
        assert result.records[0].provider_vehicle_id == "AUTO_DEVICE_ID"
        assert result.binding_updates[0].provider_vehicle_id == "AUTO_DEVICE_ID"


class TestUpsertBindingDoesNotClobberManual:
    def test_auto_value_does_not_overwrite_manual_binding(self):
        from app.services.rendimientos import _upsert_binding

        plate = "TESTCLBR"
        db_id = 1
        provider = "geotab"

        target = _make_target(provider_key=provider, customer_database_id=db_id, plate=plate)

        created_customer = False
        created_database = False

        with psycopg.connect(_db_url()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM customer_databases WHERE id = %s LIMIT 1;", (db_id,))
                db_row = cur.fetchone()
            if not db_row:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO customers (name) VALUES ('test_binding_customer') RETURNING id;
                        """,
                    )
                    cust_row = cur.fetchone()
                    cust_id = cust_row[0]
                    created_customer = True
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO customer_databases (id, customer_id, database_name, username, password, connection_type)
                        VALUES (%s, %s, 'test_binding_db', 'testuser', 'testpass', 'geotab')
                        ON CONFLICT (id) DO NOTHING;
                        """,
                        (db_id, cust_id),
                    )
                    created_database = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vehicle_motor_assignments (plate, technical_number, geotab_status)
                    VALUES (%s, 'TEC001', 'unknown')
                    ON CONFLICT (plate) DO NOTHING;
                    """,
                    (plate,),
                )
            conn.commit()

        with psycopg.connect(_db_url(), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vehicle_provider_bindings
                    (plate, customer_database_id, provider, provider_vehicle_id, binding_status, is_manual, updated_at)
                    VALUES (%s, %s, %s, 'MANUAL_ID', 'resolved', TRUE, NOW())
                    ON CONFLICT (plate, customer_database_id, provider)
                    DO UPDATE SET provider_vehicle_id = EXCLUDED.provider_vehicle_id, is_manual = TRUE;
                    """,
                    (plate, db_id, provider),
                )
            conn.commit()

        try:
            with psycopg.connect(_db_url()) as conn:
                _upsert_binding(
                    conn,
                    target=target,
                    provider_vehicle_id="AUTO_ID",
                    binding_status="resolved",
                    last_error=None,
                )

            with psycopg.connect(_db_url(), row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT provider_vehicle_id, is_manual
                        FROM vehicle_provider_bindings
                        WHERE plate = %s AND customer_database_id = %s AND provider = %s;
                        """,
                        (plate, db_id, provider),
                    )
                    row = cur.fetchone()

            assert row is not None
            assert row["provider_vehicle_id"] == "MANUAL_ID", \
                "Manual binding must not be overwritten by auto resolution"
            assert row["is_manual"] is True
        finally:
            with psycopg.connect(_db_url()) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM vehicle_motor_assignments WHERE plate = %s;", (plate,))
                conn.commit()
            if created_database:
                with psycopg.connect(_db_url()) as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM customer_databases WHERE id = %s;", (db_id,))
                    conn.commit()
            if created_customer:
                with psycopg.connect(_db_url()) as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM customers WHERE name = 'test_binding_customer';")
                    conn.commit()


class TestFrotcomPerTargetCredentials:
    def test_frotcom_uses_per_target_credentials_not_env(self):
        provider = FrotcomMonthlyPerformanceProvider()
        target_a = _make_target(
            provider_key="frotcom",
            customer_database_id=10,
            plate="AAA111",
            username="user_a",
            password="pass_a",
        )
        target_b = _make_target(
            provider_key="frotcom",
            customer_database_id=20,
            plate="BBB222",
            username="user_b",
            password="pass_b",
        )
        bindings = {
            ("frotcom", 10, "AAA111"): BindingSnapshot("VID_A", "resolved", is_manual=True),
            ("frotcom", 20, "BBB222"): BindingSnapshot("VID_B", "resolved", is_manual=True),
        }

        captured_configs = []

        def fake_calculate(*, target, config, **_kwargs):
            captured_configs.append((target.plate, config.username, config.password))
            return _make_monthly_record(target, "VID_X")

        with patch(
            "app.services.performance_providers._calculate_frotcom_vehicle_record",
            side_effect=fake_calculate,
        ), patch(
            "app.services.performance_providers.list_frotcom_vehicles"
        ) as mock_list:
            provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target_a, target_b],
                previous_records={},
                bindings=bindings,
            )

        assert mock_list.call_count == 0, \
            "Manual bindings should skip list_frotcom_vehicles"
        assert sorted(captured_configs) == [
            ("AAA111", "user_a", "pass_a"),
            ("BBB222", "user_b", "pass_b"),
        ]

    def test_frotcom_missing_credentials_reports_error(self):
        provider = FrotcomMonthlyPerformanceProvider()
        target = _make_target(
            provider_key="frotcom",
            plate="NOPE001",
            username="",
            password="",
        )

        result = provider.calculate_database_rows(
            month="2026-01",
            year=2026,
            month_number=1,
            previous_month="2025-12",
            targets=[target],
            previous_records={},
            bindings={},
        )

        assert len(result.records) == 1
        assert result.records[0].calculation_status == "error"
        assert len(result.binding_updates) == 1
        assert result.binding_updates[0].binding_status == "error"
        assert "credenciales Frotcom" in (result.binding_updates[0].last_error or "")


class TestArtimoWithoutLegacyBootstrap:
    def test_artimo_extracts_provider_vehicle_id_from_trips(self):
        provider = ArtimoMonthlyPerformanceProvider()
        target = _make_target(provider_key="artimo", plate="TEST001")
        bindings = {}
        previous_records = {}

        trip_row = {
            "plate": "TEST001",
            "id": "trip1",
            "odometer": 10000,
            "horometer": 500,
            "enginetime": 5.0,
            "startdate": "2026-01-15 08:00:00",
            "enddate": "2026-01-15 12:00:00",
            "distance": 100.0,
            "consumption": 40.0,
        }
        mock_artimo = MagicMock()
        mock_artimo.get_month_range.return_value = (
            "2026-01-01T05:00:00.000Z",
            "2026-02-01T04:59:59.999Z",
        )
        mock_artimo.get_local_month_bounds.return_value = (
            datetime(2026, 1, 1),
            datetime(2026, 1, 31, 23, 59, 59, 999000),
        )
        mock_artimo.get_trip_lookback_range.return_value = (
            "2025-12-30T05:00:00.000Z",
            "2026-02-01T04:59:59.999Z",
        )
        mock_artimo.get_report.side_effect = lambda name, *args, **kwargs: (
            [trip_row] if name == "trips" else []
        )
        mock_artimo.get_trip_details.return_value = [trip_row]
        mock_artimo.get_report_paged.return_value = ArtimoReportPage(rows=[])

        with patch.object(ArtimoMonthlyPerformanceProvider, "_build_config", return_value=MagicMock()):
            with patch("app.services.performance_providers.ArtimoClient", return_value=mock_artimo):
                result = provider.calculate_database_rows(
                    month="2026-01",
                    year=2026,
                    month_number=1,
                    previous_month="2025-12",
                    targets=[target],
                    previous_records=previous_records,
                    bindings=bindings,
                )

        assert len(result.records) == 1
        assert result.records[0].calculation_status in ("calculated", "partial")
        binding_updates = result.binding_updates
        assert len(binding_updates) == 1
        assert binding_updates[0].provider_vehicle_id == "trip1"
        assert binding_updates[0].provider_vehicle_id is not None, \
            "provider_vehicle_id must be extracted from trips"
        assert binding_updates[0].binding_status == "resolved"
