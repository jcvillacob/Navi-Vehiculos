from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from app.services.availability_store import _ensure_availability_table
from app.services.motor_catalog import _ensure_motor_tables
from app.services.rendimientos import _ensure_performance_tables
from app.clients.geotab_client import get_device_live_status
from app.services.vehicle_ficha import get_vehicle_ficha, get_vehicle_telemetry


def _connect():
    raw = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(raw, row_factory=dict_row)


def _reset_ficha_data() -> None:
    with _connect() as conn:
        _ensure_motor_tables(conn)
        _ensure_performance_tables(conn)
        _ensure_availability_table()
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE TABLE
                    monthly_vehicle_performance,
                    monthly_vehicle_availability,
                    vehicle_provider_bindings,
                    vehicle_motor_assignments,
                    customer_databases,
                    customers,
                    motor_catalog
                RESTART IDENTITY CASCADE;
                """
            )
        conn.commit()


def _insert_customer_and_database() -> tuple[int, int]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customers (name, category) VALUES ('Ficha Test Customer', 'Experiencia Superior') RETURNING id;"
            )
            customer_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO customer_databases (customer_id, database_name, username, password, connection_type)
                VALUES (%s, 'ficha_test_db', 'ficha_user', 'ficha_pass', 'geotab')
                RETURNING id;
                """,
                (customer_id,),
            )
            database_id = cur.fetchone()["id"]
        conn.commit()
    return customer_id, database_id


def _insert_motor_catalog(technical_number: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO motor_catalog (technical_number, engine_name)
                VALUES (%s, 'ISX15 Test')
                ON CONFLICT DO NOTHING;
                """,
                (technical_number,),
            )
        conn.commit()


def _insert_assignment(
    plate: str,
    customer_id: int,
    database_id: int,
    technical_number: str = "TEC-FICHA-001",
) -> None:
    _insert_motor_catalog(technical_number)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicle_motor_assignments (
                    plate, vin, technical_number, engine_number, customer_id,
                    customer_database_id, marca, linea, ano_modelo,
                    tipo_combustible, nombre_vehiculo, geotab_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    plate,
                    "3ALACWDC2DDJC0624",
                    technical_number,
                    "ENG-001",
                    customer_id,
                    database_id,
                    "Freightliner",
                    "M2 106",
                    "2022",
                    "Diesel",
                    "Volqueta Ficha",
                    "connected",
                ),
            )
        conn.commit()


def _insert_performance(
    plate: str,
    database_id: int,
    period_month: str,
    customer_id: int | None = None,
) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monthly_vehicle_performance (
                    customer_id, customer_database_id, plate, period_month,
                    source_provider, provider_vehicle_id, technical_number,
                    kms_ecm, kms_gps, hours_ecm, hours_gps, fuel_gallons,
                    calculation_status, warnings
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    customer_id,
                    database_id,
                    plate,
                    period_month,
                    "geotab",
                    "geo-123",
                    "TEC-FICHA-001",
                    1250.5,
                    1240.0,
                    180.5,
                    178.0,
                    320.75,
                    "calculated",
                    '["todo ok"]',
                ),
            )
        conn.commit()


def _insert_availability(
    plate: str,
    period_month: str,
    availability_pct: float,
    h_no_disp: float,
    mttr_hours: float,
    orders_considered: int = 3,
    orders_closed: int = 2,
) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monthly_vehicle_availability (
                    plate, period_month, calculation_status,
                    project_availability_pct, h_total, h_no_disp,
                    orders_considered, mttr_hours, orders_closed, source
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    plate,
                    period_month,
                    "calculated",
                    availability_pct,
                    720.0,
                    h_no_disp,
                    orders_considered,
                    mttr_hours,
                    orders_closed,
                    "cloudfleet",
                ),
            )
        conn.commit()


def _insert_binding(
    plate: str,
    database_id: int,
    provider: str,
    provider_vehicle_id: str,
    binding_status: str = "resolved",
    last_error: str | None = None,
    is_manual: bool = False,
) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicle_provider_bindings (
                    plate, customer_database_id, provider, provider_vehicle_id,
                    binding_status, last_error, is_manual
                ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                """,
                (plate, database_id, provider, provider_vehicle_id, binding_status, last_error, is_manual),
            )
        conn.commit()


