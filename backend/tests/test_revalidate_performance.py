"""Tests sin DB para app.jobs.revalidate_performance."""
from __future__ import annotations

import json
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from app.jobs import revalidate_performance as rp


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 1,
        "customer_id": 10,
        "customer_database_id": 5,
        "plate": "ABC123",
        "period_month": "2026-07",
        "source_provider": "geotab",
        "provider_vehicle_id": "b1",
        "technical_number": None,
        "engine_name": None,
        "odo_start": 1000.0,
        "odo_end": 2000.0,
        "horo_start": 100.0,
        "horo_end": 150.0,
        "kms_ecm": 1000.0,
        "kms_gps": 1000.0,
        "hours_ecm": 50.0,
        "hours_gps": 50.0,
        "fuel_gallons": 100.0,
        "calculation_status": "calculated",
        "warnings": [],
        "calculated_at": None,
        "is_adhoc": False,
        "geotab_regression_count": 0,
        "geotab_regression_total_km": 0.0,
        "geotab_regression_total_hours": 0.0,
        "odo_start_source": None,
        "odo_end_source": None,
        "horo_start_source": None,
        "horo_end_source": None,
        "fuel_end": None,
        "validation_flags": None,
        "source_meta": None,
        "job_id": None,
        "last_error": None,
        "is_stale": False,
        "vocacional": None,
        "plausibility_overrides": None,
    }
    base.update(overrides)
    return base


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, tuple(params) if params is not None else ()))


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #
def test_report_structure_and_status_change_counting() -> None:
    rows = [
        # Sano: no cambia.
        _row(id=1, plate="AAA111", period_month="2026-07"),
        # Retroceso de odometro: calculated -> partial, kms_ecm anulado.
        _row(id=2, plate="BBB222", period_month="2026-07", odo_start=5000.0, odo_end=4000.0, kms_ecm=900.0),
        # Valor negativo: calculated -> error.
        _row(id=3, plate="CCC333", period_month="2026-08", fuel_gallons=-5.0),
        # unbound se deja intacto aunque tenga datos raros.
        _row(id=4, plate="DDD444", period_month="2026-08", calculation_status="unbound", odo_start=9.0, odo_end=1.0),
        # Ya partial con flags: solo cambian warnings/flags (no cuenta como cambio de estado).
        _row(id=5, plate="EEE555", period_month="2026-08", calculation_status="partial", kms_ecm=40000.0, kms_gps=40000.0),
    ]
    report = rp.revalidate_rows(rows, from_month="2026-07", to_month="2026-08")

    assert report.total.evaluated == 5
    assert set(report.months) == {"2026-07", "2026-08"}
    assert report.months["2026-07"].evaluated == 2
    assert report.months["2026-08"].evaluated == 3

    # Cambios de estado
    assert report.total.status_changed == 2
    assert report.total.transitions == {"calculated->partial": 1, "calculated->error": 1}
    assert report.months["2026-07"].transitions == {"calculated->partial": 1}
    assert report.months["2026-08"].transitions == {"calculated->error": 1}

    # EEE555 cambia (flags nuevos) pero no cambia de estado.
    assert report.total.changed == 3
    ids = {c.row_id for c in report.changes}
    assert ids == {2, 3, 5}

    # Frecuencia de flags
    assert report.total.flags["odo_regression"] == 1
    assert report.total.flags["negative_value"] == 1
    assert report.total.flags["km_over_max"] == 1
    assert report.months["2026-08"].flags["km_over_max"] == 1

    # La muestra prioriza cambios de estado
    sample = report.sample()
    assert [c.row_id for c in sample[:2]] == [2, 3]
    assert len(sample) <= rp.SAMPLE_LIMIT

    # Estructura JSON serializable
    doc = report.to_dict()
    json.dumps(doc, default=str)
    assert doc["from"] == "2026-07" and doc["to"] == "2026-08"
    assert doc["total"]["status_changed"] == 2
    assert doc["months"]["2026-07"]["evaluated"] == 2
    assert doc["sample"][0]["plate"] == "BBB222"
    assert doc["sample"][0]["old_status"] == "calculated"
    assert doc["sample"][0]["new_status"] == "partial"
    assert "odo_regression" in doc["sample"][0]["flags"]
    assert doc["applied"] is None

    # Salida legible
    text = rp.format_report(report, apply=False)
    assert "DRY-RUN" in text
    assert "calculated->partial: 1" in text
    assert "BBB222" in text
    assert "odo_regression" in text


def test_unchanged_rows_are_not_reported_and_idempotent() -> None:
    row = _row(id=7, odo_start=5000.0, odo_end=4000.0, kms_ecm=None, calculation_status="partial")
    first = rp.revalidate_rows([row], from_month="2026-07", to_month="2026-07")
    assert first.total.changed == 1  # agrega warning/flag
    change = first.changes[0]
    # Segunda pasada con lo ya persistido: nada cambia.
    row2 = dict(row, warnings=change.new_warnings, validation_flags=change.flags)
    second = rp.revalidate_rows([row2], from_month="2026-07", to_month="2026-07")
    assert second.total.evaluated == 1
    assert second.total.changed == 0
    assert second.changes == []


