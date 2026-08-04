from __future__ import annotations

import pytest

import app.services.cpk_cph as cpk_cph
from app.services.cpk_cph import (
    CpkCphConflict,
    _enrich_geotab_granular,
    _needs_granular_lookup,
    _reject_geotab_regression,
    _row_from_preview,
)
from app.services.performance_providers import _geotab_regression_warnings
from app.services.performance_providers import _analyze_geotab_regressions
from app.services.rendimientos import GeotabGranularRegressions


def _row(**overrides):
    row = {
        "plate": "TST001",
        "cutoff_start_at": "2026-06-01",
        "cutoff_end_at": "2026-06-30",
        "source_provider": "geotab",
        "vocacional": False,
        "odo_start": 1000,
        "odo_end": 900,
        "horo_start": 200,
        "horo_end": 210,
        "kms_ecm_geotab": 0,
        "kms_gps": 100,
        "hours_ecm": 10,
        "hours_gps": 10,
        "km_adjustment": 0,
        "hour_adjustment": 0,
        "warnings": [],
    }
    row.update(overrides)
    return row


def test_geotab_high_difference_with_odometer_regression_is_blocked():
    row = _row_from_preview(_row())

    assert any("Revisión Geotab" in warning for warning in row["warnings"])
    assert any("odómetro retrocede" in warning for warning in row["warnings"])
    with pytest.raises(CpkCphConflict, match="retroceso"):
        _reject_geotab_regression(row)


def test_geotab_high_difference_without_regression_is_only_a_warning():
    row = _row_from_preview(_row(odo_end=1100))

    assert any("Revisión Geotab" in warning for warning in row["warnings"])
    _reject_geotab_regression(row)


def test_non_geotab_rows_are_not_checked_by_geotab_rule():
    row = _row_from_preview(_row(source_provider="artimo"))

    assert not any("Geotab" in warning for warning in row["warnings"])
    _reject_geotab_regression(row)


def test_geotab_regression_is_ignored_until_difference_exceeds_threshold():
    row = _row_from_preview(_row(kms_ecm_geotab=96, kms_gps=100))

    assert not any("Geotab" in warning for warning in row["warnings"])
    _reject_geotab_regression(row)


def test_status_data_sequence_detects_intermediate_drop_even_when_final_value_is_higher():
    readings = [
        {"dateTime": "2026-06-01T00:00:00Z", "data": 100_000_000},
        {"dateTime": "2026-06-02T00:00:00Z", "data": 100_100_000},
        {"dateTime": "2026-06-03T00:00:00Z", "data": 100_040_000},
        {"dateTime": "2026-06-04T00:00:00Z", "data": 100_200_000},
    ]

    warnings = _geotab_regression_warnings(
        readings,
        label="odómetro",
        divisor=1000,
        unit="km",
    )

    assert len(warnings) == 1
    assert "100100 → 100040 km" in warnings[0]
    assert "caída de 60 km" in warnings[0]
    assert "2026-06-02" in warnings[0]
    assert "2026-06-03" in warnings[0]


def test_cpk_blocks_chronological_regression_when_difference_is_over_threshold():
    row = _row_from_preview(
        _row(
            odo_end=1100,
            warnings=[
                "Retroceso Geotab detectado [acumulado] (odómetro): 1050 → 990 km "
                "(caída de 60 km) entre 2026-06-10 y 2026-06-11."
            ],
            geotab_regression_count=1,
            geotab_regression_total_km=60,
            suggested_adjustment=60,
        )
    )

    with pytest.raises(CpkCphConflict, match="retroceso"):
        _reject_geotab_regression(row)


def test_every_geotab_drop_generates_suggested_adjustment():
    readings = [
        {"dateTime": "2026-06-01T00:00:00Z", "data": 100_000_000},
        {"dateTime": "2026-06-02T00:00:00Z", "data": 100_100_000},
        {"dateTime": "2026-06-03T00:00:00Z", "data": 100_040_000},
        {"dateTime": "2026-06-04T00:00:00Z", "data": 100_041_000},
        {"dateTime": "2026-06-05T00:00:00Z", "data": 100_042_000},
    ]

    analysis = _analyze_geotab_regressions(
        readings,
        label="odómetro",
        divisor=1000,
        unit="km",
    )

    assert analysis.count == 1
    assert analysis.total == 60
    assert any("[acumulado]" in warning for warning in analysis.warnings)


def test_cpk_accepts_detected_regression_when_suggested_adjustment_is_applied():
    row = _row_from_preview(
        _row(
            odo_end=1100,
            kms_ecm_geotab=40,
            kms_gps=100,
            warnings=[
                "Retroceso Geotab detectado [acumulado] (odómetro): 1050 → 990 km "
                "(caída de 60 km) entre 2026-06-10 y 2026-06-11."
            ],
            geotab_regression_count=1,
            geotab_regression_total_km=60,
            suggested_adjustment=60,
            km_adjustment=60,
            correction_note="Ajuste sugerido por retroceso Geotab.",
        )
    )

    _reject_geotab_regression(row)


def test_small_or_transient_drop_is_also_added_to_suggested_adjustment():
    readings = [
        {"dateTime": "2026-06-01T00:00:00Z", "data": 100_000_000},
        {"dateTime": "2026-06-02T00:00:00Z", "data": 99_998_000},
        {"dateTime": "2026-06-03T00:00:00Z", "data": 100_001_000},
        {"dateTime": "2026-06-04T00:00:00Z", "data": 100_002_000},
    ]

    analysis = _analyze_geotab_regressions(
        readings,
        label="odómetro",
        divisor=1000,
        unit="km",
    )

    assert analysis.count == 1
    assert analysis.total == 2