def _insert_customer_and_artimo_database() -> tuple[int, int]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customers (name, category) VALUES ('Ficha Artimo Customer', 'Experiencia Superior') RETURNING id;"
            )
            customer_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO customer_databases (customer_id, database_name, username, password, connection_type)
                VALUES (%s, 'ficha_artimo_db', 'ficha_user', 'ficha_pass', 'artimo')
                RETURNING id;
                """,
                (customer_id,),
            )
            database_id = cur.fetchone()["id"]
        conn.commit()
    return customer_id, database_id


def test_vehicle_ficha_shape_and_order():
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_database()
    _insert_assignment("ABC123", customer_id, database_id)
    _insert_performance("ABC123", database_id, "2026-06", customer_id)
    _insert_performance("ABC123", database_id, "2026-05", customer_id)
    _insert_availability("ABC123", "2026-06", 97.5, 18.0, 6.5)
    _insert_availability("ABC123", "2026-05", 96.2, 27.0, 8.0)
    _insert_binding("ABC123", database_id, "geotab", "geo-123")
    _insert_binding("ABC123", database_id, "artimo", "artimo-456", "pending")

    ficha = get_vehicle_ficha("ABC123")

    assert ficha is not None
    assert ficha["plate"] == "ABC123"
    assert ficha["master"] is not None
    assert ficha["master"]["plate"] == "ABC123"
    assert ficha["master"]["client_name"] == "Ficha Test Customer"
    assert ficha["master"]["engine_name"] == "ISX15 Test"
    assert ficha["master"]["marca"] == "Freightliner"
    assert ficha["master"]["geotab_status"] == "connected"
    assert "generated_at" in ficha

    assert len(ficha["rendimientos"]) == 2
    assert ficha["rendimientos"][0]["period_month"] == "2026-06"
    assert ficha["rendimientos"][1]["period_month"] == "2026-05"
    assert ficha["rendimientos"][0]["kms_ecm"] == 1250.5

    assert len(ficha["disponibilidad"]) == 2
    assert ficha["disponibilidad"][0]["period_month"] == "2026-06"
    assert ficha["disponibilidad"][1]["period_month"] == "2026-05"
    assert ficha["disponibilidad"][0]["project_availability_pct"] == 97.5
    assert ficha["disponibilidad"][0]["mttr_hours"] == 6.5

    assert len(ficha["bindings"]) == 2
    providers = {row["provider"] for row in ficha["bindings"]}
    assert providers == {"geotab", "artimo"}


def test_vehicle_ficha_missing_plate_returns_none():
    _reset_ficha_data()
    assert get_vehicle_ficha("NOEXISTE") is None


def test_vehicle_ficha_normalizes_plate():
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_database()
    _insert_assignment("ABC123", customer_id, database_id)
    _insert_performance("ABC123", database_id, "2026-06", customer_id)

    ficha = get_vehicle_ficha("abc123")
    assert ficha is not None
    assert ficha["plate"] == "ABC123"


def _make_order(plate: str, order_number: str = "OT-001") -> dict:
    return {
        "order_number": order_number,
        "plate": plate,
        "type": "Correctiva",
        "status": "opened",
        "status_indicator": "on_time",
        "time_status_text": "En tiempo",
        "days_elapsed": 2,
        "pending_closure_days": None,
        "maintenance_labels": ["motor"],
    }


def test_vehicle_ficha_taller_shows_matching_order(monkeypatch):
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_database()
    _insert_assignment("ABC123", customer_id, database_id)

    def _fake_peek():
        return {
            "generated_at": "2026-07-12T10:00:00",
            "summary": {"total_active": 1},
            "orders": [_make_order("ABC123")],
        }

    monkeypatch.setattr("app.services.vehicle_ficha.peek_cached_orders", _fake_peek)

    ficha = get_vehicle_ficha("ABC123")
    assert ficha is not None
    assert ficha["taller"]["available"] is True
    assert ficha["taller"]["generated_at"] == "2026-07-12T10:00:00"
    assert len(ficha["taller"]["orders"]) == 1

    order = ficha["taller"]["orders"][0]
    assert order["order_number"] == "OT-001"
    assert order["type"] == "Correctiva"
    assert order["status"] == "opened"
    assert order["status_indicator"] == "on_time"
    assert order["time_status_text"] == "En tiempo"
    assert order["days_elapsed"] == 2
    assert order["pending_closure_days"] is None
    assert order["maintenance_labels"] == ["motor"]


def test_vehicle_ficha_taller_cold_cache(monkeypatch):
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_database()
    _insert_assignment("ABC123", customer_id, database_id)

    monkeypatch.setattr("app.services.vehicle_ficha.peek_cached_orders", lambda: None)

    ficha = get_vehicle_ficha("ABC123")
    assert ficha is not None
    assert ficha["taller"]["available"] is False
    assert ficha["taller"]["orders"] == []


def test_vehicle_ficha_taller_other_plates_empty(monkeypatch):
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_database()
    _insert_assignment("ABC123", customer_id, database_id)

    def _fake_peek():
        return {
            "generated_at": "2026-07-12T10:05:00",
            "summary": {"total_active": 2},
            "orders": [
                _make_order("XYZ999", "OT-002"),
                _make_order("ABC124", "OT-003"),
            ],
        }

    monkeypatch.setattr("app.services.vehicle_ficha.peek_cached_orders", _fake_peek)

    ficha = get_vehicle_ficha("ABC123")
    assert ficha is not None
    assert ficha["taller"]["available"] is True
    assert ficha["taller"]["generated_at"] == "2026-07-12T10:05:00"
    assert ficha["taller"]["orders"] == []


# ── Telemetry ────────────────────────────────────────────────────────────────


def test_vehicle_telemetry_happy_path(monkeypatch):
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_database()
    _insert_assignment("ABC123", customer_id, database_id)
    _insert_binding("ABC123", database_id, "geotab", "geo-device-123")

    def _fake_get_authenticated_client(username, password, database):
        return object()

    def _fake_get_device_live_status(api, device_id):
        return {
            "last_communication": "2026-07-12T14:30:00Z",
            "is_driving": True,
            "latitude": 4.711,
            "longitude": -74.0721,
            "speed": 45.5,
            "odometer_km": 123456.789,
            "engine_hours": 5678.9,
            "readings_window_days": 7,
        }

    monkeypatch.setattr("app.services.vehicle_ficha.get_authenticated_client", _fake_get_authenticated_client)
    monkeypatch.setattr("app.services.vehicle_ficha.get_device_live_status", _fake_get_device_live_status)

    telemetry = get_vehicle_telemetry("ABC123")

    assert telemetry["available"] is True
    assert telemetry["plate"] == "ABC123"
    assert telemetry["device_id"] == "geo-device-123"
    assert telemetry["is_driving"] is True
    assert telemetry["odometer_km"] == 123456.789
    assert telemetry["engine_hours"] == 5678.9
    assert telemetry["readings_window_days"] == 7


def test_vehicle_telemetry_no_geotab_database():
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_artimo_database()
    _insert_assignment("ABC123", customer_id, database_id)

    telemetry = get_vehicle_telemetry("ABC123")

    assert telemetry["available"] is False
    assert telemetry["reason"] == "sin_database_geotab"


def test_vehicle_telemetry_device_not_found(monkeypatch):
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_database()
    _insert_assignment("ABC123", customer_id, database_id)

    monkeypatch.setattr("app.services.vehicle_ficha.get_cached_devices", lambda *a, **k: [])

    telemetry = get_vehicle_telemetry("ABC123")

    assert telemetry["available"] is False
    assert telemetry["reason"] == "device_no_encontrado"


def test_vehicle_telemetry_geotab_exception(monkeypatch):
    _reset_ficha_data()
    customer_id, database_id = _insert_customer_and_database()
    _insert_assignment("ABC123", customer_id, database_id)
    _insert_binding("ABC123", database_id, "geotab", "geo-device-123")

    def _fake_get_authenticated_client(username, password, database):
        raise Exception("Credenciales Geotab rechazadas")

    monkeypatch.setattr("app.services.vehicle_ficha.get_authenticated_client", _fake_get_authenticated_client)

    telemetry = get_vehicle_telemetry("ABC123")

    assert telemetry["available"] is False
    assert telemetry["reason"] == "geotab_error"
    assert "Credenciales Geotab rechazadas" in telemetry["detail"]


def test_get_device_live_status_maps_fields_and_uses_last_reading():
    class FakeAPI:
        def multi_call(self, calls):
            return [
                [{"dateTime": "2026-07-12T14:30:00.000Z", "isDriving": True, "latitude": 4.711, "longitude": -74.0721, "speed": 45.5}],
                [
                    {"dateTime": "2026-07-06T10:00:00.000Z", "data": 123000000},
                    {"dateTime": "2026-07-12T14:00:00.000Z", "data": 123456789},
                ],
                [
                    {"dateTime": "2026-07-06T10:00:00.000Z", "data": 20444040},
                    {"dateTime": "2026-07-12T14:00:00.000Z", "data": 20444444},
                ],
            ]

    telemetry = get_device_live_status(FakeAPI(), "device-1")

    assert telemetry["last_communication"] == "2026-07-12T14:30:00.000Z"
    assert telemetry["is_driving"] is True
    assert telemetry["latitude"] == 4.711
    assert telemetry["longitude"] == -74.0721
    assert telemetry["speed"] == 45.5
    assert telemetry["odometer_km"] == 123456.789
    assert telemetry["engine_hours"] == round(20444444 / 3600, 3)
    assert telemetry["readings_window_days"] == 7


def test_get_device_live_status_graceful_with_missing_shape():
    class FakeAPI:
        def multi_call(self, calls):
            return [[], [], []]

    telemetry = get_device_live_status(FakeAPI(), "device-1")

    assert telemetry["last_communication"] is None
    assert telemetry["is_driving"] is None
    assert telemetry["latitude"] is None
    assert telemetry["longitude"] is None
    assert telemetry["speed"] is None
    assert telemetry["odometer_km"] is None
    assert telemetry["engine_hours"] is None
    assert telemetry["readings_window_days"] == 7
