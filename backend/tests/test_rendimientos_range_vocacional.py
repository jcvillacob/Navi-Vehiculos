"""
Consulta por rango de Rendimientos: el SELECT agregado debe exponer
``a.vocacional`` (SELECT + GROUP BY) y los warnings acumulados de los meses,
y el upsert debe preservar metricas buenas cuando llega un 'error' de grupo.

Sin base de datos: se falsifica ``db_conn``/cursor capturando el SQL.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.schemas.vehicle import MonthlyPerformanceRecord
from app.services import rendimientos


class _FakeCursor:
    def __init__(self, captured: dict[str, Any], rows: list[dict[str, Any]] | None = None):
        self._captured = captured
        self._rows = rows or []

    def execute(self, sql: str, params=None):
        self._captured["sql"] = sql
        self._captured["params"] = list(params or [])

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, captured: dict[str, Any], rows: list[dict[str, Any]] | None = None):
        self._captured = captured
        self._rows = rows

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self._captured, self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_db(monkeypatch, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(rendimientos, "_ensure_performance_tables", lambda conn: None)
    monkeypatch.setattr(rendimientos, "db_conn", lambda row_factory=None: _FakeConn(captured, rows))
    return captured


def _split_select_group_by(sql: str) -> tuple[str, str]:
    select_part = sql.split("FROM monthly_vehicle_performance", 1)[0]
    group_by_part = sql.split("GROUP BY", 1)[1].split("ORDER BY", 1)[0]
    return select_part, group_by_part


def _range_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "customer_id": 7,
        "customer_database_id": 3,
        "client_name": "Cliente A",
        "database_name": "db_a",
        "source_provider": "geotab",
        "plate": "ABC123",
        "provider_vehicle_id": "b1",
        "technical_number": "T-1",
        "engine_name": "ISX",
        "period_month": "2026-01",
        "calculation_status": "calculated",
        "warnings": None,
        "is_adhoc": False,
        "vocacional": True,
    }
    row.update(overrides)
    return row


# ── vocacional en rango ──────────────────────────────────────────────────────


def test_range_query_includes_vocacional_in_select_and_group_by(monkeypatch):
    captured = _patch_db(monkeypatch)

    response = rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-03")

    assert response.rows == []
    sql = captured["sql"]
    assert "GROUP BY" in sql, "la consulta multi-mes debe ser la agregada"
    select_part, group_by_part = _split_select_group_by(sql)
    assert "a.vocacional" in select_part
    assert "a.vocacional" in group_by_part
    # a.nombre_vehiculo sigue estando junto a vocacional en ambos lados.
    assert "a.nombre_vehiculo" in select_part and "a.nombre_vehiculo" in group_by_part
    assert "ARRAY_AGG(DISTINCT mp.period_month ORDER BY mp.period_month) AS period_months" in select_part


def test_single_month_query_still_includes_vocacional(monkeypatch):
    captured = _patch_db(monkeypatch)

    rendimientos.list_monthly_performance(month_from="2026-02", month_to="2026-02")

    sql = captured["sql"]
    assert "GROUP BY" not in sql
    assert "a.vocacional" in sql


@pytest.mark.parametrize(
    ("month_from", "month_to"),
    [("2026-02", "2026-02"), ("2026-01", "2026-03")],
)
def test_query_excludes_rows_from_a_database_no_longer_assigned(
    monkeypatch, month_from, month_to
):
    captured = _patch_db(monkeypatch)

    rendimientos.list_monthly_performance(month_from=month_from, month_to=month_to)

    sql = captured["sql"]
    assert "a.customer_id = mp.customer_id" in sql
    assert "a.customer_database_id = mp.customer_database_id" in sql
    assert "(mp.is_adhoc OR a.plate IS NOT NULL)" in sql


def test_build_record_maps_vocacional_from_row():
    record = rendimientos._build_record(_range_row(vocacional=True))
    assert record.vocacional is True

    record = rendimientos._build_record(_range_row(vocacional=False))
    assert record.vocacional is False

    record = rendimientos._build_record(_range_row(vocacional=None))
    assert record.vocacional is False


def test_build_record_exposes_all_months_in_a_range():
    record = rendimientos._build_record(
        _range_row(period_month="2026-01", period_months=["2026-03", "2026-01", "2026-03"])
    )

    assert record.period_month == "2026-01"
    assert record.period_months == ["2026-01", "2026-03"]


def test_build_record_falls_back_to_period_month_for_single_month_rows():
    record = rendimientos._build_record(_range_row(period_month="2026-02"))

    assert record.period_months == ["2026-02"]


def test_range_rows_expose_vocacional_end_to_end(monkeypatch):
    _patch_db(monkeypatch, rows=[_range_row(vocacional=True)])

    response = rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-03")

    assert len(response.rows) == 1
    assert response.rows[0].vocacional is True


# ── warnings agregados en rango (A8) ─────────────────────────────────────────


def test_range_query_no_longer_uses_jsonb_path_query_array(monkeypatch):
    captured = _patch_db(monkeypatch)

    rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-03")

    assert "jsonb_path_query_array" not in captured["sql"]
    assert "jsonb_agg(mp.warnings" in captured["sql"]


def test_range_warnings_are_flattened_and_deduped_preserving_order(monkeypatch):
    aggregated = [["w1", "w2"], None, [], ["w2", "w3"], ["w1"]]
    _patch_db(monkeypatch, rows=[_range_row(warnings=aggregated)])

    response = rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-03")

    assert response.rows[0].warnings == ["w1", "w2", "w3"]


def test_flatten_range_warnings_handles_none_and_scalars():
    assert rendimientos._flatten_range_warnings(None) == []
    assert rendimientos._flatten_range_warnings([None, None]) == []
    assert rendimientos._flatten_range_warnings([["a"], "b", ["a", None]]) == ["a", "b"]


# ── upsert: 'error' no destruye metricas buenas (R3) ─────────────────────────


def _error_record() -> MonthlyPerformanceRecord:
    return MonthlyPerformanceRecord(
        customer_id=7,
        customer_database_id=3,
        client_name="Cliente A",
        database_name="db_a",
        source_provider="geotab",
        plate="ABC123",
        period_month="2026-01",
        calculation_status="error",
        warnings=["No fue posible consultar geotab: boom"],
    )


def test_upsert_sql_preserves_metrics_and_status_when_error_over_good_row():
    captured: dict[str, Any] = {}
    returned = _range_row(kms_ecm=1234.5, calculation_status="calculated")
    conn = _FakeConn(captured, rows=[returned])

    record = rendimientos._upsert_monthly_record(conn, _error_record())

    sql = captured["sql"]
    condition = (
        "EXCLUDED.calculation_status = 'error' "
        "AND NOT (EXCLUDED.validation_flags ? 'negative_value') "
        "AND monthly_vehicle_performance.calculation_status IN ('calculated', 'partial')"
    )
    assert condition in sql
    assert condition == rendimientos._UPSERT_PRESERVE_CONDITION
    for column in ("kms_ecm", "calculation_status"):
        pattern = (
            rf"{column} = CASE WHEN {re.escape(condition)} "
            rf"THEN monthly_vehicle_performance\.{column} ELSE EXCLUDED\.{column} END"
        )
        assert re.search(pattern, sql), f"falta CASE de preservacion para {column}"
    # Las demas metricas tambien van protegidas.
    for column in (
        "odo_start", "odo_end", "horo_start", "horo_end", "kms_gps", "hours_ecm", "hours_gps",
        "fuel_gallons", "geotab_regression_count", "geotab_regression_total_km",
        "geotab_regression_total_hours",
    ):
        assert f"THEN monthly_vehicle_performance.{column} ELSE EXCLUDED.{column} END" in sql
    # Los warnings se anexan (concat jsonb) en vez de reemplazarse.
    assert "COALESCE(monthly_vehicle_performance.warnings, '[]'::jsonb) || EXCLUDED.warnings" in sql
    # Ya no hay asignaciones directas que pisen las metricas.
    assert "kms_ecm = EXCLUDED.kms_ecm," not in sql
    assert "calculation_status = EXCLUDED.calculation_status," not in sql
    # updated_at siempre se bumpea.
    assert "updated_at = NOW()" in sql

    # RETURNING alimenta _build_record: el summary refleja el estado preservado.
    assert record.calculation_status == "calculated"
    assert record.kms_ecm == pytest.approx(1234.5)
