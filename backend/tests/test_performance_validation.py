"""Tests de la capa de plausibilidad de Rendimientos
(app/services/performance_validation.py).

Logica pura sobre MonthlyPerformanceRecord: no requiere base de datos.
"""
from __future__ import annotations

import pytest

from app.schemas.vehicle import MonthlyPerformanceRecord
from app.services.performance_validation import (
    ALL_FLAGS,
    WARNING_PREFIX,
    PlausibilityThresholds,
    days_in_month,
    validate_record,
)

DAYS = 31  # 2026-08


def make(**overrides) -> MonthlyPerformanceRecord:
    base = dict(
        customer_database_id=1,
        source_provider="geotab",
        plate="ABC123",
        period_month="2026-08",
        calculation_status="calculated",
    )
    base.update(overrides)
    return MonthlyPerformanceRecord(**base)


def plaus_warnings(rec: MonthlyPerformanceRecord) -> list[str]:
    return [w for w in rec.warnings if w.startswith(WARNING_PREFIX)]


# --------------------------------------------------------------------------
# Helpers exportados
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "period,expected",
    [("2026-01", 31), ("2026-02", 28), ("2024-02", 29), ("2026-04", 30), ("2026-12", 31)],
)
def test_days_in_month(period, expected):
    assert days_in_month(period) == expected


def test_days_in_month_invalid_raises():
    with pytest.raises(ValueError):
        days_in_month("2026-13")


# --------------------------------------------------------------------------
# Registro sano: no se toca nada
# --------------------------------------------------------------------------


def test_clean_record_unchanged():
    rec = make(
        odo_start=100_000,
        odo_end=104_500,
        horo_start=5_000,
        horo_end=5_120,
        kms_ecm=4_500,
        kms_gps=4_400,
        hours_ecm=120,
        hours_gps=118,
        fuel_gallons=300,
    )
    out = validate_record(rec, days_in_month=DAYS)
    assert out.calculation_status == "calculated"
    assert out.validation_flags == []
    assert plaus_warnings(out) == []
    assert out.kms_ecm == 4_500 and out.fuel_gallons == 300


# --------------------------------------------------------------------------
# Una prueba por regla
# --------------------------------------------------------------------------


def test_odo_regression_nulls_kms_and_downgrades():
    rec = make(odo_start=500_000, odo_end=499_000, kms_ecm=0, hours_ecm=5)
    out = validate_record(rec, days_in_month=DAYS)
    assert "odo_regression" in out.validation_flags
    assert out.kms_ecm is None
    assert out.calculation_status == "partial"
    assert any("odometro final" in w for w in plaus_warnings(out))


def test_horo_regression_nulls_hours_and_downgrades():
    rec = make(horo_start=8_000, horo_end=7_900, hours_ecm=0, kms_ecm=10)
    out = validate_record(rec, days_in_month=DAYS)
    assert "horo_regression" in out.validation_flags
    assert out.hours_ecm is None
    assert out.calculation_status == "partial"


def test_km_over_max_comercial_partial():
    out = validate_record(make(kms_ecm=1_700_000), days_in_month=DAYS)
    assert "km_over_max" in out.validation_flags
    assert out.calculation_status == "partial"
    assert any("1.700.000" in w and "30.000" in w for w in plaus_warnings(out))


def test_km_over_max_vocacional_threshold():
    # 20k km: pasa en comercial (30k), falla en vocacional (15k).
    assert "km_over_max" not in validate_record(make(kms_ecm=20_000), days_in_month=DAYS).validation_flags
    out = validate_record(make(kms_ecm=20_000, vocacional=True), days_in_month=DAYS)
    assert "km_over_max" in out.validation_flags
    assert out.calculation_status == "partial"


def test_km_gps_over_max_is_warning_only():
    out = validate_record(make(kms_gps=45_000), days_in_month=DAYS)
    assert "km_gps_over_max" in out.validation_flags
    assert out.calculation_status == "calculated"


