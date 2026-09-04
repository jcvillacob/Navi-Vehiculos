"""
Integracion de la capa de plausibilidad y el hardening de Rendimientos en el
orquestador ``calculate_monthly_performance`` y en ``list_monthly_performance``:

- validate_record corre antes del upsert (odo_end < odo_start -> partial + flag);
- D5: un mes anterior en 'error' no se usa como base de encadenamiento;
- D5: al recalcular M se marca is_stale el mes M+1 de las placas afectadas;
- D12: una placa en varias databases se marca ``duplicate_plate``;
- A4/A5: ``_READ_PATH_TABLES_READY`` evita repetir el bootstrap de esquema;
- el DDL runtime replica las 10 columnas de la migracion 20260902_0001;
- A3: filtros status / source_provider / motor_groups en la consulta.

Sin base de datos: se falsifica ``psycopg.connect`` (calculo) y ``db_conn``
(lectura) capturando SQL y parametros, como en test_calculate_cancel.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from psycopg.types.json import Jsonb

from app.schemas.vehicle import MonthlyPerformanceCalculateRequest, MonthlyPerformanceRecord
from app.services import rendimientos
from app.services.performance_types import PerformanceTarget, ProviderCalculationResult


# ── fakes ────────────────────────────────────────────────────────────────────


class _RecordingCursor:
    """Captura cada execute (sql, params) y responde fetchone con una fila fija."""

    def __init__(self, executed: list[tuple[str, Any]], row: dict[str, Any] | None = None, rows=None):
        self._executed = executed
        self._row = row
        self._rows = rows or []
        self.rowcount = 1

    def execute(self, sql: str, params=None):
        self._executed.append((sql, params))

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _RecordingConn:
    def __init__(self, row: dict[str, Any] | None = None, rows=None):
        self.executed: list[tuple[str, Any]] = []
        self._row = row
        self._rows = rows
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, *args, **kwargs):
        return _RecordingCursor(self.executed, self._row, self._rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_target(plate: str, customer_database_id: int = 1, provider_config: dict | None = None) -> PerformanceTarget:
    return PerformanceTarget(
        provider_key="geotab",
        customer_id=1,
        customer_database_id=customer_database_id,
        client_name="Cliente A",
        database_name="db_a",
        plate=plate,
        technical_number=None,
        engine_name=None,
        username="user",
        password="pass",
        provider_config=provider_config or {},
    )


def _make_record(target: PerformanceTarget, month: str = "2026-01", **overrides: Any) -> MonthlyPerformanceRecord:
    base: dict[str, Any] = dict(
        customer_id=target.customer_id,
        customer_database_id=target.customer_database_id,
        client_name=target.client_name,
        database_name=target.database_name,
        source_provider=target.provider_key,
        plate=target.plate,
        provider_vehicle_id="dev",
        period_month=month,
        calculation_status="calculated",
        warnings=[],
    )
    base.update(overrides)
    return MonthlyPerformanceRecord(**base)


class _CapturingProvider:
    """Provider que devuelve registros predefinidos y guarda los kwargs recibidos."""

    key = "geotab"

    def __init__(self, records_by_plate: dict[str, MonthlyPerformanceRecord]):
        self.records_by_plate = records_by_plate
        self.calls: list[dict[str, Any]] = []

    def calculate_database_rows(self, *, month, targets, on_target_done=None, **kwargs):
        self.calls.append({"month": month, "targets": list(targets), **kwargs})
        rows = []
        for target in targets:
            rows.append(self.records_by_plate.get(target.plate) or _make_record(target, month))
            if on_target_done is not None:
                on_target_done()
        return ProviderCalculationResult(records=rows, binding_updates=[])


def _returning_row(plate: str = "PLT001", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "customer_id": 1,
        "customer_database_id": 1,
        "client_name": "Cliente A",
        "database_name": "db_a",
        "source_provider": "geotab",
        "plate": plate,
        "period_month": "2026-01",
        "calculation_status": "partial",
        "warnings": [],
        "is_adhoc": False,
    }
    row.update(overrides)
    return row


def _wire_calculate(
    monkeypatch,
    targets: list[PerformanceTarget],
    provider: Any,
    *,
    previous: dict[tuple[int, str], MonthlyPerformanceRecord] | None = None,
    returning_row: dict[str, Any] | None = None,
) -> _RecordingConn:
    conn = _RecordingConn(row=returning_row or _returning_row())
    fake_psycopg = MagicMock()
    fake_psycopg.connect = MagicMock(return_value=conn)
    monkeypatch.setattr(rendimientos, "psycopg", fake_psycopg)
    monkeypatch.setattr(rendimientos, "_database_dsn", lambda: "postgresql://fake")
    monkeypatch.setattr(rendimientos, "_ensure_performance_tables", lambda conn: None)
    monkeypatch.setattr(rendimientos, "_fetch_targets", lambda conn, **kwargs: list(targets))

    def _load_existing(conn, month, targets):
        if month == "2025-12":
            return dict(previous or {})
        return {}

    monkeypatch.setattr(rendimientos, "_load_existing_records", _load_existing)
    monkeypatch.setattr(rendimientos, "_load_binding_map", lambda conn, targets: {})
    monkeypatch.setattr(rendimientos, "get_monthly_performance_provider", lambda key: provider)
    monkeypatch.setattr(rendimientos, "_upsert_binding", lambda conn, **kwargs: None)
    # Mes "actual" fijo para que la cascada stale sea determinista.
    monkeypatch.setattr(rendimientos, "_current_month_bogota", lambda: "2026-09")
    return conn


def _payload(month: str = "2026-01") -> MonthlyPerformanceCalculateRequest:
    return MonthlyPerformanceCalculateRequest(month=month, include_adhoc=False, force_recalculate=True)


def _upsert_executes(conn: _RecordingConn) -> list[tuple[str, Any]]:
    return [(sql, params) for sql, params in conn.executed if "INSERT INTO monthly_vehicle_performance" in sql]


def _stale_executes(conn: _RecordingConn) -> list[tuple[str, Any]]:
    return [(sql, params) for sql, params in conn.executed if "SET is_stale = TRUE" in sql]


# ── (a) validacion antes del upsert ──────────────────────────────────────────


def test_provider_record_with_odo_regression_is_upserted_as_partial_with_flag(monkeypatch):
    target = _make_target("PLT001")
    provider = _CapturingProvider(
        {"PLT001": _make_record(target, odo_start=1000.0, odo_end=900.0, kms_ecm=50.0, hours_ecm=5.0)}
    )
    conn = _wire_calculate(monkeypatch, [target], provider)

    rendimientos.calculate_monthly_performance(_payload(), job_id=77)

    upserts = _upsert_executes(conn)
    assert len(upserts) == 1
    sql, params = upserts[0]
    params = list(params)
    assert "partial" in params, "el estado debio degradarse a partial"
    assert "calculated" not in params
    jsonb_params = [p for p in params if isinstance(p, Jsonb)]
    flags = [p.obj for p in jsonb_params if isinstance(p.obj, list) and "odo_regression" in p.obj]
    assert flags, f"validation_flags no contiene odo_regression: {[p.obj for p in jsonb_params]}"
    warnings = [p.obj for p in jsonb_params if isinstance(p.obj, list) and any("Plausibilidad" in str(w) for w in p.obj)]
    assert warnings
    # job_id e is_stale=False viajan al upsert.
    assert 77 in params
    assert "validation_flags" in sql and "job_id" in sql and "is_stale" in sql


def test_plausibility_overrides_come_from_target_provider_config():
    target = _make_target("PLT001", provider_config={"plausibility_overrides": {"max_km_month": 500}})
    assert rendimientos._plausibility_overrides(target) == {"max_km_month": 500}
    assert rendimientos._plausibility_overrides(_make_target("PLT002")) is None
    assert rendimientos._plausibility_overrides(None) is None
    bad = _make_target("PLT003", provider_config={"plausibility_overrides": "no-es-dict"})
    assert rendimientos._plausibility_overrides(bad) is None


# ── (b) D5: mes anterior en error no encadena ────────────────────────────────


def test_previous_record_with_error_status_is_not_passed_to_provider(monkeypatch):
    target_err = _make_target("PLT001")
    target_ok = _make_target("PLT002")
    previous = {
        (1, "PLT001"): _make_record(target_err, month="2025-12", calculation_status="error", odo_end=5000.0),
        (1, "PLT002"): _make_record(target_ok, month="2025-12", calculation_status="calculated", odo_end=7000.0),
    }
    provider = _CapturingProvider({})
    _wire_calculate(monkeypatch, [target_err, target_ok], provider, previous=previous)

    rendimientos.calculate_monthly_performance(_payload())

    assert len(provider.calls) == 1
    passed = provider.calls[0]["previous_records"]
    assert (1, "PLT001") not in passed, "un mes anterior en 'error' no debe usarse como base"
    assert (1, "PLT002") in passed


def test_filter_chainable_previous_drops_non_calculable_statuses():
    target = _make_target("PLT001")
    previous = {
        (1, "a"): _make_record(target, calculation_status="calculated"),
        (1, "b"): _make_record(target, calculation_status="partial"),
        (1, "c"): _make_record(target, calculation_status="error"),
        (1, "d"): _make_record(target, calculation_status="no_data"),
        (1, "e"): _make_record(target, calculation_status="unbound"),
    }
    kept = rendimientos._filter_chainable_previous(previous, month="2026-01")
    assert set(kept) == {(1, "a"), (1, "b")}


# ── (c) D5: cascada stale al mes siguiente ───────────────────────────────────


def test_stale_update_is_emitted_for_next_month_with_upserted_plates(monkeypatch):
    targets = [_make_target("PLT001"), _make_target("PLT002")]
    provider = _CapturingProvider({})
    conn = _wire_calculate(monkeypatch, targets, provider)

    rendimientos.calculate_monthly_performance(_payload("2026-01"))

    stale = _stale_executes(conn)
    assert len(stale) == 1
    sql, params = stale[0]
    assert "UPDATE monthly_vehicle_performance" in sql
    assert "period_month = %s" in sql and "plate = ANY(%s)" in sql
    assert tuple(params) == (1, "2026-02", ["PLT001", "PLT002"])
    # El UPDATE va dentro de la transaccion del grupo, antes de su commit.
    assert conn.commits >= 1


def test_stale_update_is_skipped_when_next_month_is_in_the_future(monkeypatch):
    targets = [_make_target("PLT001")]
    provider = _CapturingProvider({})
    conn = _wire_calculate(monkeypatch, targets, provider)
    # "Hoy" es 2026-09: recalcular 2026-09 no debe tocar 2026-10.
    rendimientos.calculate_monthly_performance(_payload("2026-09"))
    assert _stale_executes(conn) == []


def test_next_month_helper():
    assert rendimientos._next_month("2026-01") == "2026-02"
    assert rendimientos._next_month("2026-12") == "2027-01"
    assert rendimientos._previous_month(rendimientos._next_month("2026-06")) == "2026-06"


# ── (d) D12: placas duplicadas en list_monthly_performance ───────────────────


class _ListConn:
    def __init__(self, captured: dict[str, Any], rows: list[dict[str, Any]]):
        self._captured = captured
        self._rows = rows

    def cursor(self, *args, **kwargs):
        cur = _RecordingCursor(self._captured.setdefault("executed", []), rows=self._rows)
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_list_db(monkeypatch, rows: list[dict[str, Any]]) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(rendimientos, "_ensure_performance_tables", lambda conn: None)
    monkeypatch.setattr(rendimientos, "db_conn", lambda row_factory=None: _ListConn(captured, rows))
    return captured


def _list_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "customer_id": 7,
        "customer_database_id": 3,
        "client_name": "Cliente A",
        "database_name": "db_a",
        "source_provider": "geotab",
        "plate": "ABC123",
        "period_month": "2026-01",
        "calculation_status": "calculated",
        "warnings": [],
        "validation_flags": [],
        "is_adhoc": False,
    }
    row.update(overrides)
    return row


def test_duplicate_plate_across_databases_is_flagged_on_both_rows(monkeypatch):
    _patch_list_db(
        monkeypatch,
        rows=[
            _list_row(customer_database_id=3, database_name="db_a"),
            _list_row(customer_database_id=4, database_name="db_b", validation_flags=["km_over_max"]),
            _list_row(plate="XYZ999", customer_database_id=3),
        ],
    )

    response = rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-01")

    assert len(response.rows) == 3, "no se eliminan filas"
    dup_rows = [row for row in response.rows if row.plate == "ABC123"]
    assert len(dup_rows) == 2
    for row in dup_rows:
        assert "duplicate_plate" in row.validation_flags
        assert any("aparece en 2 databases" in w for w in row.warnings)
    assert "km_over_max" in dup_rows[1].validation_flags, "las flags previas se conservan"
    other = next(row for row in response.rows if row.plate == "XYZ999")
    assert "duplicate_plate" not in other.validation_flags
    assert other.warnings == []


def test_same_plate_same_database_is_not_flagged(monkeypatch):
    _patch_list_db(monkeypatch, rows=[_list_row(), _list_row()])
    response = rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-01")
    assert all("duplicate_plate" not in row.validation_flags for row in response.rows)


# ── (e) A4/A5: bootstrap de lectura una sola vez ─────────────────────────────


def test_read_path_flag_prevents_second_ensure_call(monkeypatch):
    calls = {"count": 0}

    def _ensure(conn):
        calls["count"] += 1

    monkeypatch.setattr(rendimientos, "_ensure_performance_tables", _ensure)
    monkeypatch.setattr(rendimientos, "_READ_PATH_TABLES_READY", False)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(rendimientos, "db_conn", lambda row_factory=None: _ListConn(captured, []))

    rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-01")
    rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-01")
    rendimientos.list_adhoc_filter_options()

    assert calls["count"] == 1
    assert rendimientos._READ_PATH_TABLES_READY is True


# ── (f) DDL runtime replica la migracion 20260902_0001 ───────────────────────


def test_runtime_ddl_adds_hardening_columns_and_indexes():
    conn = _RecordingConn()
    rendimientos._run_performance_tables_ddl_inner(conn)
    ddl = "\n".join(sql for sql, _ in conn.executed)
    for column in (
        "odo_start_source TEXT",
        "odo_end_source TEXT",
        "horo_start_source TEXT",
        "horo_end_source TEXT",
        "fuel_end DOUBLE PRECISION",
        "validation_flags JSONB NOT NULL DEFAULT '[]'::jsonb",
        "source_meta JSONB NOT NULL DEFAULT '{}'::jsonb",
        "job_id BIGINT",
        "last_error TEXT",
        "is_stale BOOLEAN NOT NULL DEFAULT FALSE",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in ddl, column
    assert (
        "CREATE INDEX IF NOT EXISTS monthly_vehicle_performance_period_customer_idx "
        "ON monthly_vehicle_performance (period_month, customer_id)" in ddl
    )
    assert (
        "CREATE INDEX IF NOT EXISTS monthly_vehicle_performance_plate_period_idx "
        "ON monthly_vehicle_performance (plate, period_month)" in ddl
    )


# ── upsert: columnas nuevas y preservacion en error ──────────────────────────


def test_upsert_sql_includes_new_columns_and_preserve_rules():
    target = _make_target("PLT001")
    conn = _RecordingConn(row=_returning_row())
    record = _make_record(
        target,
        odo_start_source="previous",
        odo_end_source="last_reading",
        fuel_end=123.0,
        validation_flags=["km_over_max"],
        source_meta={"device": "b1"},
        job_id=5,
    )
    rendimientos._upsert_monthly_record(conn, record)

    sql, params = conn.executed[0]
    params = list(params)
    # Preservadas en error sobre fila buena.
    for column in ("fuel_end", "odo_start_source", "odo_end_source", "horo_start_source", "horo_end_source", "validation_flags", "source_meta"):
        assert f"THEN monthly_vehicle_performance.{column} ELSE EXCLUDED.{column} END" in sql, column
    # Siempre sobreescritas.
    for column in ("job_id", "last_error", "is_stale"):
        assert f"{column} = EXCLUDED.{column}" in sql, column
    assert "previous" in params and "last_reading" in params and 123.0 in params and 5 in params
    jsonb_objs = [p.obj for p in params if isinstance(p, Jsonb)]
    assert ["km_over_max"] in jsonb_objs
    assert {"device": "b1"} in jsonb_objs


def test_upsert_error_record_without_last_error_uses_first_warning():
    target = _make_target("PLT001")
    conn = _RecordingConn(row=_returning_row(calculation_status="error"))
    record = _make_record(target, calculation_status="error", warnings=["No fue posible consultar geotab: boom"])
    rendimientos._upsert_monthly_record(conn, record)
    _, params = conn.executed[0]
    assert "No fue posible consultar geotab: boom" in list(params)


def test_build_record_maps_new_columns_and_tolerates_missing_keys():
    full = rendimientos._build_record(
        _list_row(
            odo_start_source="gps",
            fuel_end=9.5,
            validation_flags=["hours_high"],
            source_meta={"k": 1},
            job_id=3,
            last_error="x",
            is_stale=True,
        )
    )
    assert full.odo_start_source == "gps"
    assert full.fuel_end == 9.5
    assert full.validation_flags == ["hours_high"]
    assert full.source_meta == {"k": 1}
    assert full.job_id == 3 and full.last_error == "x" and full.is_stale is True

    minimal = rendimientos._build_record(
        {"customer_database_id": 1, "plate": "P", "period_month": "2026-01", "calculation_status": "calculated"}
    )
    assert minimal.validation_flags == [] and minimal.source_meta == {} and minimal.is_stale is False


# ── A3: filtros status / source_provider / motor_groups ──────────────────────


def _last_sql(captured: dict[str, Any]) -> tuple[str, list[Any]]:
    sql, params = captured["executed"][-1]
    return sql, list(params or [])


def test_single_month_filters_apply_in_where(monkeypatch):
    captured = _patch_list_db(monkeypatch, rows=[])
    rendimientos.list_monthly_performance(
        month_from="2026-01",
        month_to="2026-01",
        status=["error", "partial"],
        source_provider=["geotab"],
        motor_group="ISX",
        motor_groups=["X15", "ISX"],
    )
    sql, params = _last_sql(captured)
    assert "mp.calculation_status = ANY(%s)" in sql
    assert "mp.source_provider = ANY(%s)" in sql
    assert "COALESCE(mp.engine_name, '') = ANY(%s)" in sql
    assert ["error", "partial"] in params
    assert ["geotab"] in params
    assert ["ISX", "X15"] in params
    assert "HAVING" not in sql


def test_range_status_filter_goes_to_having_and_provider_to_where(monkeypatch):
    captured = _patch_list_db(monkeypatch, rows=[])
    rendimientos.list_monthly_performance(
        month_from="2026-01", month_to="2026-03", status=["calculated"], source_provider=["artimo"]
    )
    sql, params = _last_sql(captured)
    where_part = sql.split("WHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "mp.calculation_status = ANY" not in where_part
    assert "mp.source_provider = ANY(%s)" in where_part
    having_part = sql.split("HAVING", 1)[1]
    assert "BOOL_OR(mp.calculation_status = 'error')" in having_part
    assert having_part.strip().startswith("CASE")
    assert "END = ANY(%s)" in having_part
    # El status es el ultimo parametro (HAVING va despues del WHERE).
    assert params[-1] == ["calculated"]
    assert ["artimo"] in params


def test_invalid_status_raises_value_error(monkeypatch):
    _patch_list_db(monkeypatch, rows=[])
    with pytest.raises(ValueError, match="Estado"):
        rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-01", status=["bogus"])


def test_range_select_exposes_new_columns(monkeypatch):
    captured = _patch_list_db(monkeypatch, rows=[])
    rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-03")
    sql, _ = _last_sql(captured)
    select_part = sql.split("FROM monthly_vehicle_performance", 1)[0]
    assert "jsonb_agg(mp.validation_flags" in select_part
    assert "BOOL_OR(mp.is_stale) AS is_stale" in select_part
    assert "'{}'::jsonb AS source_meta" in select_part
    for column in ("odo_start_source", "odo_end_source", "horo_start_source", "horo_end_source", "fuel_end", "job_id", "last_error"):
        assert f"AS {column}" in select_part, column


def test_range_validation_flags_are_flattened(monkeypatch):
    _patch_list_db(
        monkeypatch,
        rows=[_list_row(validation_flags=[["km_over_max"], None, ["km_over_max", "hours_high"]], warnings=None)],
    )
    response = rendimientos.list_monthly_performance(month_from="2026-01", month_to="2026-03")
    assert response.rows[0].validation_flags == ["km_over_max", "hours_high"]