def test_flags_and_warnings_stored_as_json_strings_are_parsed() -> None:
    row = _row(
        id=8,
        warnings=json.dumps(["Plausibilidad: viejo", "Otro aviso"]),
        validation_flags=json.dumps(["km_over_max"]),
        source_meta=json.dumps({"device": "b1"}),
    )
    record = rp.build_record(row)
    assert record.warnings == ["Plausibilidad: viejo", "Otro aviso"]
    assert record.validation_flags == ["km_over_max"]
    assert record.source_meta == {"device": "b1"}
    report = rp.revalidate_rows([row], from_month="2026-07", to_month="2026-07")
    # El warning stale de plausibilidad se limpia -> la fila cambia, sin cambio de estado.
    assert report.total.changed == 1
    assert report.total.status_changed == 0
    assert report.changes[0].new_warnings == ["Otro aviso"]
    assert report.changes[0].flags == []


# --------------------------------------------------------------------------- #
# Previous-month linkage
# --------------------------------------------------------------------------- #
def test_previous_month_linkage_flags_chain_broken() -> None:
    rows = [
        # Mes previo fuera de rango: solo sirve como `previous`, no se reporta.
        _row(id=1, plate="ABC123", period_month="2026-06", odo_start=0.0, odo_end=1000.0, horo_start=0.0, horo_end=100.0),
        _row(id=2, plate="ABC123", period_month="2026-07", odo_start=1500.0, odo_end=2000.0, horo_start=100.0, horo_end=150.0, kms_ecm=500.0, kms_gps=500.0),
    ]
    report = rp.revalidate_rows(rows, from_month="2026-07", to_month="2026-07")
    assert report.total.evaluated == 1
    assert "2026-06" not in report.months
    assert report.total.flags["chain_broken"] == 1
    assert report.changes[0].row_id == 2
    assert report.changes[0].old_status == "calculated"
    assert report.changes[0].new_status == "calculated"  # chain_broken es solo warning


def test_previous_month_requires_immediate_month_and_same_database() -> None:
    # Hueco de un mes: 2026-05 no es previous de 2026-07.
    rows_gap = [
        _row(id=1, plate="ABC123", period_month="2026-05", odo_end=1000.0),
        _row(id=2, plate="ABC123", period_month="2026-07", odo_start=1500.0, odo_end=2000.0, kms_ecm=500.0, kms_gps=500.0),
    ]
    report = rp.revalidate_rows(rows_gap, from_month="2026-07", to_month="2026-07")
    assert report.total.flags.get("chain_broken", 0) == 0

    # Misma placa, otra database: no se enlaza.
    rows_db = [
        _row(id=1, plate="ABC123", customer_database_id=1, period_month="2026-06", odo_end=1000.0),
        _row(id=2, plate="ABC123", customer_database_id=2, period_month="2026-07", odo_start=1500.0, odo_end=2000.0, kms_ecm=500.0, kms_gps=500.0),
    ]
    report = rp.revalidate_rows(rows_db, from_month="2026-07", to_month="2026-07")
    assert report.total.flags.get("chain_broken", 0) == 0


def test_vocacional_and_overrides_from_row() -> None:
    # 20.000 km: comercial OK, vocacional (max 15.000) -> partial.
    base = dict(odo_start=0.0, odo_end=20000.0, kms_ecm=20000.0, kms_gps=20000.0, hours_ecm=400.0, hours_gps=400.0, fuel_gallons=2000.0)
    comercial = rp.revalidate_rows([_row(id=1, **base)], from_month="2026-07", to_month="2026-07")
    assert comercial.total.status_changed == 0
    vocacional = rp.revalidate_rows([_row(id=2, vocacional=True, **base)], from_month="2026-07", to_month="2026-07")
    assert vocacional.total.transitions == {"calculated->partial": 1}
    assert vocacional.total.flags["km_over_max"] == 1
    # Override por cliente levanta el limite.
    relaxed = rp.revalidate_rows(
        [_row(id=3, vocacional=True, plausibility_overrides={"max_km_month": 25000}, **base)],
        from_month="2026-07",
        to_month="2026-07",
    )
    assert relaxed.total.status_changed == 0