def test_hours_over_month_partial():
    out = validate_record(make(hours_ecm=23_000), days_in_month=DAYS)
    assert "hours_over_month" in out.validation_flags
    assert out.calculation_status == "partial"


def test_hours_over_month_applies_to_gps_too():
    out = validate_record(make(hours_gps=24 * DAYS + 1), days_in_month=DAYS)
    assert "hours_over_month" in out.validation_flags
    assert out.calculation_status == "partial"


def test_hours_high_warning_between_18_and_24_per_day():
    out = validate_record(make(hours_ecm=20 * DAYS), days_in_month=DAYS)
    assert "hours_high" in out.validation_flags
    assert "hours_over_month" not in out.validation_flags
    assert out.calculation_status == "calculated"


def test_hours_respects_days_in_month():
    # 24*28 = 672 h: valido en un mes de 31 dias, imposible en febrero.
    rec = make(hours_ecm=700)
    assert "hours_over_month" not in validate_record(rec, days_in_month=31).validation_flags
    assert "hours_over_month" in validate_record(rec, days_in_month=28).validation_flags


def test_kmh_implausible_too_fast():
    out = validate_record(make(kms_ecm=12_000, hours_ecm=100), days_in_month=DAYS)  # 120 km/h
    assert "kmh_implausible" in out.validation_flags
    assert out.calculation_status == "calculated"


def test_kmh_implausible_too_slow_only_with_enough_km():
    slow = validate_record(make(kms_ecm=300, hours_ecm=300), days_in_month=DAYS)  # 1 km/h, 300 km
    assert "kmh_implausible" in slow.validation_flags
    few = validate_record(make(kms_ecm=100, hours_ecm=300), days_in_month=DAYS)  # 1 km/h pero <200 km
    assert "kmh_implausible" not in few.validation_flags


def test_kmh_skipped_when_hours_zero_or_none():
    out = validate_record(make(kms_ecm=10, hours_ecm=0), days_in_month=DAYS)
    assert "kmh_implausible" not in out.validation_flags
    out = validate_record(make(kms_ecm=1_000, hours_ecm=None), days_in_month=DAYS)
    assert "kmh_implausible" not in out.validation_flags


def test_kpg_out_of_range_warning():
    out = validate_record(make(kms_ecm=1_000, fuel_gallons=1_200), days_in_month=DAYS)  # 0.83 km/gal
    assert "kpg_out_of_range" in out.validation_flags
    assert out.calculation_status == "calculated"


def test_kpg_out_of_range_hard_partial():
    out = validate_record(make(kms_ecm=1_000, fuel_gallons=5), days_in_month=DAYS)  # 200 km/gal
    assert "kpg_out_of_range" in out.validation_flags
    assert out.calculation_status == "partial"


def test_kpg_within_range_no_flag():
    out = validate_record(make(kms_ecm=4_000, fuel_gallons=250), days_in_month=DAYS)  # 16 km/gal
    assert "kpg_out_of_range" not in out.validation_flags


def test_gph_out_of_range_only_vocacional():
    com = validate_record(make(fuel_gallons=3_000, hours_ecm=100), days_in_month=DAYS)  # 30 gal/h
    assert "gph_out_of_range" not in com.validation_flags
    voc = validate_record(make(fuel_gallons=3_000, hours_ecm=100, vocacional=True), days_in_month=DAYS)
    assert "gph_out_of_range" in voc.validation_flags
    assert voc.calculation_status == "calculated"


def test_fuel_over_max_partial():
    out = validate_record(make(fuel_gallons=4_500), days_in_month=DAYS)
    assert "fuel_over_max" in out.validation_flags
    assert out.calculation_status == "partial"


def test_ecm_gps_divergence_warning_and_partial():
    warn = validate_record(make(kms_ecm=1_000, kms_gps=800), days_in_month=DAYS)  # 20%
    assert "ecm_gps_divergence" in warn.validation_flags
    assert warn.calculation_status == "calculated"

    part = validate_record(make(kms_ecm=1_000, kms_gps=500), days_in_month=DAYS)  # 50%
    assert "ecm_gps_divergence" in part.validation_flags
    assert part.calculation_status == "partial"

    small = validate_record(make(kms_ecm=90, kms_gps=40), days_in_month=DAYS)  # ambos <=100 km
    assert "ecm_gps_divergence" not in small.validation_flags


