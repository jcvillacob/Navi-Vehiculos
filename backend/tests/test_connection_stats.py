from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from app.services.motor_catalog import _ensure_motor_tables, get_connection_stats


def _connect():
    raw = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(raw, row_factory=dict_row)


def _reset_connection_data() -> None:
    with _connect() as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE TABLE
                    vehicle_connection_log,
                    vehicle_motor_assignments
                RESTART IDENTITY CASCADE;
                """
            )
        conn.commit()


def _insert_vehicle(plate: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicle_motor_assignments (plate, technical_number)
                VALUES (%s, 'TEC-TEST');
                """,
                (plate,),
            )
        conn.commit()


def _insert_connection_log(plate: str, rows: list[tuple[str, str]]) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO vehicle_connection_log (plate, check_date, status)
                VALUES (%s, %s, %s);
                """,
                [(plate, check_date, status) for check_date, status in rows],
            )
        conn.commit()


def test_connection_stats_excludes_unresolved_days_from_percentage():
    _reset_connection_data()
    _insert_vehicle("ABC123")
    _insert_vehicle("ERR123")

    _insert_connection_log(
        "ABC123",
        [
            ("2026-07-01", "connected"),
            ("2026-07-02", "error"),
            ("2026-07-03", "not_found"),
            ("2026-07-04", "connected"),
            ("2026-07-05", "disconnected"),
        ],
    )
    _insert_connection_log(
        "ERR123",
        [
            ("2026-07-01", "error"),
            ("2026-07-02", "not_found"),
        ],
    )

    stats = {row["plate"]: row for row in get_connection_stats("2026-07")}

    assert stats["ABC123"]["days_checked"] == 3
    assert stats["ABC123"]["days_connected"] == 2
    assert stats["ABC123"]["days_disconnected"] == 1
    assert stats["ABC123"]["days_error"] == 1
    assert stats["ABC123"]["days_not_found"] == 1
    assert stats["ABC123"]["connection_pct"] == 66.7
    assert stats["ABC123"]["consecutive_disconnected"] == 1

    assert stats["ERR123"]["days_checked"] == 0
    assert stats["ERR123"]["days_error"] == 1
    assert stats["ERR123"]["days_not_found"] == 1
    assert stats["ERR123"]["connection_pct"] == 0
    assert stats["ERR123"]["consecutive_disconnected"] == 0