# --------------------------------------------------------------------------- #
# --apply
# --------------------------------------------------------------------------- #
def test_apply_changes_issues_expected_update_params() -> None:
    rows = [
        _row(id=11, plate="AAA111"),  # sin cambios
        _row(id=12, plate="BBB222", odo_start=5000.0, odo_end=4000.0, kms_ecm=900.0),  # odo_regression
        _row(id=13, plate="CCC333", fuel_gallons=0.0),  # fuel_zero_with_km
    ]
    report = rp.revalidate_rows(rows, from_month="2026-07", to_month="2026-07")
    cur = FakeCursor()
    applied = rp.apply_changes(cur, report.changes)

    assert applied == 2
    assert len(cur.calls) == 2
    by_id = {params[-1]: (sql, params) for sql, params in cur.calls}
    assert set(by_id) == {12, 13}

    sql, params = by_id[12]
    assert "UPDATE monthly_vehicle_performance" in sql
    assert "calculation_status = %s" in sql
    assert "updated_at = NOW()" in sql
    assert sql.strip().endswith("WHERE id = %s")
    status, warnings, flags, kms_ecm, hours_ecm, fuel, row_id = params
    assert status == "partial"
    assert isinstance(warnings, Jsonb) and any("Plausibilidad" in w for w in warnings.obj)
    assert isinstance(flags, Jsonb) and flags.obj == ["odo_regression"]
    assert kms_ecm is None  # anulado
    assert hours_ecm == 50.0
    assert fuel == 100.0
    assert row_id == 12

    status, warnings, flags, kms_ecm, hours_ecm, fuel, row_id = by_id[13][1]
    assert status == "partial"
    assert flags.obj == ["fuel_zero_with_km"]
    assert kms_ecm == 1000.0
    assert fuel is None  # anulado
    assert row_id == 13


def test_apply_never_touches_non_evaluable_statuses() -> None:
    rows = [
        _row(id=21, calculation_status="unbound", odo_start=9.0, odo_end=1.0, fuel_gallons=-1.0),
        _row(id=22, calculation_status="no_data", odo_start=9.0, odo_end=1.0),
        _row(id=23, calculation_status="error", fuel_gallons=-1.0),
    ]
    report = rp.revalidate_rows(rows, from_month="2026-07", to_month="2026-07")
    assert report.total.evaluated == 3
    assert report.changes == []
    cur = FakeCursor()
    assert rp.apply_changes(cur, report.changes) == 0
    assert cur.calls == []


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_previous_month_helper() -> None:
    assert rp.previous_month("2026-01") == "2025-12"
    assert rp.previous_month("2026-07") == "2026-06"
    assert rp.previous_month("2026-03") == "2026-02"


def test_cli_parser_defaults_and_validation() -> None:
    parser = rp.build_parser()
    args = parser.parse_args([])
    assert args.from_month is None and args.to_month is None
    assert args.apply is False and args.as_json is False and args.customer_database_id is None

    args = parser.parse_args(["--from", "2026-06", "--to", "2026-09", "--apply", "--customer-database-id", "3", "--json"])
    assert args.from_month == "2026-06" and args.to_month == "2026-09"
    assert args.apply and args.as_json and args.customer_database_id == 3

    with pytest.raises(SystemExit):
        parser.parse_args(["--from", "junio"])


def test_run_dry_run_and_apply_with_fake_connection(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [
        _row(id=1, plate="AAA111", period_month="2026-06"),
        _row(id=2, plate="BBB222", period_month="2026-07", odo_start=5000.0, odo_end=4000.0, kms_ecm=900.0),
    ]
    executed: list[tuple[str, tuple[Any, ...]]] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, tuple(params) if params is not None else ()))

        def fetchall(self):
            return list(rows)

    class _Conn:
        def cursor(self):
            return _Cursor()

    from contextlib import contextmanager

    @contextmanager
    def _fake_db_conn(row_factory=None):
        yield _Conn()

    monkeypatch.setattr(rp, "db_conn", _fake_db_conn)

    # Dry-run: solo SELECT (con el mes previo incluido en los params).
    report = rp.run(["--from", "2026-07", "--to", "2026-07"])
    assert report.applied is None
    assert report.total.evaluated == 1
    assert len(executed) == 1
    assert executed[0][1] == ("2026-06", "2026-07")
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "BBB222" in out

    # --json imprime un documento JSON.
    executed.clear()
    rp.run(["--from", "2026-07", "--to", "2026-07", "--json", "--customer-database-id", "5"])
    assert executed[0][1] == ("2026-06", "2026-07", 5)
    assert "customer_database_id = %s" in executed[0][0]
    doc = json.loads(capsys.readouterr().out)
    assert doc["total"]["status_changed"] == 1

    # --apply: SELECT + 1 UPDATE.
    executed.clear()
    report = rp.run(["--from", "2026-07", "--to", "2026-07", "--apply"])
    assert report.applied == 1
    updates = [c for c in executed if c[0].lstrip().startswith("UPDATE")]
    assert len(updates) == 1
    assert updates[0][1][-1] == 2
    assert updates[0][1][0] == "partial"
    assert "Filas actualizadas: 1" in capsys.readouterr().out

    assert rp.main(["--from", "2026-07", "--to", "2026-07"]) == 0
