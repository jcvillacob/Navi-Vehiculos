"""Capa de plausibilidad para los registros mensuales de Rendimientos.

Funciones puras, sin DB ni I/O. Reciben un ``MonthlyPerformanceRecord`` ya
calculado por el proveedor (Geotab, Frotcom, Artimo, ...) y devuelven una copia
con:

- ``warnings`` enriquecido con mensajes ``"Plausibilidad: ..."`` (en espanol,
  con magnitudes) que el usuario ve en la UI.
- ``validation_flags`` con banderas cortas de maquina (``km_over_max``,
  ``odo_regression``, ...) para filtrar/agrupar.
- ``calculation_status`` degradado cuando corresponde: ``calculated -> partial``
  o ``* -> error``. Nunca se sube el estado.
- Metricas imposibles anuladas (``kms_ecm``/``hours_ecm`` en retrocesos de
  odometro/horometro; ``fuel_gallons`` = 0 con kilometros).

La funcion es idempotente: al reevaluar se descartan los warnings/flags que
la propia capa agrego antes, asi que correrla dos veces da el mismo resultado.
Solo evalua registros ``calculated`` o ``partial``; ``unbound``/``no_data``/
``error`` se devuelven intactos.

Los umbrales viven en ``PlausibilityThresholds`` y pueden sobreescribirse por
cliente via ``overrides`` (p. ej. ``customer_databases.provider_config
['plausibility_overrides']``), usando como claves los nombres de los campos.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any

from app.schemas.vehicle import MonthlyPerformanceRecord

logger = logging.getLogger(__name__)

WARNING_PREFIX = "Plausibilidad: "

# Estados sobre los que se evalua plausibilidad. El resto se deja intacto.
EVALUABLE_STATUSES: frozenset[str] = frozenset({"calculated", "partial"})

# Orden de severidad de las acciones: la mas alta gana. Nunca se sube el estado.
ACTION_RANK: dict[str, int] = {"warning": 0, "partial": 1, "error": 2}

# Campos numericos sobre los que aplica la regla de valor negativo.
METRIC_FIELDS: tuple[str, ...] = (
    "odo_start",
    "odo_end",
    "horo_start",
    "horo_end",
    "kms_ecm",
    "kms_gps",
    "hours_ecm",
    "hours_gps",
    "fuel_gallons",
    "fuel_end",
)

# Metricas cubiertas por el CHECK mvp_nonneg_chk: nunca deben persistirse negativas.
_NULLABLE_ON_NEGATIVE = ("kms_ecm", "kms_gps", "hours_ecm", "hours_gps", "fuel_gallons", "fuel_end")

# Todas las flags que puede emitir esta capa (documentacion + limpieza idempotente).
ALL_FLAGS: frozenset[str] = frozenset(
    {
        "odo_regression",
        "horo_regression",
        "km_over_max",
        "km_gps_over_max",
        "hours_over_month",
        "hours_high",
        "kmh_implausible",
        "kpg_out_of_range",
        "gph_out_of_range",
        "fuel_over_max",
        "ecm_gps_divergence",
        "hours_ecm_gps_divergence",
        "km_hours_incoherent",
        "fuel_zero_with_km",
        "chain_broken",
        "regression_significant",
        "source_mix",
        "negative_value",
    }
)


@dataclass(frozen=True)
class PlausibilityThresholds:
    """Limites numericos de las reglas. Los defaults son los de flota comercial;
    ``from_overrides(..., vocacional=True)`` aplica las variantes vocacionales."""

    # Kilometros maximos por mes (ECM y GPS comparten el limite).
    max_km_month: float = 30000.0
    max_km_month_vocacional: float = 15000.0
    # Horas por dia: >24 es fisicamente imposible (partial); >18 es sospechoso (warning).
    max_hours_per_day: float = 24.0
    high_hours_per_day: float = 18.0
    # Velocidad promedio km/h = kms_ecm / hours_ecm.
    max_avg_kmh: float = 110.0
    min_avg_kmh: float = 2.0
    min_km_for_kmh_check: float = 200.0
    # Rendimiento km/gal: fuera del rango "warning" avisa; fuera del "hard" degrada.
    kpg_warn_min: float = 1.0
    kpg_warn_max: float = 60.0
    kpg_hard_min: float = 0.3
    kpg_hard_max: float = 120.0
    # Consumo gal/h (solo vocacional).
    gph_min: float = 0.2
    gph_max: float = 25.0
    # Combustible maximo por mes.
    max_fuel_gallons: float = 4000.0
    # Divergencia ECM vs GPS en km (desvio relativo |a-b|/max).
    km_divergence_min_km: float = 100.0
    km_divergence_warn: float = 0.15
    km_divergence_partial: float = 0.40
    # Divergencia ECM vs GPS en horas.
    hours_divergence_min_hours: float = 20.0
    hours_divergence_warn: float = 0.25
    # Incoherencia km vs horas.
    incoherent_hours_without_km: float = 10.0
    incoherent_km_without_hours: float = 50.0
    # Continuidad con el mes anterior (odo_start vs previous.odo_end).
    chain_tolerance_km: float = 1.0
    chain_tolerance_hours: float = 0.1
    # Retrocesos Geotab significativos: total retrocedido / metrica del mes.
    regression_significant_ratio: float = 0.05

    @classmethod
    def from_overrides(
        cls,
        overrides: dict[str, Any] | None,
        *,
        vocacional: bool = False,
    ) -> "PlausibilityThresholds":
        """Construye los umbrales aplicando la variante vocacional y luego los
        overrides (solo claves conocidas y valores numericos; el resto se ignora
        con un log, nunca se lanza)."""
        base = cls()
        if vocacional:
            base = replace(base, max_km_month=base.max_km_month_vocacional)
        if not overrides or not isinstance(overrides, dict):
            return base

        known = {f.name for f in fields(cls)}
        clean: dict[str, float] = {}
        for key, value in overrides.items():
            if key not in known:
                logger.debug("plausibility override ignorado (clave desconocida): %s", key)
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    logger.debug("plausibility override ignorado (valor no numerico): %s=%r", key, value)
                    continue
            clean[key] = float(value)
        if not clean:
            return base
        # Si se sobreescribe la variante vocacional y estamos en vocacional, debe
        # reflejarse tambien en el limite efectivo.
        if vocacional and "max_km_month_vocacional" in clean and "max_km_month" not in clean:
            clean["max_km_month"] = clean["max_km_month_vocacional"]
        return replace(base, **clean)


def days_in_month(period_month: str) -> int:
    """Dias del mes para un periodo ``YYYY-MM``. Lanza ValueError si el formato
    es invalido (el llamador ya valido el periodo antes de calcular)."""
    parsed = datetime.strptime((period_month or "").strip(), "%Y-%m")
    return calendar.monthrange(parsed.year, parsed.month)[1]


def _fmt(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "-"
    if abs(value - round(value)) < 1e-9 and abs(value) < 1e12:
        return f"{int(round(value)):,}".replace(",", ".")
    return f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _positive(value: float | None) -> bool:
    return value is not None and value > 0


def _rel_dev(a: float, b: float) -> float:
    top = max(abs(a), abs(b))
    if top <= 0:
        return 0.0
    return abs(a - b) / top


class _Collector:
    """Acumula warnings/flags/accion y las mutaciones de metricas de una corrida."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.flags: list[str] = []
        self.action_rank: int = -1
        self.updates: dict[str, Any] = {}

    def add(self, flag: str, message: str, action: str) -> None:
        if flag not in self.flags:
            self.flags.append(flag)
        text = f"{WARNING_PREFIX}{message}"
        if text not in self.warnings:
            self.warnings.append(text)
        self.action_rank = max(self.action_rank, ACTION_RANK[action])

    def set_metric(self, field: str, value: Any) -> None:
        self.updates[field] = value