def test_hours_ecm_gps_divergence_warning():
    out = validate_record(make(hours_ecm=100, hours_gps=60), days_in_month=DAYS)  # 40%
    assert "hours_ecm_gps_divergence" in out.validation_flags
    assert out.calculation_status == "calculated"
    ok = validate_record(make(hours_ecm=100, hours_gps=90), days_in_month=DAYS)  # 10%
    assert "hours_ecm_gps_divergence" not in ok.validation_flags


def test_km_hours_incoherent_zero_km_with_hours():
    out = validate_record(make(kms_ecm=0, hours_ecm=50), days_in_month=DAYS)
    assert "km_hours_incoherent" in out.validation_flags
    assert out.calculation_status == "partial"


def test_km_hours_incoherent_km_without_hours():
    out = validate_record(make(kms_ecm=500, hours_ecm=0), days_in_month=DAYS)
    assert "km_hours_incoherent" in out.validation_flags
    assert out.calculation_status == "partial"
    ok = validate_record(make(kms_ecm=30, hours_ecm=0), days_in_month=DAYS)
    assert "km_hours_incoherent" not in ok.validation_flags


def test_fuel_zero_with_km_nulls_fuel():
    out = validate_record(make(kms_ecm=2_000, hours_ecm=60, fuel_gallons=0), days_in_month=DAYS)
    assert "fuel_zero_with_km" in out.validation_flags
    assert out.fuel_gallons is None
    assert out.calculation_status == "partial"


def test_chain_broken_odo_and_horo():
    prev = make(period_month="2026-07", odo_end=100_000, horo_end=5_000)
    rec = make(odo_start=100_250, horo_start=5_000.05)  # odo rompe, horo dentro de tolerancia
    out = validate_record(rec, days_in_month=DAYS, previous=prev)
    assert "chain_broken" in out.validation_flags
    assert out.calculation_status == "calculated"
    assert sum("horometro inicial" in w for w in plaus_warnings(out)) == 0
    assert sum("odometro inicial" in w for w in plaus_warnings(out)) == 1

    rec2 = make(odo_start=100_000.5, horo_start=5_001)
    out2 = validate_record(rec2, days_in_month=DAYS, previous=prev)
    assert sum("horometro inicial" in w for w in plaus_warnings(out2)) == 1
    assert sum("odometro inicial" in w for w in plaus_warnings(out2)) == 0


def test_chain_ok_or_no_previous():
    prev = make(period_month="2026-07", odo_end=100_000, horo_end=5_000)
    ok = validate_record(make(odo_start=100_000.4, horo_start=5_000.05), days_in_month=DAYS, previous=prev)
    assert "chain_broken" not in ok.validation_flags
    none = validate_record(make(odo_start=1), days_in_month=DAYS, previous=None)
    assert "chain_broken" not in none.validation_flags
    prev_no_end = make(period_month="2026-07", odo_end=None)
    assert "chain_broken" not in validate_record(make(odo_start=1), days_in_month=DAYS, previous=prev_no_end).validation_flags


def test_regression_significant_km_and_hours():
    km = validate_record(make(kms_ecm=1_000, geotab_regression_total_km=80), days_in_month=DAYS)  # 8%
    assert "regression_significant" in km.validation_flags
    assert km.calculation_status == "partial"

    ok = validate_record(make(kms_ecm=1_000, geotab_regression_total_km=30), days_in_month=DAYS)  # 3%
    assert "regression_significant" not in ok.validation_flags

    hrs = validate_record(make(hours_ecm=100, geotab_regression_total_hours=10), days_in_month=DAYS)  # 10%
    assert "regression_significant" in hrs.validation_flags
    assert hrs.calculation_status == "partial"


