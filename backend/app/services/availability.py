"""
Calculo de "disponibilidad de proyectos" por vehiculo siguiendo la doc
docs/CALCULO_DISPONIBILIDAD.md.

Logica pura: sin DB ni HTTP. Recibe los inventarios devueltos por CloudFleet
y devuelve resultados por placa.

Formulas relevantes (ver doc):
  8.1  Conversion UTC -> Colombia (UTC-5).
  8.2  Horas no disponibles por orden (interseccion con periodo).
  8.4  Disponibilidad mensual de "consumo" — denominador = mes COMPLETO.

Disponibilidad de proyectos (seccion 7.2 de la doc):
  - Solo se cuentan ordenes con `affectsVehicleAvailability == true`.
  - Inicio del periodo no-disponible: startDate (fallback workshopDate).
  - Fin del periodo no-disponible: technicalCompletionDate.
    Si la orden sigue activa (no tiene technicalCompletionDate) -> usar
    end_datetime (momento del calculo).
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

_logger = logging.getLogger(__name__)

# UTC offset hardcoded segun la doc (la app esta atada a Colombia).
_LOCAL_OFFSET_HOURS = 5

# Estados inactivos segun doc seccion 6: el vehiculo se considera disponible,
# por lo que la orden no aporta horas de no-disponibilidad.
_INACTIVE_ORDER_STATUSES = frozenset({"closed", "cancelled", "voided", "completed", "finished"})


# ─────────────────────────────────────────────────────────────────────────────
# Tipos publicos
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AvailabilityTarget:
    """Una placa local a evaluar."""

    plate: str


# calculation_status posibles:
#   'calculated'         - calculo OK con al menos 1 orden afectando
#   'no_orders'          - placa existe en CloudFleet pero no tuvo ordenes en el mes
#   'not_in_cloudfleet'  - la placa local no aparece en GET /vehicles
#   'error'              - error puntual al evaluar una placa (ej: fechas mal formadas)
@dataclass
class AvailabilityResult:
    plate: str
    status: str
    h_total: float | None = None
    h_no_disp: float | None = None
    project_availability_pct: float | None = None
    orders_considered: int = 0
    mttr_hours: float | None = None
    orders_closed: int = 0
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────


def _parse_utc_to_local(utc_str: str | None) -> datetime | None:
    """
    Convierte un timestamp ISO-8601 UTC (formato CloudFleet
    'YYYY-MM-DDTHH:MM:SS.fffffffZ' con 7 digitos de fraccion) a hora local
    Colombia (UTC-5). Devuelve None si el string esta vacio o mal formado.
    """
    if not utc_str:
        return None
    text = str(utc_str).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    try:
        if "." in text:
            datetime_part, micro = text.split(".", 1)
            micro = micro[:6].ljust(6, "0")
            utc_dt = datetime.fromisoformat(f"{datetime_part}.{micro}")
        else:
            utc_dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return utc_dt - timedelta(hours=_LOCAL_OFFSET_HOURS)


def _normalize_plate(value: Any) -> str:
    if value is None:
        return ""
    return "".join(ch for ch in str(value).strip().upper() if ch.isalnum())


def _month_bounds_local(month: str) -> tuple[datetime, datetime, int]:
    """
    Devuelve (period_start_local, period_end_local_full, days_in_month).
    `period_end_local_full` es el ultimo segundo del mes (no truncado por now).
    """
    year, month_number = (int(part) for part in month.split("-"))
    days_in_month = calendar.monthrange(year, month_number)[1]
    start = datetime(year, month_number, 1, 0, 0, 0)
    end = datetime(year, month_number, days_in_month, 23, 59, 59)
    return start, end, days_in_month


def _month_bounds_utc(month: str) -> tuple[datetime, datetime]:
    """
    Para consultar work-orders por `updatedAt` necesitamos un rango UTC.
    Usamos un colchon de 1 dia a cada lado para no perdernos ordenes cuyo
    updatedAt cayo en zona limite por la conversion horaria.
    """
    start_local, end_local, _ = _month_bounds_local(month)
    start_utc = start_local + timedelta(hours=_LOCAL_OFFSET_HOURS) - timedelta(days=1)
    end_utc = end_local + timedelta(hours=_LOCAL_OFFSET_HOURS) + timedelta(days=1)
    return start_utc, end_utc


def _order_start_local(order: dict[str, Any]) -> datetime | None:
    """startDate, con fallback a workshopDate (segun doc seccion 7)."""
    primary = _parse_utc_to_local(order.get("startDate"))
    if primary is not None:
        return primary
    return _parse_utc_to_local(order.get("workshopDate"))


def _order_end_local_project(order: dict[str, Any], *, now: datetime) -> datetime | None:
    """
    Para disponibilidad de proyectos: el periodo no-disponible termina en
    technicalCompletionDate. Cascada de resolucion:

      1. technicalCompletionDate presente y parseable -> usarla.
      2. Si no, finalCompletionDate presente y parseable -> usarla
         (cierre conservador para ordenes cerradas sin completacion tecnica).
      3. Si no, y status normalizado esta en `_INACTIVE_ORDER_STATUSES`
         -> None: la orden no aporta horas (no esta "en curso").
      4. Cualquier otro caso (activa o estado desconocido) -> `now`.
    """
    tc = _parse_utc_to_local(order.get("technicalCompletionDate"))
    if tc is not None:
        return tc

    fc = _parse_utc_to_local(order.get("finalCompletionDate"))
    if fc is not None:
        return fc

    status = str(order.get("status") or "").strip().lower()
    if status in _INACTIVE_ORDER_STATUSES:
        return None

    return now


def _order_repair_hours(
    order: dict[str, Any],
    *,
    month_start: datetime,
    month_end_full: datetime,
) -> float | None:
    """
    Duracion de reparacion de una orden en horas, usada para MTTR mensual.

    - Solo se consideran ordenes CERRADAS DENTRO del mes.
    - El cierre real es `finalCompletionDate` (doc seccion 9), con fallback a
      `technicalCompletionDate` si no existe (nota: esta es una desviacion
      deliberada respecto a la doc, que usa finalCompletionDate).
    - Si no hay fecha de cierre real -> None (orden abierta, no cuenta).
    - Si el cierre cae fuera de [month_start, month_end_full] -> None.
    - Si el inicio es invalido o start >= end -> None.

    Nota: este modulo solo indexa ordenes con `affectsVehicleAvailability=true`,
    por lo que el MTTR calculado aqui considera exclusivamente ese subconjunto.
    Esto difiere de la doc seccion 9, que promedia todas las ordenes cerradas;
    la desviacion es deliberada y documentada.
    """
    end = _parse_utc_to_local(order.get("finalCompletionDate"))
    if end is None:
        end = _parse_utc_to_local(order.get("technicalCompletionDate"))
    if end is None:
        return None

    if end < month_start or end > month_end_full:
        return None

    start = _order_start_local(order)
    if start is None or start >= end:
        return None

    return (end - start).total_seconds() / 3600.0


def _order_unavailable_hours(
    order: dict[str, Any],
    *,
    period_start: datetime,
    period_end: datetime,
    now: datetime,
) -> float:
    """
    Formula 8.2 — interseccion entre el rango de la orden y el periodo.
    Devuelve 0.0 si no hay solape o si las fechas son invalidas.
    """
    order_start = _order_start_local(order)
    if order_start is None:
        return 0.0
    order_end = _order_end_local_project(order, now=now)
    if order_end is None:
        return 0.0

    effective_start = max(order_start, period_start)
    effective_end = min(order_end, period_end)
    if effective_start >= effective_end:
        return 0.0
    return (effective_end - effective_start).total_seconds() / 3600.0


def _extract_vehicle_code(vehicle: dict[str, Any]) -> str | None:
    value = vehicle.get("code")
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _affects_availability(order: dict[str, Any]) -> bool:
    value = order.get("affectsVehicleAvailability")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Funciones publicas
# ─────────────────────────────────────────────────────────────────────────────


def dedupe_work_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Deduplica ordenes por `number`, conservando la version con `updatedAt`
    mas reciente (doc seccion 3).

    - Clave: `number`; si falta o esta vacio, se usa `id`; si tampoco existe,
      se conserva la orden tal cual (no se descarta).
    - Entre duplicados gana el `updatedAt` mas reciente segun `_parse_utc_to_local`;
      si no parsea se trata como el mas viejo.
    - Mantiene el orden estable de primera aparicion.
    """
    seen: dict[Any, dict[str, Any]] = {}
    preserved_order: list[Any] = []

    for order in orders:
        if not isinstance(order, dict):
            continue
        raw_key = order.get("number") or order.get("id")
        if raw_key is None or str(raw_key).strip() == "":
            preserved_order.append(id(order))
            continue
        key = str(raw_key).strip()

        current = seen.get(key)
        if current is None:
            seen[key] = order
            preserved_order.append(key)
            continue

        current_updated = _parse_utc_to_local(current.get("updatedAt"))
        new_updated = _parse_utc_to_local(order.get("updatedAt"))
        # None se trata como "muy viejo", asi que solo gana si el actual
        # tambien es None (se queda con el primero) o si el nuevo es mas reciente.
        if current_updated is None:
            if new_updated is not None:
                seen[key] = order
        elif new_updated is not None and new_updated > current_updated:
            seen[key] = order

    result: list[dict[str, Any]] = []
    for token in preserved_order:
        if token in seen:
            result.append(seen[token])
        else:
            # token es id(order) de una orden sin clave conocida.
            for order in orders:
                if id(order) == token:
                    result.append(order)
                    break
    return result


