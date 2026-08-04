from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas.cpk_cph import (
    CpkCphInputRow,
    CpkCphPreviewRequest,
    CpkCphPreviewResponse,
    CpkCphPreviewRow,
    CpkCphReportDetail,
    CpkCphReportRow,
    CpkCphReportSaveRequest,
    CpkCphReportSummary,
    CpkCphRowPatchRequest,
)
from app.schemas.vehicle import CpkCutoffInputRow, CpkCutoffPreviewRequest
from app.services.motor_catalog import _database_dsn
from app.services.rendimientos import lookup_geotab_granular_regressions, preview_cpk_cutoffs


class CpkCphError(Exception):
    pass


class CpkCphNotFound(CpkCphError):
    pass


class CpkCphConflict(CpkCphError):
    pass


_CPK_TABLES_DONE = False
_MANAGED_FLEET_CATEGORY = "Flota Administrada"
_GEOTAB_REVIEW_THRESHOLD_PCT = 5.0
_GEOTAB_REGRESSION_WARNING_PREFIX = "Retroceso Geotab detectado"
_GEOTAB_ACCUMULATED_REGRESSION_MARKER = "[acumulado]"
_GEOTAB_GRANULAR_CHECK_PREFIX = "Verificación granular Geotab"
_GEOTAB_OVERRIDE_MARKER = "Retroceso Geotab aceptado manualmente"
_ODOMETER_LABEL = "odómetro"
_HOURMETER_LABEL = "horómetro"