def _strip_previous(record: MonthlyPerformanceRecord) -> tuple[list[str], list[str]]:
    """Quita lo que esta capa agrego en una corrida anterior (idempotencia)."""
    warnings = [w for w in (record.warnings or []) if not str(w).startswith(WARNING_PREFIX)]
    flags = [f for f in (record.validation_flags or []) if f not in ALL_FLAGS]
    return warnings, flags


def validate_record(
    record: MonthlyPerformanceRecord,
    *,
    days_in_month: int,
    previous: MonthlyPerformanceRecord | None = None,
    overrides: dict[str, Any] | None = None,
) -> MonthlyPerformanceRecord:
    """Evalua las reglas de plausibilidad y devuelve una copia del registro.

    Nunca lanza: cualquier excepcion inesperada se loguea y se devuelve el
    registro original sin cambios.
    """
    try:
        return _validate(record, days_in_month=days_in_month, previous=previous, overrides=overrides)
    except Exception:  # pragma: no cover - red de seguridad
        logger.exception(
            "plausibility: fallo evaluando %s %s; se devuelve el registro sin cambios",
            getattr(record, "plate", "?"),
            getattr(record, "period_month", "?"),
        )
        return record


def _validate(
    record: MonthlyPerformanceRecord,
    *,
    days_in_month: int,
    previous: MonthlyPerformanceRecord | None,
    overrides: dict[str, Any] | None,
) -> MonthlyPerformanceRecord:
    if record.calculation_status not in EVALUABLE_STATUSES:
        return record.model_copy()

    base_warnings, base_flags = _strip_previous(record)
    t = PlausibilityThresholds.from_overrides(overrides, vocacional=bool(record.vocacional))
    days = max(int(days_in_month or 0), 1)
    c = _Collector()

    # Valores "vivos": las reglas posteriores ven las anulaciones de las previas.
    odo_start, odo_end = record.odo_start, record.odo_end
    horo_start, horo_end = record.horo_start, record.horo_end
    kms_ecm, kms_gps = record.kms_ecm, record.kms_gps
    hours_ecm, hours_gps = record.hours_ecm, record.hours_gps
    fuel = record.fuel_gallons

    # 1. Valores negativos -> error. Se evalua sobre el registro original.
    negatives = [
        (name, getattr(record, name))
        for name in METRIC_FIELDS
        if getattr(record, name) is not None and getattr(record, name) < 0
    ]
    if negatives:
        detail = ", ".join(f"{name}={_fmt(val)}" for name, val in negatives)
        c.add("negative_value", f"valores negativos en {detail}; metricas anuladas", "error")
        # Se anulan las metricas negativas: el CHECK mvp_nonneg_chk de la tabla
        # rechazaria la fila y tumbaria el job completo. El valor crudo queda en el warning.
        for name, _val in negatives:
            if name in _NULLABLE_ON_NEGATIVE:
                c.set_metric(name, None)
        if "kms_ecm" in dict(negatives):
            kms_ecm = None
        if "kms_gps" in dict(negatives):
            kms_gps = None
        if "hours_ecm" in dict(negatives):
            hours_ecm = None
        if "hours_gps" in dict(negatives):
            hours_gps = None
        if "fuel_gallons" in dict(negatives):
            fuel = None

    # 2. Retrocesos de odometro/horometro -> anular ECM, partial.
    if odo_start is not None and odo_end is not None and odo_end < odo_start:
        c.add(
            "odo_regression",
            f"odometro final ({_fmt(odo_end)}) menor que inicial ({_fmt(odo_start)}); kms_ecm anulado",
            "partial",
        )
        kms_ecm = None
        c.set_metric("kms_ecm", None)
    if horo_start is not None and horo_end is not None and horo_end < horo_start:
        c.add(
            "horo_regression",
            f"horometro final ({_fmt(horo_end)}) menor que inicial ({_fmt(horo_start)}); hours_ecm anulado",
            "partial",
        )
        hours_ecm = None
        c.set_metric("hours_ecm", None)

    # 3. Kilometros por encima del maximo mensual.
    if kms_ecm is not None and kms_ecm > t.max_km_month:
        c.add(
            "km_over_max",
            f"kms_ecm {_fmt(kms_ecm)} km supera el maximo mensual de {_fmt(t.max_km_month)} km",
            "partial",
        )
    if kms_gps is not None and kms_gps > t.max_km_month:
        c.add(
            "km_gps_over_max",
            f"kms_gps {_fmt(kms_gps)} km supera el maximo mensual de {_fmt(t.max_km_month)} km",
            "warning",
        )

    # 4. Horas por encima de lo posible en el mes.
    max_hours = t.max_hours_per_day * days
    high_hours = t.high_hours_per_day * days
    for label, value in (("hours_ecm", hours_ecm), ("hours_gps", hours_gps)):
        if value is None:
            continue
        if value > max_hours:
            c.add(
                "hours_over_month",
                f"{label} {_fmt(value)} h supera las {_fmt(max_hours)} h posibles en {days} dias",
                "partial",
            )
        elif value > high_hours:
            c.add(
                "hours_high",
                f"{label} {_fmt(value)} h supera las {_fmt(high_hours)} h ({_fmt(t.high_hours_per_day)} h/dia) en {days} dias",
                "warning",
            )

    # 5. Velocidad promedio implausible.
    if _positive(kms_ecm) and _positive(hours_ecm):
        kmh = kms_ecm / hours_ecm
        if kmh > t.max_avg_kmh:
            c.add(
                "kmh_implausible",
                f"velocidad promedio {_fmt(kmh)} km/h supera {_fmt(t.max_avg_kmh)} km/h ({_fmt(kms_ecm)} km / {_fmt(hours_ecm)} h)",
                "warning",
            )
        elif kmh < t.min_avg_kmh and kms_ecm > t.min_km_for_kmh_check:
            c.add(
                "kmh_implausible",
                f"velocidad promedio {_fmt(kmh, 2)} km/h por debajo de {_fmt(t.min_avg_kmh)} km/h ({_fmt(kms_ecm)} km / {_fmt(hours_ecm)} h)",
                "warning",
            )

    # 6. Rendimiento km/gal.
    if _positive(kms_ecm) and _positive(fuel):
        kpg = kms_ecm / fuel
        if kpg < t.kpg_hard_min or kpg > t.kpg_hard_max:
            c.add(
                "kpg_out_of_range",
                f"rendimiento {_fmt(kpg, 2)} km/gal fuera del rango {_fmt(t.kpg_hard_min, 1)}-{_fmt(t.kpg_hard_max)} ({_fmt(kms_ecm)} km / {_fmt(fuel)} gal)",
                "partial",
            )
        elif kpg < t.kpg_warn_min or kpg > t.kpg_warn_max:
            c.add(
                "kpg_out_of_range",
                f"rendimiento {_fmt(kpg, 2)} km/gal fuera del rango esperado {_fmt(t.kpg_warn_min)}-{_fmt(t.kpg_warn_max)} ({_fmt(kms_ecm)} km / {_fmt(fuel)} gal)",
                "warning",
            )

    # 7. Consumo gal/h (vocacional).
    if record.vocacional and _positive(fuel) and _positive(hours_ecm):
        gph = fuel / hours_ecm
        if gph < t.gph_min or gph > t.gph_max:
            c.add(
                "gph_out_of_range",
                f"consumo {_fmt(gph, 2)} gal/h fuera del rango {_fmt(t.gph_min, 1)}-{_fmt(t.gph_max)} ({_fmt(fuel)} gal / {_fmt(hours_ecm)} h)",
                "warning",
            )

    # 8. Combustible maximo.
    if fuel is not None and fuel > t.max_fuel_gallons:
        c.add(
            "fuel_over_max",
            f"combustible {_fmt(fuel)} gal supera el maximo mensual de {_fmt(t.max_fuel_gallons)} gal",
            "partial",
        )

    # 9. Divergencia ECM vs GPS en km.
    if (
        kms_ecm is not None
        and kms_gps is not None
        and kms_ecm > t.km_divergence_min_km
        and kms_gps > t.km_divergence_min_km
    ):
        dev = _rel_dev(kms_ecm, kms_gps)
        if dev > t.km_divergence_partial:
            c.add(
                "ecm_gps_divergence",
                f"kms ECM ({_fmt(kms_ecm)}) y GPS ({_fmt(kms_gps)}) divergen {_fmt(dev * 100)}% (> {_fmt(t.km_divergence_partial * 100)}%)",
                "partial",
            )
        elif dev > t.km_divergence_warn:
            c.add(
                "ecm_gps_divergence",
                f"kms ECM ({_fmt(kms_ecm)}) y GPS ({_fmt(kms_gps)}) divergen {_fmt(dev * 100)}% (> {_fmt(t.km_divergence_warn * 100)}%)",
                "warning",
            )

    # 10. Divergencia ECM vs GPS en horas.
    if (
        hours_ecm is not None
        and hours_gps is not None
        and hours_ecm > t.hours_divergence_min_hours
        and hours_gps > t.hours_divergence_min_hours
    ):
        dev = _rel_dev(hours_ecm, hours_gps)
        if dev > t.hours_divergence_warn:
            c.add(
                "hours_ecm_gps_divergence",
                f"horas ECM ({_fmt(hours_ecm)}) y GPS ({_fmt(hours_gps)}) divergen {_fmt(dev * 100)}% (> {_fmt(t.hours_divergence_warn * 100)}%)",
                "warning",
            )

    # 11. Incoherencia km vs horas.
    if kms_ecm is not None and hours_ecm is not None:
        if kms_ecm == 0 and hours_ecm > t.incoherent_hours_without_km:
            c.add(
                "km_hours_incoherent",
                f"0 km con {_fmt(hours_ecm)} h de motor (> {_fmt(t.incoherent_hours_without_km)} h)",
                "partial",
            )
        elif kms_ecm > t.incoherent_km_without_hours and hours_ecm == 0:
            c.add(
                "km_hours_incoherent",
                f"{_fmt(kms_ecm)} km (> {_fmt(t.incoherent_km_without_hours)} km) con 0 h de motor",
                "partial",
            )

    # 12. Combustible cero con kilometros -> anular combustible. Como la
    # anulacion destruye la evidencia (fuel pasa a None), en una reevaluacion
    # se reconoce la flag previa para mantener la idempotencia.
    previously_zeroed = fuel is None and "fuel_zero_with_km" in (record.validation_flags or [])
    if _positive(kms_ecm) and ((fuel is not None and fuel == 0) or previously_zeroed):
        c.add(
            "fuel_zero_with_km",
            f"combustible 0 gal con {_fmt(kms_ecm)} km recorridos; fuel_gallons anulado",
            "partial",
        )
        fuel = None
        c.set_metric("fuel_gallons", None)

    # 13. Continuidad con el mes anterior.
    if previous is not None:
        if (
            previous.odo_end is not None
            and record.odo_start is not None
            and abs(record.odo_start - previous.odo_end) > t.chain_tolerance_km
        ):
            c.add(
                "chain_broken",
                f"odometro inicial {_fmt(record.odo_start)} no empalma con el final del mes anterior {_fmt(previous.odo_end)} (dif. {_fmt(record.odo_start - previous.odo_end)} km)",
                "warning",
            )
        if (
            previous.horo_end is not None
            and record.horo_start is not None
            and abs(record.horo_start - previous.horo_end) > t.chain_tolerance_hours
        ):
            c.add(
                "chain_broken",
                f"horometro inicial {_fmt(record.horo_start)} no empalma con el final del mes anterior {_fmt(previous.horo_end)} (dif. {_fmt(record.horo_start - previous.horo_end)} h)",
                "warning",
            )

    # 14. Retrocesos Geotab significativos.
    reg_km = record.geotab_regression_total_km or 0
    if _positive(kms_ecm) and reg_km > t.regression_significant_ratio * kms_ecm:
        c.add(
            "regression_significant",
            f"retrocesos de odometro {_fmt(reg_km)} km superan el {_fmt(t.regression_significant_ratio * 100)}% de {_fmt(kms_ecm)} km",
            "partial",
        )
    reg_h = record.geotab_regression_total_hours or 0
    if _positive(hours_ecm) and reg_h > t.regression_significant_ratio * hours_ecm:
        c.add(
            "regression_significant",
            f"retrocesos de horometro {_fmt(reg_h)} h superan el {_fmt(t.regression_significant_ratio * 100)}% de {_fmt(hours_ecm)} h",
            "partial",
        )

    # 15. Mezcla de fuentes en el mismo contador.
    if record.odo_start_source and record.odo_end_source and record.odo_start_source != record.odo_end_source:
        c.add(
            "source_mix",
            f"odometro inicial de '{record.odo_start_source}' y final de '{record.odo_end_source}'",
            "warning",
        )
    if (
        record.horo_start_source
        and record.horo_end_source
        and record.horo_start_source != record.horo_end_source
    ):
        c.add(
            "source_mix",
            f"horometro inicial de '{record.horo_start_source}' y final de '{record.horo_end_source}'",
            "warning",
        )

    # --- Resolver estado final (nunca se sube) ---
    status = record.calculation_status
    if c.action_rank >= ACTION_RANK["error"]:
        status = "error"
    elif c.action_rank >= ACTION_RANK["partial"] and status == "calculated":
        status = "partial"

    updates: dict[str, Any] = dict(c.updates)
    updates["warnings"] = base_warnings + c.warnings
    updates["validation_flags"] = base_flags + c.flags
    updates["calculation_status"] = status
    return record.model_copy(update=updates)


__all__ = [
    "ALL_FLAGS",
    "EVALUABLE_STATUSES",
    "PlausibilityThresholds",
    "WARNING_PREFIX",
    "days_in_month",
    "validate_record",
]