def find_unmatched_cloudfleet_vehicles(
    cloudfleet_vehicles: list[dict[str, Any]],
    local_plates: set[str],
) -> list[dict[str, Any]]:
    """
    Devuelve los vehiculos de CloudFleet cuyo codigo normalizado no esta
    presente en el set de placas locales normalizadas.

    - `local_plates` debe contener placas YA normalizadas (usar `_normalize_plate`).
    - Se ignora cualquier vehiculo sin `code`.
    - Se deduplica por placa normalizada conservando la primera aparicion.
    - Resultado ordenado por `code` ascendente.
    """
    seen: dict[str, dict[str, Any]] = {}
    for vehicle in cloudfleet_vehicles:
        if not isinstance(vehicle, dict):
            continue
        code = _extract_vehicle_code(vehicle)
        if not code:
            continue
        normalized = _normalize_plate(code)
        if not normalized or normalized in local_plates or normalized in seen:
            continue
        cost_center = (vehicle.get("costCenter") or {}).get("name") or None
        seen[normalized] = {"code": code, "normalized": normalized, "cost_center": cost_center}
    return sorted(seen.values(), key=lambda item: item["code"])


def calculate_availability_for_targets(
    targets: list[AvailabilityTarget],
    *,
    month: str,
    cloudfleet_vehicles: list[dict[str, Any]],
    cloudfleet_work_orders: list[dict[str, Any]],
    now: datetime,
) -> list[AvailabilityResult]:
    """
    Para cada `target` calcula la disponibilidad de proyectos del mes solicitado.

    Pasos por placa:
      1. Match exacto contra cloudfleet_vehicles por (vehicle.code == plate).
         Si no hay match -> status='not_in_cloudfleet'.
      2. Filtrar ordenes por (vehicleCode == plate AND affectsVehicleAvailability).
         Si no hay ordenes -> status='no_orders', pct=100%.
      3. Calcular h_no_disp como suma de intersecciones (formula 8.2).
      4. h_total = dias_del_mes * 24 (un solo vehiculo, denominador completo).
      5. pct = max(0, min(100, (h_total - h_no_disp) / h_total * 100)).
      6. MTTR mensual: promedio de horas de reparacion de las ordenes cerradas
         DENTRO del mes (ver `_order_repair_hours`).

    Notas:
      - Para meses pasados completos: period_end = ultimo segundo del mes.
      - Para mes actual: period_end = min(now, ultimo segundo del mes).
      - now se inyecta para facilitar testing.
      - El MTTR aqui considera solo ordenes con `affectsVehicleAvailability=true`
        (las unicas que indexa este modulo), a diferencia de la doc seccion 9
        que usa todas las ordenes cerradas. Esta desviacion es deliberada.
    """
    period_start, period_end_full, days_in_month = _month_bounds_local(month)
    h_total = days_in_month * 24.0

    # Para mes actual recortamos period_end a `now` (asi solo contamos horas
    # transcurridas), siguiendo la idea de la formula 8.4 (consumo mensual).
    if period_start <= now <= period_end_full:
        period_end = now
    else:
        period_end = period_end_full

    # Indice de vehiculos CloudFleet por codigo normalizado
    vehicles_by_code: dict[str, dict[str, Any]] = {}
    for vehicle in cloudfleet_vehicles:
        if not isinstance(vehicle, dict):
            continue
        code = _extract_vehicle_code(vehicle)
        if not code:
            continue
        vehicles_by_code[_normalize_plate(code)] = vehicle

    # Indice de ordenes por vehicleCode normalizado (solo las que afectan)
    orders_by_plate: dict[str, list[dict[str, Any]]] = {}
    for order in cloudfleet_work_orders:
        if not isinstance(order, dict):
            continue
        if not _affects_availability(order):
            continue
        code = order.get("vehicleCode")
        if not code:
            continue
        norm = _normalize_plate(code)
        if not norm:
            continue
        orders_by_plate.setdefault(norm, []).append(order)

    results: list[AvailabilityResult] = []
    for target in targets:
        norm_plate = _normalize_plate(target.plate)
        if not norm_plate:
            results.append(
                AvailabilityResult(
                    plate=target.plate,
                    status="error",
                    error_message="Placa local vacia.",
                )
            )
            continue

        if norm_plate not in vehicles_by_code:
            results.append(
                AvailabilityResult(
                    plate=target.plate,
                    status="not_in_cloudfleet",
                )
            )
            continue

        plate_orders = orders_by_plate.get(norm_plate, [])
        if not plate_orders:
            results.append(
                AvailabilityResult(
                    plate=target.plate,
                    status="no_orders",
                    h_total=h_total,
                    h_no_disp=0.0,
                    project_availability_pct=100.0,
                    orders_considered=0,
                )
            )
            continue

        try:
            h_no_disp = 0.0
            repair_durations: list[float] = []
            for order in plate_orders:
                h_no_disp += _order_unavailable_hours(
                    order,
                    period_start=period_start,
                    period_end=period_end,
                    now=now,
                )
                repair_hours = _order_repair_hours(
                    order,
                    month_start=period_start,
                    month_end_full=period_end_full,
                )
                if repair_hours is not None:
                    repair_durations.append(repair_hours)

            # No deberia pasar pero por si las fechas se solapan entre ordenes:
            h_no_disp = max(0.0, min(h_no_disp, h_total))
            pct = (h_total - h_no_disp) / h_total * 100.0
            pct = max(0.0, min(100.0, pct))

            orders_closed = len(repair_durations)
            mttr_hours = round(sum(repair_durations) / orders_closed, 3) if repair_durations else None

            results.append(
                AvailabilityResult(
                    plate=target.plate,
                    status="calculated",
                    h_total=round(h_total, 3),
                    h_no_disp=round(h_no_disp, 3),
                    project_availability_pct=round(pct, 3),
                    orders_considered=len(plate_orders),
                    mttr_hours=mttr_hours,
                    orders_closed=orders_closed,
                )
            )
        except Exception as exc:
            _logger.exception("Error calculando disponibilidad de %s", target.plate)
            results.append(
                AvailabilityResult(
                    plate=target.plate,
                    status="error",
                    orders_considered=len(plate_orders),
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            )

    return results


__all__ = [
    "AvailabilityTarget",
    "AvailabilityResult",
    "calculate_availability_for_targets",
    "dedupe_work_orders",
    "find_unmatched_cloudfleet_vehicles",
    "_month_bounds_utc",
]
