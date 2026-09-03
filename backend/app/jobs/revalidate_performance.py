"""
Revalidacion de plausibilidad sobre cortes mensuales ya persistidos.

Recorre ``monthly_vehicle_performance`` en un rango de meses, reconstruye el
``MonthlyPerformanceRecord`` de cada fila y le aplica
``performance_validation.validate_record`` (con el mes anterior de la misma
placa/database como ``previous`` y los ``plausibility_overrides`` del cliente).

Por defecto es DRY-RUN: solo reporta cuantas filas cambiarian de estado, la
frecuencia de cada flag y una muestra de filas. Con ``--apply`` persiste los
cambios (estado, warnings, validation_flags y metricas anuladas) en una sola
transaccion.

Uso:
    python -m app.jobs.revalidate_performance [--from YYYY-MM] [--to YYYY-MM]
        [--apply] [--customer-database-id N] [--json]

Nunca toca filas ``unbound``/``no_data``/``error`` (validate_record las deja
intactas) y el proceso siempre termina con exit code 0.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.db import db_conn
from app.schemas.vehicle import MonthlyPerformanceRecord
from app.services.performance_validation import days_in_month, validate_record

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [revalidate-performance] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

COL_TZ_OFFSET = timezone(timedelta(hours=-5))

# Cantidad maxima de filas listadas en la muestra del reporte.
SAMPLE_LIMIT = 30

# Metricas que la capa de plausibilidad puede anular y que se persisten con --apply.
NULLABLE_METRICS: tuple[str, ...] = ("kms_ecm", "hours_ecm", "fuel_gallons")

UPDATE_SQL = """
UPDATE monthly_vehicle_performance
SET calculation_status = %s,
    warnings = %s,
    validation_flags = %s,
    kms_ecm = %s,
    hours_ecm = %s,
    fuel_gallons = %s,
    updated_at = NOW()
WHERE id = %s
"""

SELECT_SQL = """
SELECT
    p.id,
    p.customer_id,
    p.customer_database_id,
    p.plate,
    p.period_month,
    p.source_provider,
    p.provider_vehicle_id,
    p.technical_number,
    p.engine_name,
    p.odo_start,
    p.odo_end,
    p.horo_start,
    p.horo_end,
    p.kms_ecm,
    p.kms_gps,
    p.hours_ecm,
    p.hours_gps,
    p.fuel_gallons,
    p.calculation_status,
    p.warnings,
    p.calculated_at,
    p.is_adhoc,
    p.geotab_regression_count,
    p.geotab_regression_total_km,
    p.geotab_regression_total_hours,
    p.odo_start_source,
    p.odo_end_source,
    p.horo_start_source,
    p.horo_end_source,
    p.fuel_end,
    p.validation_flags,
    p.source_meta,
    p.job_id,
    p.last_error,
    p.is_stale,
    a.vocacional,
    d.provider_config -> 'plausibility_overrides' AS plausibility_overrides
FROM monthly_vehicle_performance p
LEFT JOIN vehicle_motor_assignments a ON a.plate = p.plate
LEFT JOIN customer_databases d ON d.id = p.customer_database_id
WHERE p.period_month >= %s AND p.period_month <= %s
  {database_filter}