def test_source_mix_warning():
    out = validate_record(make(odo_start_source="ecm", odo_end_source="gps"), days_in_month=DAYS)
    assert "source_mix" in out.validation_flags
    assert out.calculation_status == "calculated"
    same = validate_record(make(odo_start_source="ecm", odo_end_source="ecm"), days_in_month=DAYS)
    assert "source_mix" not in same.validation_flags
    horo = validate_record(make(horo_start_source="ecm", horo_end_source="status_data"), days_in_month=DAYS)
    assert "source_mix" in horo.validation_flags


def test_negative_value_is_error():
    out = validate_record(make(kms_ecm=-120, hours_ecm=10), days_in_month=DAYS)
    assert "negative_value" in out.validation_flags
    assert out.calculation_status == "error"
    assert any("kms_ecm" in w for w in plaus_warnings(out))
    # la metrica negativa se anula para no violar mvp_nonneg_chk al persistir
    assert out.kms_ecm is None
    assert out.hours_ecm == 10


def test_negative_fuel_end_is_error():
    out = validate_record(make(fuel_end=-1), days_in_month=DAYS)
    assert out.calculation_status == "error"
    assert out.fuel_end is None


def test_negative_odo_kept_but_flagged():
    # odo_* no esta en el CHECK: se conserva el valor crudo, solo se marca error
    out = validate_record(make(odo_start=-5, odo_end=100, kms_ecm=105), days_in_month=DAYS)
    assert out.calculation_status == "error"
    assert out.odo_start == -5
    assert out.kms_ecm == 105


# --------------------------------------------------------------------------
# Semantica de estados
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["unbound", "no_data", "error"])
def test_untouched_for_non_evaluable_statuses(status):
    rec = make(calculation_status=status, kms_ecm=9_999_999, hours_ecm=-5, warnings=["previo"])
    out = validate_record(rec, days_in_month=DAYS)
    assert out == rec
    assert out.validation_flags == []


def test_partial_input_stays_partial_and_never_upgrades():
    rec = make(calculation_status="partial", kms_ecm=4_000, hours_ecm=100, fuel_gallons=250)
    out = validate_record(rec, days_in_month=DAYS)
    assert out.calculation_status == "partial"
    assert out.validation_flags == []


def test_highest_action_wins():
    # warning (kmh) + partial (km_over_max) + error (negative) -> error.
    rec = make(kms_ecm=50_000, hours_ecm=100, hours_gps=-1)
    out = validate_record(rec, days_in_month=DAYS)
    assert out.calculation_status == "error"
    assert {"km_over_max", "negative_value"} <= set(out.validation_flags)


def test_preserves_provider_warnings():
    rec = make(kms_ecm=50_000, warnings=["Geotab: sin datos del dia 3"])
    out = validate_record(rec, days_in_month=DAYS)
    assert out.warnings[0] == "Geotab: sin datos del dia 3"
    assert all(w.startswith(WARNING_PREFIX) for w in out.warnings[1:])


def test_preserves_foreign_validation_flags():
    rec = make(kms_ecm=50_000, validation_flags=["custom_flag"])
    out = validate_record(rec, days_in_month=DAYS)
    assert out.validation_flags[0] == "custom_flag"
    assert "km_over_max" in out.validation_flags


def test_does_not_mutate_input():
    rec = make(odo_start=10, odo_end=5, kms_ecm=0, fuel_gallons=0, hours_ecm=20)
    validate_record(rec, days_in_month=DAYS)
    assert rec.kms_ecm == 0 and rec.fuel_gallons == 0
    assert rec.calculation_status == "calculated"
    assert rec.validation_flags == [] and rec.warnings == []


# --------------------------------------------------------------------------
# Idempotencia
# --------------------------------------------------------------------------