def _ensure_cpk_tables(conn: psycopg.Connection) -> None:
    global _CPK_TABLES_DONE
    if _CPK_TABLES_DONE:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cpk_cph_reports (
                id BIGSERIAL PRIMARY KEY,
                customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                period_month TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'saved',
                current_version INTEGER NOT NULL DEFAULT 0,
                created_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                updated_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                approved_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
                approved_at TIMESTAMPTZ NULL,
                reopened_from_version INTEGER NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (customer_id, period_month)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cpk_cph_report_rows (
                id BIGSERIAL PRIMARY KEY,
                report_id BIGINT NOT NULL REFERENCES cpk_cph_reports(id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL DEFAULT 0,
                plate VARCHAR(32) NOT NULL,
                cutoff_start_at TEXT NOT NULL,
                cutoff_end_at TEXT NOT NULL,
                cutoff_start_utc TEXT NULL,
                cutoff_end_utc TEXT NULL,
                client_name TEXT NULL,
                database_name TEXT NULL,
                source_provider TEXT NULL,
                provider_vehicle_id TEXT NULL,
                vocacional BOOLEAN NOT NULL DEFAULT FALSE,
                km_client DOUBLE PRECISION NULL,
                odo_start DOUBLE PRECISION NULL,
                odo_end DOUBLE PRECISION NULL,
                horo_start DOUBLE PRECISION NULL,
                horo_end DOUBLE PRECISION NULL,
                kms_ecm_geotab DOUBLE PRECISION NULL,
                kms_gps DOUBLE PRECISION NULL,
                hours_ecm DOUBLE PRECISION NULL,
                hours_gps DOUBLE PRECISION NULL,
                fuel_gallons DOUBLE PRECISION NULL,
                geotab_regression_count INTEGER NOT NULL DEFAULT 0,
                geotab_regression_total_km DOUBLE PRECISION NOT NULL DEFAULT 0,
                geotab_regression_total_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
                suggested_adjustment DOUBLE PRECISION NOT NULL DEFAULT 0,
                km_adjustment DOUBLE PRECISION NULL DEFAULT 0,
                hour_adjustment DOUBLE PRECISION NULL DEFAULT 0,
                kms_ecm_approved DOUBLE PRECISION NULL,
                hours_ecm_approved DOUBLE PRECISION NULL,
                km_difference DOUBLE PRECISION NULL,
                km_difference_pct DOUBLE PRECISION NULL,
                hour_difference DOUBLE PRECISION NULL,
                hour_difference_pct DOUBLE PRECISION NULL,
                calculation_status TEXT NOT NULL DEFAULT 'pending',
                warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
                correction_note TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS vocacional BOOLEAN NOT NULL DEFAULT FALSE;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS geotab_regression_count INTEGER NOT NULL DEFAULT 0;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS geotab_regression_total_km DOUBLE PRECISION NOT NULL DEFAULT 0;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS geotab_regression_total_hours DOUBLE PRECISION NOT NULL DEFAULT 0;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS suggested_adjustment DOUBLE PRECISION NOT NULL DEFAULT 0;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS km_adjustment DOUBLE PRECISION NULL DEFAULT 0;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS hour_adjustment DOUBLE PRECISION NULL DEFAULT 0;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS kms_ecm_approved DOUBLE PRECISION NULL;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS hours_ecm_approved DOUBLE PRECISION NULL;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS hour_difference DOUBLE PRECISION NULL;")
        cur.execute("ALTER TABLE cpk_cph_report_rows ADD COLUMN IF NOT EXISTS hour_difference_pct DOUBLE PRECISION NULL;")
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cpk_cph_report_rows_report
            ON cpk_cph_report_rows (report_id);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cpk_cph_reports_month_customer
            ON cpk_cph_reports (period_month, customer_id);
            """
        )
    conn.commit()
    _CPK_TABLES_DONE = True


def _normalize_plate(value: str | None) -> str:
    return "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum())


def _customer_name(conn: psycopg.Connection, customer_id: int) -> str:
    """Resuelve el nombre del cliente y exige categoría Flota Administrada:
    CPK/CPH solo aplica a esas flotas."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, category FROM customers WHERE id = %s", (customer_id,))
        row = cur.fetchone()
    if not row:
        raise CpkCphNotFound("Cliente no encontrado.")
    if str(row.get("category") or "") != _MANAGED_FLEET_CATEGORY:
        raise CpkCphConflict(
            "CPK/CPH solo aplica a clientes de categoría Flota Administrada."
        )
    return str(row["name"])


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _diff(approved: float | None, reference: float | None) -> tuple[float | None, float | None]:
    if approved is None or reference is None:
        return None, None
    diff = approved - reference
    pct = (diff / reference * 100.0) if reference else None
    return diff, pct


def _km_reference(km_client: float | None, kms_gps: float | None) -> float | None:
    return km_client if km_client is not None else kms_gps


def _is_geotab_row(row: dict[str, Any]) -> bool:
    """Las reglas de consistencia de acumulados aplican exclusivamente a Geotab."""
    return str(row.get("source_provider") or "").strip().lower() == "geotab"


def _regression_metric(row: dict[str, Any]) -> tuple[str, str, str]:
    """Medidor, etiqueta y unidad que gobiernan el ajuste de la fila."""
    if bool(row.get("vocacional")):
        return _HOURMETER_LABEL, "horas", "h"
    return _ODOMETER_LABEL, "kilómetros", "km"


def _split_sequence_regressions(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Separa los retrocesos [acumulado] del medidor que rige el ajuste de los
    del otro medidor: un retroceso de horómetro no puede bloquear un ajuste de
    km (ni al revés), porque el ajuste sugerido nunca lo cubriría."""
    own_label, _, _ = _regression_metric(row)
    other_label = _ODOMETER_LABEL if own_label == _HOURMETER_LABEL else _HOURMETER_LABEL
    own_total = (
        _num(row.get("geotab_regression_total_hours"))
        if own_label == _HOURMETER_LABEL
        else _num(row.get("geotab_regression_total_km"))
    ) or 0.0
    own: list[str] = []
    other: list[str] = []
    for warning in row.get("warnings") or []:
        text = str(warning)
        if not (
            text.startswith(_GEOTAB_REGRESSION_WARNING_PREFIX)
            and _GEOTAB_ACCUMULATED_REGRESSION_MARKER in text
        ):
            continue
        if f"({own_label})" in text:
            own.append(text)
        elif f"({other_label})" in text:
            other.append(text)
        else:
            # Resumen sin medidor ("N retroceso(s) adicional(es)"): solo cuenta
            # si el medidor de la fila acumuló retrocesos.
            (own if own_total > 0 else other).append(text)
    return own, other


def _regression_override_requested(row: dict[str, Any]) -> bool:
    """El usuario aceptó explícitamente la inconsistencia (flag del request) o
    ya la aceptó en un guardado previo (marcador persistido en warnings)."""
    if bool(row.get("regression_override")):
        return True
    return any(
        str(warning).startswith(_GEOTAB_OVERRIDE_MARKER) for warning in row.get("warnings") or []
    )


def _geotab_adjustment_validation(row: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """Devuelve si una fila Geotab debe bloquearse, sus advertencias y los motivos.

    La revisión se activa con la misma diferencia que se muestra en CPK/CPH:
    kilómetros para vehículos comerciales y horas para los vocacionales. Solo en
    ese caso se inspeccionan los acumulados crudos; un odómetro u horómetro no
    puede terminar por debajo de su lectura inicial. Únicamente el medidor que
    rige el ajuste bloquea; el otro queda como advertencia, y el usuario puede
    forzar el guardado con nota (`regression_override`).
    """
    if not _is_geotab_row(row):
        return False, [], []

    relevant_difference_pct = (
        _num(row.get("hour_difference_pct"))
        if bool(row.get("vocacional"))
        else _num(row.get("km_difference_pct"))
    )
    if relevant_difference_pct is None or abs(relevant_difference_pct) <= _GEOTAB_REVIEW_THRESHOLD_PCT:
        return False, [], []

    own_label, metric_name, unit = _regression_metric(row)
    warnings = [
        "Revisión Geotab: la diferencia relevante supera 5%; se validaron los acumulados de km y horas."
    ]
    endpoint_regressions: list[str] = []
    other_regressions: list[str] = []
    odo_start, odo_end = _num(row.get("odo_start")), _num(row.get("odo_end"))
    if odo_start is not None and odo_end is not None and odo_end < odo_start:
        message = f"odómetro retrocede de {odo_start:g} a {odo_end:g} km"
        (endpoint_regressions if own_label == _ODOMETER_LABEL else other_regressions).append(message)

    horo_start, horo_end = _num(row.get("horo_start")), _num(row.get("horo_end"))
    if horo_start is not None and horo_end is not None and horo_end < horo_start:
        message = f"horómetro retrocede de {horo_start:g} a {horo_end:g} h"
        (endpoint_regressions if own_label == _HOURMETER_LABEL else other_regressions).append(message)

    sequence_regressions, other_sequence = _split_sequence_regressions(row)
    if other_regressions or other_sequence:
        warnings.append(
            f"Aviso Geotab: hay retrocesos en el otro medidor "
            f"({len(other_regressions) + len(other_sequence)}); no afectan el ajuste de "
            f"{metric_name} de esta fila."
        )
    suggested_adjustment = max(0.0, _num(row.get("suggested_adjustment")) or 0.0)
    applied_adjustment = abs(
        (_num(row.get("hour_adjustment")) or 0.0)
        if bool(row.get("vocacional"))
        else (_num(row.get("km_adjustment")) or 0.0)
    )
    sequence_adjustment_missing = bool(sequence_regressions) and (
        suggested_adjustment <= 0 or applied_adjustment + 0.0001 < suggested_adjustment
    )
    adjustment_covers_sequence = bool(sequence_regressions) and not sequence_adjustment_missing
    regressions = [] if adjustment_covers_sequence else list(endpoint_regressions)
    if sequence_adjustment_missing:
        regressions.append(
            f"{len(sequence_regressions)} retroceso(s) de {own_label} sin cubrir: "
            f"sugerido {suggested_adjustment:g} {unit}, aplicado {applied_adjustment:g} {unit}"
        )

    if not regressions:
        return False, warnings, []

    if _regression_override_requested(row):
        note = str(row.get("correction_note") or "").strip()
        if note:
            warnings.append(
                f"{_GEOTAB_OVERRIDE_MARKER}: "
                + "; ".join(regressions)
                + f". Nota: {note}"
            )
            return False, warnings, []
        regressions.append("forzar el guardado exige nota de corrección")

    warnings.append(
        "Inconsistencia Geotab: "
        + "; ".join(regressions)
        + ". No se permite guardar el ajuste."
    )
    return True, warnings, regressions


def _append_geotab_validation_warnings(row: dict[str, Any]) -> dict[str, Any]:
    """Añade advertencias deterministas sin duplicarlas en guardados sucesivos."""
    _, validation_warnings, _ = _geotab_adjustment_validation(row)
    warnings = list(row.get("warnings") or [])
    for warning in validation_warnings:
        if warning not in warnings:
            warnings.append(warning)
    row["warnings"] = warnings
    return row


def _has_regression_metrics(row: dict[str, Any]) -> bool:
    return bool(
        int(_num(row.get("geotab_regression_count")) or 0)
        or (_num(row.get("geotab_regression_total_km")) or 0.0) > 0
        or (_num(row.get("geotab_regression_total_hours")) or 0.0) > 0
    )


def _needs_granular_lookup(row: dict[str, Any]) -> bool:
    """Una fila Geotab con diferencia relevante >5% y sin métricas de retroceso
    exige consultar los registros granulares: los rendimientos mensuales
    calculados antes de la métrica traen ceros que no distinguen "sin
    retrocesos" de "nunca analizado"."""
    if not _is_geotab_row(row):
        return False
    relevant_difference_pct = (
        _num(row.get("hour_difference_pct"))
        if bool(row.get("vocacional"))
        else _num(row.get("km_difference_pct"))
    )
    if relevant_difference_pct is None or abs(relevant_difference_pct) <= _GEOTAB_REVIEW_THRESHOLD_PCT:
        return False
    if _has_regression_metrics(row):
        return False
    warnings = [str(warning) for warning in row.get("warnings") or []]
    if any(
        warning.startswith(_GEOTAB_REGRESSION_WARNING_PREFIX)
        and _GEOTAB_ACCUMULATED_REGRESSION_MARKER in warning
        for warning in warnings
    ):
        return False
    if any(warning.startswith(_GEOTAB_GRANULAR_CHECK_PREFIX) for warning in warnings):
        return False
    return True


def _enrich_geotab_granular(
    row: dict[str, Any],
    *,
    month: str,
    api_cache: dict[Any, Any] | None = None,
) -> dict[str, Any]:
    """Busca retrocesos en el registro granular de Geotab y los plasma en la fila.

    Actualiza métricas, ajuste sugerido y advertencias; si la fila no tenía
    ajuste aplicado, adopta el sugerido y recalcula aprobados y diferencias.
    Un fallo de consulta no bloquea: deja advertencia y conserva la fila.
    """
    if not _needs_granular_lookup(row):
        return row
    warnings = list(row.get("warnings") or [])
    try:
        result = lookup_geotab_granular_regressions(
            plate=str(row.get("plate") or ""),
            month=month,
            cutoff_start_utc=row.get("cutoff_start_utc"),
            cutoff_end_utc=row.get("cutoff_end_utc"),
            provider_vehicle_id=row.get("provider_vehicle_id"),
            odo_start=_num(row.get("odo_start")),
            horo_start=_num(row.get("horo_start")),
            api_cache=api_cache,
        )
    except Exception as exc:
        message = f"No fue posible verificar retrocesos granulares en Geotab: {exc}"
        if message not in warnings:
            warnings.append(message)
        row["warnings"] = warnings
        return row

    vocacional = bool(row.get("vocacional"))
    regression_count = result.hourmeter_count if vocacional else result.odometer_count
    suggested_adjustment = max(
        0.0,
        result.hourmeter_total_hours if vocacional else result.odometer_total_km,
    )
    row["geotab_regression_count"] = regression_count
    row["geotab_regression_total_km"] = max(0.0, result.odometer_total_km)
    row["geotab_regression_total_hours"] = max(0.0, result.hourmeter_total_hours)
    row["suggested_adjustment"] = suggested_adjustment
    for warning in result.warnings:
        if warning not in warnings:
            warnings.append(warning)
    summary = (
        f"{_GEOTAB_GRANULAR_CHECK_PREFIX}: {regression_count} retroceso(s), "
        f"total {suggested_adjustment:g} {'h' if vocacional else 'km'}."
        if suggested_adjustment > 0
        else f"{_GEOTAB_GRANULAR_CHECK_PREFIX}: sin retrocesos en el periodo."
    )
    if summary not in warnings:
        warnings.append(summary)
    row["warnings"] = warnings

    if suggested_adjustment > 0:
        if vocacional and not (_num(row.get("hour_adjustment")) or 0.0):
            row["hour_adjustment"] = suggested_adjustment
            hours_raw = _num(row.get("hours_ecm"))
            if hours_raw is not None:
                row["hours_ecm_approved"] = hours_raw + suggested_adjustment
        elif not vocacional and not (_num(row.get("km_adjustment")) or 0.0):
            row["km_adjustment"] = suggested_adjustment
            kms_raw = _num(row.get("kms_ecm_geotab"))
            if kms_raw is not None:
                row["kms_ecm_approved"] = kms_raw + suggested_adjustment
        if not str(row.get("correction_note") or "").strip():
            row["correction_note"] = (
                f"Ajuste sugerido automáticamente por {regression_count} "
                "retroceso(s) Geotab detectado(s)."
            )
    km_diff, km_pct = _diff(
        _num(row.get("kms_ecm_approved")),
        _km_reference(_num(row.get("km_client")), _num(row.get("kms_gps"))),
    )
    hour_diff, hour_pct = _diff(_num(row.get("hours_ecm_approved")), _num(row.get("hours_gps")))
    row["km_difference"] = km_diff
    row["km_difference_pct"] = km_pct
    row["hour_difference"] = hour_diff
    row["hour_difference_pct"] = hour_pct
    return row


def _regression_conflict_message(details: list[str]) -> str:
    return (
        "No se puede guardar el ajuste: Geotab reporta un retroceso en kilómetros u horas. "
        "Detalle: "
        + " | ".join(details)
        + ". Verifique las lecturas inicial y final, o acepte la inconsistencia con "
        "'Guardar de todas formas' explicando el motivo en la nota."
    )


def _regression_block_detail(row: dict[str, Any]) -> str | None:
    """Motivo de bloqueo de una fila, con placa, o None si no bloquea."""
    blocked, _, reasons = _geotab_adjustment_validation(row)
    if not blocked:
        return None
    plate = str(row.get("plate") or "sin placa")
    return f"{plate}: " + "; ".join(reasons)


def _reject_geotab_regression(row: dict[str, Any]) -> None:
    detail = _regression_block_detail(row)
    if detail:
        raise CpkCphConflict(_regression_conflict_message([detail]))


def _row_from_preview(row: CpkCphPreviewRow | dict[str, Any]) -> dict[str, Any]:
    data = row.model_dump() if hasattr(row, "model_dump") else dict(row)
    vocacional = bool(data.get("vocacional"))
    regression_count = int(_num(data.get("geotab_regression_count")) or 0)
    regression_total_km = max(0.0, _num(data.get("geotab_regression_total_km")) or 0.0)
    regression_total_hours = max(0.0, _num(data.get("geotab_regression_total_hours")) or 0.0)
    suggested_adjustment = _num(data.get("suggested_adjustment"))
    if suggested_adjustment is None:
        suggested_adjustment = regression_total_hours if vocacional else regression_total_km
    km_adjustment = _num(data.get("km_adjustment")) or 0.0
    hour_adjustment = _num(data.get("hour_adjustment")) or 0.0
    kms_approved = _num(data.get("kms_ecm_approved"))
    if kms_approved is None:
        kms_geotab = _num(data.get("kms_ecm_geotab"))
        kms_approved = kms_geotab + km_adjustment if kms_geotab is not None else None
    hours_ecm = _num(data.get("hours_ecm"))
    hours_approved = _num(data.get("hours_ecm_approved"))
    if hours_approved is None:
        hours_approved = hours_ecm + hour_adjustment if hours_ecm is not None else None
    km_client = _num(data.get("km_client"))
    kms_gps = _num(data.get("kms_gps"))
    hours_gps = _num(data.get("hours_gps"))
    km_diff, km_pct = _diff(kms_approved, _km_reference(km_client, kms_gps))
    hour_diff, hour_pct = _diff(hours_approved, hours_gps)
    normalized_row = {
        "plate": _normalize_plate(data.get("plate")),
        "cutoff_start_at": str(data.get("cutoff_start_at") or ""),
        "cutoff_end_at": str(data.get("cutoff_end_at") or ""),
        "cutoff_start_utc": data.get("cutoff_start_utc"),
        "cutoff_end_utc": data.get("cutoff_end_utc"),
        "client_name": data.get("client_name"),
        "database_name": data.get("database_name"),
        "source_provider": data.get("source_provider"),
        "provider_vehicle_id": data.get("provider_vehicle_id"),
        "vocacional": vocacional,
        "km_client": km_client,
        "odo_start": _num(data.get("odo_start")),
        "odo_end": _num(data.get("odo_end")),
        "horo_start": _num(data.get("horo_start")),
        "horo_end": _num(data.get("horo_end")),
        "kms_ecm_geotab": _num(data.get("kms_ecm_geotab")),
        "kms_gps": kms_gps,
        "hours_ecm": hours_ecm,
        "hours_gps": hours_gps,
        "fuel_gallons": _num(data.get("fuel_gallons")),
        "geotab_regression_count": regression_count,
        "geotab_regression_total_km": regression_total_km,
        "geotab_regression_total_hours": regression_total_hours,
        "suggested_adjustment": max(0.0, suggested_adjustment),
        "km_adjustment": km_adjustment,
        "hour_adjustment": hour_adjustment,
        "kms_ecm_approved": kms_approved,
        "hours_ecm_approved": hours_approved,
        "km_difference": km_diff,
        "km_difference_pct": km_pct,
        "hour_difference": hour_diff,
        "hour_difference_pct": hour_pct,
        "calculation_status": str(data.get("calculation_status") or "pending"),
        "warnings": list(data.get("warnings") or []),
        "correction_note": (str(data.get("correction_note")).strip() if data.get("correction_note") else None),
        # No es columna: viaja en el request y queda registrado como marcador en warnings.
        "regression_override": bool(data.get("regression_override")),
    }
    return _append_geotab_validation_warnings(normalized_row)


_ROW_COLUMNS = (
    "report_id, version_number, plate, cutoff_start_at, cutoff_end_at, "
    "cutoff_start_utc, cutoff_end_utc, client_name, database_name, "
    "source_provider, provider_vehicle_id, vocacional, km_client, odo_start, odo_end, "
    "horo_start, horo_end, kms_ecm_geotab, kms_gps, hours_ecm, hours_gps, "
    "fuel_gallons, geotab_regression_count, geotab_regression_total_km, "
    "geotab_regression_total_hours, suggested_adjustment, km_adjustment, "
    "hour_adjustment, kms_ecm_approved, "
    "hours_ecm_approved, km_difference, km_difference_pct, hour_difference, "
    "hour_difference_pct, calculation_status, warnings, correction_note"
)


def _insert_rows(
    conn: psycopg.Connection,
    *,
    report_id: int,
    rows: list[CpkCphPreviewRow | dict[str, Any]],
) -> None:
    with conn.cursor() as cur:
        for raw_row in rows:
            row = _row_from_preview(raw_row)
            cur.execute(
                f"""
                INSERT INTO cpk_cph_report_rows ({_ROW_COLUMNS})
                VALUES (
                    %(report_id)s, 0, %(plate)s, %(cutoff_start_at)s, %(cutoff_end_at)s,
                    %(cutoff_start_utc)s, %(cutoff_end_utc)s, %(client_name)s, %(database_name)s,
                    %(source_provider)s, %(provider_vehicle_id)s, %(vocacional)s, %(km_client)s, %(odo_start)s, %(odo_end)s,
                    %(horo_start)s, %(horo_end)s, %(kms_ecm_geotab)s, %(kms_gps)s, %(hours_ecm)s, %(hours_gps)s,
                    %(fuel_gallons)s, %(geotab_regression_count)s, %(geotab_regression_total_km)s,
                    %(geotab_regression_total_hours)s, %(suggested_adjustment)s,
                    %(km_adjustment)s, %(hour_adjustment)s, %(kms_ecm_approved)s,
                    %(hours_ecm_approved)s, %(km_difference)s, %(km_difference_pct)s, %(hour_difference)s,
                    %(hour_difference_pct)s, %(calculation_status)s, %(warnings)s::jsonb, %(correction_note)s
                );
                """,
                {
                    **row,
                    "report_id": report_id,
                    "warnings": Jsonb(row["warnings"]),
                },
            )


def _summary_from_row(row: dict[str, Any]) -> CpkCphReportSummary:
    return CpkCphReportSummary(
        id=int(row["id"]),
        customer_id=int(row["customer_id"]),
        customer_name=str(row.get("customer_name") or ""),
        period_month=str(row["period_month"]),
        status=str(row["status"]),
        row_count=int(row.get("row_count") or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_record(row: dict[str, Any]) -> CpkCphReportRow:
    return CpkCphReportRow(
        id=int(row["id"]),
        report_id=int(row["report_id"]),
        plate=str(row["plate"]),
        cutoff_start_at=str(row["cutoff_start_at"]),
        cutoff_end_at=str(row["cutoff_end_at"]),
        cutoff_start_utc=row.get("cutoff_start_utc"),
        cutoff_end_utc=row.get("cutoff_end_utc"),
        client_name=row.get("client_name"),
        database_name=row.get("database_name"),
        source_provider=row.get("source_provider"),
        provider_vehicle_id=row.get("provider_vehicle_id"),
        vocacional=bool(row.get("vocacional")),
        km_client=row.get("km_client"),
        odo_start=row.get("odo_start"),
        odo_end=row.get("odo_end"),
        horo_start=row.get("horo_start"),
        horo_end=row.get("horo_end"),
        kms_ecm_geotab=row.get("kms_ecm_geotab"),
        kms_gps=row.get("kms_gps"),
        hours_ecm=row.get("hours_ecm"),
        hours_gps=row.get("hours_gps"),
        fuel_gallons=row.get("fuel_gallons"),
        geotab_regression_count=int(row.get("geotab_regression_count") or 0),
        geotab_regression_total_km=row.get("geotab_regression_total_km") or 0,
        geotab_regression_total_hours=row.get("geotab_regression_total_hours") or 0,
        suggested_adjustment=row.get("suggested_adjustment") or 0,
        km_adjustment=row.get("km_adjustment") or 0,
        hour_adjustment=row.get("hour_adjustment") or 0,
        kms_ecm_approved=row.get("kms_ecm_approved"),
        hours_ecm_approved=row.get("hours_ecm_approved"),
        km_difference=row.get("km_difference"),
        km_difference_pct=row.get("km_difference_pct"),
        hour_difference=row.get("hour_difference"),
        hour_difference_pct=row.get("hour_difference_pct"),
        calculation_status=str(row.get("calculation_status") or "pending"),
        warnings=list(row.get("warnings") or []),
        correction_note=row.get("correction_note"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _get_report_summary(conn: psycopg.Connection, report_id: int) -> CpkCphReportSummary:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                r.*,
                c.name AS customer_name,
                (
                    SELECT COUNT(*)
                    FROM cpk_cph_report_rows rr
                    WHERE rr.report_id = r.id
                ) AS row_count
            FROM cpk_cph_reports r
            INNER JOIN customers c ON c.id = r.customer_id
            WHERE r.id = %s;
            """,
            (report_id,),
        )
        row = cur.fetchone()
    if not row:
        raise CpkCphNotFound("Reporte CPK/CPH no encontrado.")
    return _summary_from_row(row)


def list_reports(*, month: str | None = None, customer_id: int | None = None) -> list[CpkCphReportSummary]:
    params: list[Any] = []
    where: list[str] = ["c.name <> '__navitrans_system__'"]
    if month:
        where.append("r.period_month = %s")
        params.append(month)
    if customer_id:
        where.append("r.customer_id = %s")
        params.append(customer_id)
    where_sql = f"WHERE {' AND '.join(where)}"
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    r.*,
                    c.name AS customer_name,
                    (
                        SELECT COUNT(*)
                        FROM cpk_cph_report_rows rr
                        WHERE rr.report_id = r.id
                    ) AS row_count
                FROM cpk_cph_reports r
                INNER JOIN customers c ON c.id = r.customer_id
                {where_sql}
                ORDER BY r.period_month DESC, c.name ASC;
                """,
                params,
            )
            return [_summary_from_row(row) for row in cur.fetchall()]


def preview_report(payload: CpkCphPreviewRequest) -> CpkCphPreviewResponse:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        customer_name = _customer_name(conn, payload.customer_id)

    seen: set[str] = set()
    duplicate_rows: dict[int, CpkCphPreviewRow] = {}
    valid_rows: list[tuple[int, CpkCphInputRow]] = []
    for index, row in enumerate(payload.rows, start=1):
        plate = _normalize_plate(row.plate)
        if plate in seen:
            duplicate_rows[index] = CpkCphPreviewRow(
                row_number=index,
                plate=plate,
                cutoff_start_at=row.cutoff_start_at,
                cutoff_end_at=row.cutoff_end_at,
                km_client=row.km_client,
                calculation_status="duplicate",
                warnings=["Placa duplicada en el pegado."],
            )
            continue
        seen.add(plate)
        valid_rows.append((index, row))

    cutoff_payload = CpkCutoffPreviewRequest(
        month=payload.month,
        client_names=[customer_name],
        rows=[
            CpkCutoffInputRow(
                plate=row.plate,
                cutoff_start_at=row.cutoff_start_at,
                cutoff_end_at=row.cutoff_end_at,
            )
            for _, row in valid_rows
        ],
    )
    cutoff_response = preview_cpk_cutoffs(cutoff_payload)
    rows_by_order = list(cutoff_response.rows)
    out: list[CpkCphPreviewRow] = []
    valid_idx = 0
    for index, input_row in enumerate(payload.rows, start=1):
        if index in duplicate_rows:
            out.append(duplicate_rows[index])
            continue
        base = rows_by_order[valid_idx]
        valid_idx += 1
        km_client = input_row.km_client
        kms_geotab = base.kms_ecm
        vocacional = bool(base.vocacional)
        suggested_adjustment = (
            base.geotab_regression_total_hours
            if vocacional
            else base.geotab_regression_total_km
        )
        km_adjustment = 0.0 if vocacional else suggested_adjustment
        hour_adjustment = suggested_adjustment if vocacional else 0.0
        kms_approved = kms_geotab + km_adjustment if kms_geotab is not None else None
        hours_approved = base.hours_ecm + hour_adjustment if base.hours_ecm is not None else None
        km_diff, km_pct = _diff(kms_approved, _km_reference(km_client, base.kms_gps))
        hour_diff, hour_pct = _diff(hours_approved, base.hours_gps)
        correction_note = (
            f"Ajuste sugerido automáticamente por {base.geotab_regression_count} "
            "retroceso(s) Geotab detectado(s)."
            if suggested_adjustment > 0
            else None
        )
        preview_row = CpkCphPreviewRow(
            row_number=index,
            plate=base.plate,
            cutoff_start_at=base.cutoff_start_at,
            cutoff_end_at=base.cutoff_end_at,
            cutoff_start_utc=base.cutoff_start_utc,
            cutoff_end_utc=base.cutoff_end_utc,
            client_name=base.client_name,
            database_name=base.database_name,
            source_provider=base.source_provider,
            provider_vehicle_id=base.provider_vehicle_id,
            vocacional=vocacional,
            km_client=km_client,
            odo_start=base.odo_start,
            odo_end=base.odo_end,
            horo_start=base.horo_start,
            horo_end=base.horo_end,
            kms_ecm_geotab=kms_geotab,
            kms_gps=base.kms_gps,
            hours_ecm=base.hours_ecm,
            hours_gps=base.hours_gps,
            fuel_gallons=base.fuel_gallons,
            geotab_regression_count=base.geotab_regression_count,
            geotab_regression_total_km=base.geotab_regression_total_km,
            geotab_regression_total_hours=base.geotab_regression_total_hours,
            suggested_adjustment=suggested_adjustment,
            km_adjustment=km_adjustment,
            hour_adjustment=hour_adjustment,
            kms_ecm_approved=kms_approved,
            hours_ecm_approved=hours_approved,
            km_difference=km_diff,
            km_difference_pct=km_pct,
            hour_difference=hour_diff,
            hour_difference_pct=hour_pct,
            calculation_status=base.status,
            warnings=list(base.warnings or []),
            correction_note=correction_note,
        )
        validated_row = _row_from_preview(preview_row)
        out.append(CpkCphPreviewRow(row_number=index, **validated_row))
    return CpkCphPreviewResponse(month=payload.month, customer_id=payload.customer_id, rows=out)


def save_report(payload: CpkCphReportSaveRequest, *, user_id: int | None) -> CpkCphReportDetail:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        _customer_name(conn, payload.customer_id)
        api_cache: dict[Any, Any] = {}
        enriched_rows: list[dict[str, Any]] = []
        missing_notes: list[str] = []
        blocked_rows: list[str] = []
        for raw_row in payload.rows:
            row = _row_from_preview(raw_row)
            row = _enrich_geotab_granular(row, month=payload.month, api_cache=api_cache)
            has_adjustment = bool(row.get("km_adjustment") or row.get("hour_adjustment"))
            if has_adjustment and not row.get("correction_note"):
                missing_notes.append(str(row.get("plate") or "sin placa"))
            detail = _regression_block_detail(row)
            if detail:
                blocked_rows.append(detail)
            enriched_rows.append(row)
        # Se revisan todas las filas antes de fallar: el usuario ve de una vez
        # cada placa problemática en lugar de corregir una por intento.
        if missing_notes:
            raise CpkCphConflict(
                "Cada ajuste de CPK/CPH requiere nota. Placas sin nota: "
                + ", ".join(missing_notes)
                + "."
            )
        if blocked_rows:
            raise CpkCphConflict(_regression_conflict_message(blocked_rows))
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cpk_cph_reports (customer_id, period_month, status, created_by, updated_by)
                VALUES (%s, %s, 'saved', %s, %s)
                ON CONFLICT (customer_id, period_month)
                DO UPDATE SET
                    status = 'saved',
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                RETURNING id;
                """,
                (payload.customer_id, payload.month, user_id, user_id),
            )
            report = cur.fetchone()
            report_id = int(report["id"])
            cur.execute(
                "DELETE FROM cpk_cph_report_rows WHERE report_id = %s;",
                (report_id,),
            )
            _insert_rows(conn, report_id=report_id, rows=enriched_rows)
        conn.commit()
        return get_report(report_id, conn=conn)


def get_report(report_id: int, *, conn: psycopg.Connection | None = None) -> CpkCphReportDetail:
    own_conn = conn is None
    active_conn = conn or psycopg.connect(_database_dsn(), row_factory=dict_row)
    try:
        _ensure_cpk_tables(active_conn)
        summary = _get_report_summary(active_conn, report_id)
        with active_conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM cpk_cph_report_rows
                WHERE report_id = %s
                ORDER BY plate ASC, id ASC;
                """,
                (report_id,),
            )
            rows = [_row_record(row) for row in cur.fetchall()]
        return CpkCphReportDetail(**summary.model_dump(), rows=rows)
    finally:
        if own_conn:
            active_conn.close()


def delete_report(report_id: int) -> None:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cpk_cph_reports WHERE id = %s RETURNING id;", (report_id,))
            if cur.fetchone() is None:
                raise CpkCphNotFound("Reporte CPK/CPH no encontrado.")
        conn.commit()


def update_row(report_id: int, row_id: int, payload: CpkCphRowPatchRequest, *, user_id: int | None) -> CpkCphReportDetail:
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_cpk_tables(conn)
        summary = _get_report_summary(conn, report_id)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM cpk_cph_report_rows WHERE id = %s AND report_id = %s;",
                (row_id, report_id),
            )
            row = cur.fetchone()
        if not row:
            raise CpkCphNotFound("Fila no encontrada en el reporte.")

        next_data = _row_record(row).model_dump()
        provided_fields = getattr(payload, "model_fields_set", set())
        recalc = False
        if payload.cutoff_start_at is not None:
            next_data["cutoff_start_at"] = payload.cutoff_start_at
            recalc = True
        if payload.cutoff_end_at is not None:
            next_data["cutoff_end_at"] = payload.cutoff_end_at
            recalc = True
        if "km_client" in provided_fields:
            next_data["km_client"] = payload.km_client
        if "km_adjustment" in provided_fields:
            next_data["km_adjustment"] = payload.km_adjustment
        if "hour_adjustment" in provided_fields:
            next_data["hour_adjustment"] = payload.hour_adjustment
        if "kms_ecm_approved" in provided_fields:
            next_data["kms_ecm_approved"] = payload.kms_ecm_approved
        if "hours_ecm_approved" in provided_fields:
            next_data["hours_ecm_approved"] = payload.hours_ecm_approved
        if payload.correction_note is not None:
            next_data["correction_note"] = payload.correction_note.strip() or None
        if payload.regression_override:
            next_data["regression_override"] = True

        if recalc:
            preview = preview_report(
                CpkCphPreviewRequest(
                    month=summary.period_month,
                    customer_id=summary.customer_id,
                    rows=[
                        CpkCphInputRow(
                            plate=next_data["plate"],
                            cutoff_start_at=next_data["cutoff_start_at"],
                            cutoff_end_at=next_data["cutoff_end_at"],
                            km_client=next_data.get("km_client"),
                        )
                    ],
                )
            )
            recalculated = preview.rows[0].model_dump()
            recalculated["id"] = row_id
            recalculated["correction_note"] = next_data.get("correction_note")
            recalculated["km_adjustment"] = next_data.get("km_adjustment") or 0
            recalculated["hour_adjustment"] = next_data.get("hour_adjustment") or 0
            recalculated["vocacional"] = next_data.get("vocacional")
            next_data.update(recalculated)

        km_adjustment = _num(next_data.get("km_adjustment")) or 0.0
        hour_adjustment = _num(next_data.get("hour_adjustment")) or 0.0
        kms_raw = _num(next_data.get("kms_ecm_geotab"))
        kms_approved = _num(next_data.get("kms_ecm_approved"))
        if "kms_ecm_approved" not in provided_fields:
            kms_approved = kms_raw + km_adjustment if kms_raw is not None else None
        hours_raw = _num(next_data.get("hours_ecm"))
        hours_approved = _num(next_data.get("hours_ecm_approved"))
        if "hours_ecm_approved" not in provided_fields:
            hours_approved = hours_raw + hour_adjustment if hours_raw is not None else None
        derived_kms = kms_raw + km_adjustment if kms_raw is not None else None
        derived_hours = hours_raw + hour_adjustment if hours_raw is not None else None
        manual_kms = (
            kms_approved is not None
            and derived_kms is not None
            and abs(kms_approved - derived_kms) > 0.0001
        )
        manual_hours = (
            hours_approved is not None
            and derived_hours is not None
            and abs(hours_approved - derived_hours) > 0.0001
        )
        if (km_adjustment or hour_adjustment or manual_kms or manual_hours) and not (next_data.get("correction_note") or "").strip():
            raise CpkCphConflict("Cada correccion de CPK/CPH requiere nota.")
        km_diff, km_pct = _diff(
            kms_approved,
            _km_reference(_num(next_data.get("km_client")), _num(next_data.get("kms_gps"))),
        )
        hour_diff, hour_pct = _diff(hours_approved, _num(next_data.get("hours_gps")))
        next_data["km_adjustment"] = km_adjustment
        next_data["hour_adjustment"] = hour_adjustment
        next_data["kms_ecm_approved"] = kms_approved
        next_data["hours_ecm_approved"] = hours_approved
        next_data["km_difference"] = km_diff
        next_data["km_difference_pct"] = km_pct
        next_data["hour_difference"] = hour_diff
        next_data["hour_difference_pct"] = hour_pct
        next_data = _enrich_geotab_granular(next_data, month=summary.period_month)
        next_data = _append_geotab_validation_warnings(next_data)
        _reject_geotab_regression(next_data)

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cpk_cph_report_rows
                SET cutoff_start_at = %(cutoff_start_at)s,
                    cutoff_end_at = %(cutoff_end_at)s,
                    cutoff_start_utc = %(cutoff_start_utc)s,
                    cutoff_end_utc = %(cutoff_end_utc)s,
                    client_name = %(client_name)s,
                    database_name = %(database_name)s,
                    source_provider = %(source_provider)s,
                    provider_vehicle_id = %(provider_vehicle_id)s,
                    vocacional = %(vocacional)s,
                    km_client = %(km_client)s,
                    odo_start = %(odo_start)s,
                    odo_end = %(odo_end)s,
                    horo_start = %(horo_start)s,
                    horo_end = %(horo_end)s,
                    kms_ecm_geotab = %(kms_ecm_geotab)s,
                    kms_gps = %(kms_gps)s,
                    hours_ecm = %(hours_ecm)s,
                    hours_gps = %(hours_gps)s,
                    fuel_gallons = %(fuel_gallons)s,
                    geotab_regression_count = %(geotab_regression_count)s,
                    geotab_regression_total_km = %(geotab_regression_total_km)s,
                    geotab_regression_total_hours = %(geotab_regression_total_hours)s,
                    suggested_adjustment = %(suggested_adjustment)s,
                    km_adjustment = %(km_adjustment)s,
                    hour_adjustment = %(hour_adjustment)s,
                    kms_ecm_approved = %(kms_ecm_approved)s,
                    hours_ecm_approved = %(hours_ecm_approved)s,
                    km_difference = %(km_difference)s,
                    km_difference_pct = %(km_difference_pct)s,
                    hour_difference = %(hour_difference)s,
                    hour_difference_pct = %(hour_difference_pct)s,
                    calculation_status = %(calculation_status)s,
                    warnings = %(warnings)s::jsonb,
                    correction_note = %(correction_note)s,
                    updated_at = NOW()
                WHERE id = %(id)s AND report_id = %(report_id)s;
                """,
                {
                    **next_data,
                    "id": row_id,
                    "report_id": report_id,
                    "warnings": Jsonb(next_data.get("warnings") or []),
                },
            )
            cur.execute(
                "UPDATE cpk_cph_reports SET updated_by = %s, updated_at = NOW() WHERE id = %s;",
                (user_id, report_id),
            )
        conn.commit()
        return get_report(report_id, conn=conn)