def test_regression_on_the_other_meter_does_not_block_the_row():
    """Fila comercial (ajuste en km): un retroceso de horómetro nunca lo cubre
    el ajuste sugerido en km, así que solo puede advertir."""
    row = _row_from_preview(
        _row(
            odo_end=1100,
            horo_end=190,
            warnings=[
                "Retroceso Geotab detectado [acumulado] (horómetro): 200 → 190 h "
                "(caída de 10 h) entre 2026-06-10 y 2026-06-11."
            ],
            geotab_regression_count=1,
            geotab_regression_total_hours=10,
            suggested_adjustment=0,
        )
    )

    assert any("otro medidor" in warning for warning in row["warnings"])
    _reject_geotab_regression(row)


def test_blocking_message_names_the_plate_and_the_reason():
    row = _row_from_preview(_row(plate="ABC123"))

    with pytest.raises(CpkCphConflict) as excinfo:
        _reject_geotab_regression(row)
    assert "ABC123" in str(excinfo.value)
    assert "odómetro retrocede de 1000 a 900 km" in str(excinfo.value)


def test_regression_override_with_note_allows_saving():
    row = _row_from_preview(
        _row(regression_override=True, correction_note="Cambio de ECM el 12/06.")
    )

    _reject_geotab_regression(row)
    assert any("aceptado manualmente" in warning for warning in row["warnings"])


def test_regression_override_without_note_still_blocks():
    row = _row_from_preview(_row(regression_override=True))

    with pytest.raises(CpkCphConflict, match="nota"):
        _reject_geotab_regression(row)


def test_persisted_override_marker_survives_a_reload():
    """Al releer la fila guardada el flag ya no viaja, pero el marcador en
    warnings mantiene la aceptación."""
    saved = _row_from_preview(
        _row(regression_override=True, correction_note="Cambio de ECM el 12/06.")
    )
    reloaded = _row_from_preview({**saved, "regression_override": False})

    _reject_geotab_regression(reloaded)


def _stale_monthly_row(**overrides):
    """Fila típica de datos mensuales calculados antes de la métrica de retrocesos:
    diferencia alta, métricas en cero, sin warnings [acumulado]."""
    return _row_from_preview(
        _row(
            odo_end=1100,
            kms_ecm_geotab=50,
            kms_gps=100,
            **overrides,
        )
    )


def test_stale_monthly_row_with_high_diff_needs_granular_lookup():
    row = _stale_monthly_row()

    assert _needs_granular_lookup(row)


def test_low_diff_row_does_not_need_granular_lookup():
    row = _row_from_preview(_row(odo_end=1100, kms_ecm_geotab=98, kms_gps=100))

    assert not _needs_granular_lookup(row)


def test_row_with_existing_metrics_does_not_need_granular_lookup():
    row = _stale_monthly_row(
        geotab_regression_count=2,
        geotab_regression_total_km=40,
        suggested_adjustment=40,
    )

    assert not _needs_granular_lookup(row)


def test_enrichment_fills_metrics_and_applies_suggested_adjustment(monkeypatch):
    monkeypatch.setattr(
        cpk_cph,
        "lookup_geotab_granular_regressions",
        lambda **kwargs: GeotabGranularRegressions(
            odometer_count=2,
            odometer_total_km=50,
            hourmeter_count=0,
            hourmeter_total_hours=0,
            warnings=[
                "Retroceso Geotab detectado [acumulado] (odómetro): 1050 → 1020 km "
                "(caída de 30 km) entre 2026-06-10 y 2026-06-11.",
                "Retroceso Geotab detectado [acumulado] (odómetro): 1060 → 1040 km "
                "(caída de 20 km) entre 2026-06-15 y 2026-06-16.",
            ],
        ),
    )
    row = _enrich_geotab_granular(_stale_monthly_row(), month="2026-06")

    assert row["geotab_regression_count"] == 2
    assert row["geotab_regression_total_km"] == 50
    assert row["suggested_adjustment"] == 50
    assert row["km_adjustment"] == 50
    assert row["kms_ecm_approved"] == 100
    assert row["km_difference_pct"] == 0
    assert "retroceso(s) Geotab detectado(s)" in row["correction_note"]
    assert any("Verificación granular Geotab" in warning for warning in row["warnings"])
    _reject_geotab_regression(row)


def test_enrichment_without_regressions_marks_row_as_checked(monkeypatch):
    monkeypatch.setattr(
        cpk_cph,
        "lookup_geotab_granular_regressions",
        lambda **kwargs: GeotabGranularRegressions(
            odometer_count=0,
            odometer_total_km=0,
            hourmeter_count=0,
            hourmeter_total_hours=0,
            warnings=[],
        ),
    )
    row = _enrich_geotab_granular(_stale_monthly_row(), month="2026-06")

    assert row["geotab_regression_count"] == 0
    assert row["km_adjustment"] == 0
    assert any("sin retrocesos en el periodo" in warning for warning in row["warnings"])
    assert not _needs_granular_lookup(row)
    _reject_geotab_regression(row)


def test_enrichment_failure_adds_warning_and_keeps_row(monkeypatch):
    def _boom(**kwargs):
        raise ValueError("credenciales incompletas")

    monkeypatch.setattr(cpk_cph, "lookup_geotab_granular_regressions", _boom)
    row = _enrich_geotab_granular(_stale_monthly_row(), month="2026-06")

    assert any("No fue posible verificar retrocesos granulares" in warning for warning in row["warnings"])
    assert row["geotab_regression_count"] == 0
    _reject_geotab_regression(row)