def test_idempotent_complex_record():
    prev = make(period_month="2026-07", odo_end=90_000, horo_end=4_000)
    rec = make(
        odo_start=100_000,
        odo_end=99_000,  # odo_regression
        horo_start=4_000,
        horo_end=4_100,
        kms_ecm=0,
        kms_gps=45_000,  # km_gps_over_max
        hours_ecm=100,
        hours_gps=60,  # hours divergence
        fuel_gallons=0,
        odo_start_source="ecm",
        odo_end_source="gps",  # source_mix
        warnings=["Geotab: aviso del proveedor"],
    )
    once = validate_record(rec, days_in_month=DAYS, previous=prev)
    twice = validate_record(once, days_in_month=DAYS, previous=prev)
    thrice = validate_record(twice, days_in_month=DAYS, previous=prev)
    assert once == twice == thrice
    assert once.calculation_status == "partial"
    assert once.warnings[0] == "Geotab: aviso del proveedor"
    # ningun warning duplicado
    assert len(once.warnings) == len(set(once.warnings))
    assert len(once.validation_flags) == len(set(once.validation_flags))


def test_idempotent_fuel_zero_with_km():
    # La anulacion de fuel destruye la evidencia: la flag debe sobrevivir a la reevaluacion.
    rec = make(kms_ecm=2_000, hours_ecm=60, fuel_gallons=0)
    once = validate_record(rec, days_in_month=DAYS)
    twice = validate_record(once, days_in_month=DAYS)
    assert once == twice
    assert "fuel_zero_with_km" in twice.validation_flags
    assert twice.fuel_gallons is None


def test_idempotent_error_record():
    rec = make(kms_ecm=-5)
    once = validate_record(rec, days_in_month=DAYS)
    assert once.calculation_status == "error"
    twice = validate_record(once, days_in_month=DAYS)
    assert once == twice


# --------------------------------------------------------------------------
# Overrides
# --------------------------------------------------------------------------


def test_thresholds_defaults_and_vocacional():
    com = PlausibilityThresholds.from_overrides(None, vocacional=False)
    assert com.max_km_month == 30_000
    voc = PlausibilityThresholds.from_overrides(None, vocacional=True)
    assert voc.max_km_month == 15_000


def test_thresholds_overrides_known_keys_only():
    t = PlausibilityThresholds.from_overrides(
        {"max_km_month": 50_000, "unknown": 1, "kpg_warn_max": "80", "max_fuel_gallons": "abc"},
        vocacional=False,
    )
    assert t.max_km_month == 50_000
    assert t.kpg_warn_max == 80.0
    assert t.max_fuel_gallons == 4_000  # valor no numerico ignorado
    assert not hasattr(t, "unknown")


def test_thresholds_vocacional_override_propagates():
    t = PlausibilityThresholds.from_overrides({"max_km_month_vocacional": 20_000}, vocacional=True)
    assert t.max_km_month == 20_000


def test_validate_record_uses_overrides():
    rec = make(kms_ecm=40_000)
    assert "km_over_max" in validate_record(rec, days_in_month=DAYS).validation_flags
    out = validate_record(rec, days_in_month=DAYS, overrides={"max_km_month": 50_000})
    assert "km_over_max" not in out.validation_flags
    assert out.calculation_status == "calculated"


def test_validate_record_ignores_garbage_overrides():
    rec = make(kms_ecm=40_000)
    out = validate_record(rec, days_in_month=DAYS, overrides="not-a-dict")  # type: ignore[arg-type]
    assert "km_over_max" in out.validation_flags


def test_all_emitted_flags_are_declared():
    """Toda flag emitida debe estar en ALL_FLAGS, si no la limpieza idempotente la dejaria pegada."""
    prev = make(period_month="2026-07", odo_end=1, horo_end=1)
    rec = make(
        odo_start=100, odo_end=50, horo_start=100, horo_end=50,
        kms_ecm=-1, kms_gps=50_000, hours_ecm=30_000, hours_gps=25_000, fuel_gallons=5_000,
        odo_start_source="a", odo_end_source="b", geotab_regression_total_km=10,
    )
    out = validate_record(rec, days_in_month=DAYS, previous=prev)
    assert set(out.validation_flags) <= ALL_FLAGS