ORDER BY p.customer_database_id, p.plate, p.period_month
"""


# --------------------------------------------------------------------------- #
# Utilidades de meses
# --------------------------------------------------------------------------- #
def previous_month(month: str) -> str:
    parsed = datetime.strptime(month.strip(), "%Y-%m")
    first = parsed.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime("%Y-%m")


def _validate_month(value: str) -> str:
    try:
        datetime.strptime(value.strip(), "%Y-%m")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"mes invalido '{value}', se espera YYYY-MM") from exc
    return value.strip()


def _current_month() -> str:
    return datetime.now(COL_TZ_OFFSET).strftime("%Y-%m")


# --------------------------------------------------------------------------- #
# Mapeo fila -> record
# --------------------------------------------------------------------------- #
def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def build_record(row: dict[str, Any]) -> MonthlyPerformanceRecord:
    """Construye el ``MonthlyPerformanceRecord`` a partir de una fila del SELECT."""
    return MonthlyPerformanceRecord(
        customer_id=row.get("customer_id"),
        customer_database_id=row["customer_database_id"],
        source_provider=row.get("source_provider") or "unknown",
        plate=row["plate"],
        provider_vehicle_id=row.get("provider_vehicle_id"),
        technical_number=row.get("technical_number"),
        engine_name=row.get("engine_name"),
        period_month=row["period_month"],
        odo_start=row.get("odo_start"),
        odo_end=row.get("odo_end"),
        horo_start=row.get("horo_start"),
        horo_end=row.get("horo_end"),
        kms_ecm=row.get("kms_ecm"),
        kms_gps=row.get("kms_gps"),
        hours_ecm=row.get("hours_ecm"),
        hours_gps=row.get("hours_gps"),
        fuel_gallons=row.get("fuel_gallons"),
        geotab_regression_count=row.get("geotab_regression_count") or 0,
        geotab_regression_total_km=row.get("geotab_regression_total_km") or 0,
        geotab_regression_total_hours=row.get("geotab_regression_total_hours") or 0,
        vocacional=bool(row.get("vocacional")),
        calculation_status=row["calculation_status"],
        warnings=_as_list(row.get("warnings")),
        calculated_at=row.get("calculated_at"),
        is_adhoc=bool(row.get("is_adhoc")),
        odo_start_source=row.get("odo_start_source"),
        odo_end_source=row.get("odo_end_source"),
        horo_start_source=row.get("horo_start_source"),
        horo_end_source=row.get("horo_end_source"),
        fuel_end=row.get("fuel_end"),
        validation_flags=_as_list(row.get("validation_flags")),
        source_meta=_as_dict(row.get("source_meta")),
        job_id=row.get("job_id"),
        last_error=row.get("last_error"),
        is_stale=bool(row.get("is_stale")),
    )


# --------------------------------------------------------------------------- #
# Evaluacion (pura, sin DB)
# --------------------------------------------------------------------------- #
@dataclass
class RowChange:
    row_id: Any
    period_month: str
    plate: str
    provider: str
    old_status: str
    new_status: str
    flags: list[str]
    new_warnings: list[str]
    kms_ecm: float | None
    hours_ecm: float | None
    fuel_gallons: float | None

    @property
    def status_changed(self) -> bool:
        return self.old_status != self.new_status

    def update_params(self) -> tuple[Any, ...]:
        return (
            self.new_status,
            Jsonb(self.new_warnings),
            Jsonb(self.flags),
            self.kms_ecm,
            self.hours_ecm,
            self.fuel_gallons,
            self.row_id,
        )


@dataclass
class MonthStats:
    evaluated: int = 0
    changed: int = 0
    status_changed: int = 0
    transitions: Counter = field(default_factory=Counter)
    flags: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated": self.evaluated,
            "changed": self.changed,
            "status_changed": self.status_changed,
            "transitions": {k: v for k, v in sorted(self.transitions.items())},
            "flags": {k: v for k, v in self.flags.most_common()},
        }


@dataclass
class Report:
    from_month: str
    to_month: str
    months: dict[str, MonthStats] = field(default_factory=dict)
    total: MonthStats = field(default_factory=MonthStats)
    changes: list[RowChange] = field(default_factory=list)
    applied: int | None = None

    def sample(self, limit: int = SAMPLE_LIMIT) -> list[RowChange]:
        # Prioriza cambios de estado; luego el resto (warnings/flags/metricas).
        ordered = sorted(self.changes, key=lambda c: (not c.status_changed, c.period_month, c.plate))
        return ordered[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_month,
            "to": self.to_month,
            "months": {m: s.to_dict() for m, s in sorted(self.months.items())},
            "total": self.total.to_dict(),
            "applied": self.applied,
            "sample": [
                {
                    "period_month": c.period_month,
                    "plate": c.plate,
                    "provider": c.provider,
                    "old_status": c.old_status,
                    "new_status": c.new_status,
                    "flags": c.flags,
                }
                for c in self.sample()
            ],
        }


def _metrics_differ(before: MonthlyPerformanceRecord, after: MonthlyPerformanceRecord) -> bool:
    return any(getattr(before, name) != getattr(after, name) for name in NULLABLE_METRICS)


def _record_changed(before: MonthlyPerformanceRecord, after: MonthlyPerformanceRecord) -> bool:
    return (
        before.calculation_status != after.calculation_status
        or list(before.warnings or []) != list(after.warnings or [])
        or list(before.validation_flags or []) != list(after.validation_flags or [])
        or _metrics_differ(before, after)
    )


def revalidate_rows(rows: Iterable[dict[str, Any]], *, from_month: str, to_month: str) -> Report:
    """Evalua las filas (ya ordenadas por database, placa, mes) y arma el reporte.

    Las filas con ``period_month < from_month`` solo sirven como ``previous`` de
    la primera fila reportada de cada placa; no se cuentan ni se modifican.
    """
    report = Report(from_month=from_month, to_month=to_month)
    # Mes anterior por (database, placa): se usa el registro ORIGINAL persistido
    # (no el revalidado), porque la continuidad se compara con lo que quedo en DB.
    previous_by_key: dict[tuple[Any, str], MonthlyPerformanceRecord] = {}

    for row in rows:
        record = build_record(row)
        key = (record.customer_database_id, record.plate)
        prev = previous_by_key.get(key)
        if prev is not None and prev.period_month != previous_month(record.period_month):
            prev = None
        previous_by_key[key] = record

        if record.period_month < from_month or record.period_month > to_month:
            continue

        overrides = _as_dict(row.get("plausibility_overrides")) or None
        after = validate_record(
            record,
            days_in_month=days_in_month(record.period_month),
            previous=prev,
            overrides=overrides,
        )

        stats = report.months.setdefault(record.period_month, MonthStats())
        stats.evaluated += 1
        report.total.evaluated += 1
        for flag in after.validation_flags or []:
            stats.flags[flag] += 1
            report.total.flags[flag] += 1

        if not _record_changed(record, after):
            continue

        change = RowChange(
            row_id=row.get("id"),
            period_month=record.period_month,
            plate=record.plate,
            provider=record.source_provider,
            old_status=record.calculation_status,
            new_status=after.calculation_status,
            flags=list(after.validation_flags or []),
            new_warnings=list(after.warnings or []),
            kms_ecm=after.kms_ecm,
            hours_ecm=after.hours_ecm,
            fuel_gallons=after.fuel_gallons,
        )
        report.changes.append(change)
        stats.changed += 1
        report.total.changed += 1
        if change.status_changed:
            transition = f"{change.old_status}->{change.new_status}"
            stats.status_changed += 1
            stats.transitions[transition] += 1
            report.total.status_changed += 1
            report.total.transitions[transition] += 1

    return report


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def load_rows(conn: Any, *, from_month: str, to_month: str, customer_database_id: int | None) -> list[dict[str, Any]]:
    load_from = previous_month(from_month)
    params: list[Any] = [load_from, to_month]
    database_filter = ""
    if customer_database_id is not None:
        database_filter = "AND p.customer_database_id = %s"
        params.append(customer_database_id)
    with conn.cursor() as cur:
        cur.execute(SELECT_SQL.format(database_filter=database_filter), params)
        return list(cur.fetchall())


def apply_changes(cur: Any, changes: Iterable[RowChange]) -> int:
    count = 0
    for change in changes:
        cur.execute(UPDATE_SQL, change.update_params())
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Salida
# --------------------------------------------------------------------------- #
def _fmt_stats(label: str, stats: MonthStats) -> list[str]:
    lines = [
        f"{label:<10} evaluadas={stats.evaluated:<5} cambian={stats.changed:<5} cambian_estado={stats.status_changed}"
    ]
    if stats.transitions:
        trans = ", ".join(f"{k}: {v}" for k, v in sorted(stats.transitions.items()))
        lines.append(f"{'':<10} transiciones: {trans}")
    return lines


def format_report(report: Report, *, apply: bool) -> str:
    lines: list[str] = []
    mode = "APPLY" if apply else "DRY-RUN"
    lines.append(f"Revalidacion de plausibilidad {report.from_month}..{report.to_month} [{mode}]")
    lines.append("")
    lines.append("Por mes:")
    for month in sorted(report.months):
        lines.extend(_fmt_stats(month, report.months[month]))
    lines.append("")
    lines.extend(_fmt_stats("TOTAL", report.total))
    lines.append("")
    lines.append("Frecuencia de flags (total):")
    if report.total.flags:
        width = max(len(f) for f in report.total.flags)
        for flag, count in report.total.flags.most_common():
            lines.append(f"  {flag:<{width}}  {count}")
    else:
        lines.append("  (ninguna)")
    lines.append("")
    sample = report.sample()
    lines.append(f"Muestra de filas que cambian ({len(sample)} de {len(report.changes)}):")
    if sample:
        lines.append(f"  {'mes':<8} {'placa':<10} {'proveedor':<10} {'estado':<22} flags")
        for c in sample:
            status = f"{c.old_status}->{c.new_status}"
            lines.append(f"  {c.period_month:<8} {c.plate:<10} {c.provider:<10} {status:<22} {','.join(c.flags) or '-'}")
    else:
        lines.append("  (ninguna)")
    if report.applied is not None:
        lines.append("")
        lines.append(f"Filas actualizadas: {report.applied}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.revalidate_performance",
        description="Reevalua la plausibilidad de los cortes mensuales persistidos (dry-run por defecto).",
    )
    parser.add_argument("--from", dest="from_month", type=_validate_month, default=None, help="Mes inicial YYYY-MM (default: --to)")
    parser.add_argument("--to", dest="to_month", type=_validate_month, default=None, help="Mes final YYYY-MM (default: mes actual)")
    parser.add_argument("--apply", action="store_true", help="Persiste los cambios (una sola transaccion)")
    parser.add_argument("--customer-database-id", type=int, default=None, help="Limitar a una database de cliente")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Imprime el reporte como JSON")
    return parser


def run(argv: list[str] | None = None) -> Report:
    args = build_parser().parse_args(argv)
    to_month = args.to_month or _current_month()
    from_month = args.from_month or to_month
    if from_month > to_month:
        from_month, to_month = to_month, from_month

    logger.info(
        "Cargando cortes %s..%s (mas %s como mes previo)%s ...",
        from_month,
        to_month,
        previous_month(from_month),
        f" database={args.customer_database_id}" if args.customer_database_id is not None else "",
    )

    with db_conn(row_factory=dict_row) as conn:
        rows = load_rows(
            conn,
            from_month=from_month,
            to_month=to_month,
            customer_database_id=args.customer_database_id,
        )
        logger.info("%d filas cargadas.", len(rows))
        report = revalidate_rows(rows, from_month=from_month, to_month=to_month)

        if args.apply and report.changes:
            with conn.cursor() as cur:
                report.applied = apply_changes(cur, report.changes)
            # db_conn hace commit al salir del bloque sin excepcion.
            logger.info("Se aplicaron %d actualizaciones (commit al cerrar la conexion).", report.applied)
        elif args.apply:
            report.applied = 0

    if args.as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(format_report(report, apply=args.apply))
    return report


def main(argv: list[str] | None = None) -> int:
    run(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
