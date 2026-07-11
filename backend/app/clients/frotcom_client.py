from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

FROTCOM_BASE_URL = "https://v2api.frotcom.com"
FROTCOM_PROVIDER = "frotcom"

_LITERS_PER_GALLON = 3.7854118
_HTTP_TIMEOUT = 45
_HTTP_MAX_ATTEMPTS = 3
_HTTP_RETRY_STATUSES = {429, 502, 503, 504}
_TRIPS_CHUNK_STEP_DAYS = 6
_TRIPS_CHUNK_SPAN_DAYS = 7  # 6 + 1 de overlap; el endpoint /trips rechaza rangos mayores a ~7 dias
_TRIP_DATE_FIELDS = ("started", "startDate", "startTime", "start", "dateStart",
                     "departureDate", "departureTime", "beginDate", "df")
_TRIP_DISTANCE_FIELDS = ("distance", "mileage", "tripDistance", "km",
                         "totalDistance", "distanceKms", "mileageKms", "gpsMileage")
_BOGOTA_UTC_OFFSET = timedelta(hours=-5)
_AUTH_FAILURE_TTL_SECONDS = 60
_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[tuple[str, str, str], str] = {}
_AUTH_FAILURE_CACHE: dict[tuple[str, str, str], tuple[float, str]] = {}


class FrotcomAuthError(RuntimeError):
    """Credenciales Frotcom invalidas o rechazadas por el servidor."""


class FrotcomRequestError(RuntimeError):
    """Error HTTP de un endpoint Frotcom ya autenticado."""

    def __init__(self, path: str, status_code: int, body: str = "") -> None:
        self.path = path
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"Frotcom GET {path} fallo (HTTP {status_code}): {body[:200]}"
        )


@dataclass(frozen=True)
class FrotcomConfig:
    username: str
    password: str
    base_url: str = FROTCOM_BASE_URL

    def cache_key(self) -> tuple[str, str, str]:
        return (self.base_url.rstrip("/"), self.username, self.password)


def _authorize_token(config: FrotcomConfig) -> str:
    url = f"{config.base_url.rstrip('/')}/v2/authorize"
    response = requests.post(
        url,
        data={
            "provider": FROTCOM_PROVIDER,
            "username": config.username,
            "password": config.password,
        },
        timeout=_HTTP_TIMEOUT,
    )
    if response.status_code in (401, 403):
        raise FrotcomAuthError(
            "Credenciales Frotcom invalidas. Revisa usuario y contraseña de la database."
        )
    if response.status_code != 201:
        raise RuntimeError(
            f"Frotcom autorizacion fallida (HTTP {response.status_code}): {response.text[:200]}"
        )
    token = (response.json() or {}).get("token")
    if not token:
        raise RuntimeError("Frotcom no devolvio token de acceso.")
    return str(token)


def _get_cached_auth_failure(key: tuple[str, str, str]) -> str | None:
    entry = _AUTH_FAILURE_CACHE.get(key)
    if not entry:
        return None
    failed_at, message = entry
    if time.monotonic() - failed_at > _AUTH_FAILURE_TTL_SECONDS:
        _AUTH_FAILURE_CACHE.pop(key, None)
        return None
    return message


def get_access_token(config: FrotcomConfig, force_refresh: bool = False) -> str:
    key = config.cache_key()
    with _TOKEN_LOCK:
        if not force_refresh:
            cached = _TOKEN_CACHE.get(key)
            if cached:
                return cached
            cached_failure = _get_cached_auth_failure(key)
            if cached_failure is not None:
                raise FrotcomAuthError(cached_failure)
        try:
            token = _authorize_token(config)
        except FrotcomAuthError as exc:
            _AUTH_FAILURE_CACHE[key] = (time.monotonic(), str(exc))
            _TOKEN_CACHE.pop(key, None)
            raise
        _TOKEN_CACHE[key] = token
        _AUTH_FAILURE_CACHE.pop(key, None)
        return token


def clear_auth_failure(config: FrotcomConfig) -> None:
    """Invalida el cache negativo; util si el operador acaba de corregir credenciales."""
    with _TOKEN_LOCK:
        _AUTH_FAILURE_CACHE.pop(config.cache_key(), None)


def _invalidate_token(config: FrotcomConfig) -> None:
    key = config.cache_key()
    with _TOKEN_LOCK:
        _TOKEN_CACHE.pop(key, None)


def _frotcom_get(
    config: FrotcomConfig,
    path: str,
    params: dict[str, Any],
    *,
    allow_no_content: bool = False,
) -> Any:
    token = get_access_token(config)
    url = f"{config.base_url.rstrip('/')}{path}"
    query = {"api_key": token, **params}
    refreshed_token = False
    response = None
    for attempt in range(_HTTP_MAX_ATTEMPTS):
        response = requests.get(url, params=query, timeout=_HTTP_TIMEOUT)
        if response.status_code == 401 and not refreshed_token:
            _invalidate_token(config)
            token = get_access_token(config, force_refresh=True)
            query["api_key"] = token
            refreshed_token = True
            continue
        if response.status_code in _HTTP_RETRY_STATUSES and attempt < _HTTP_MAX_ATTEMPTS - 1:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = max(0.0, min(float(retry_after), 10.0))
            except (TypeError, ValueError):
                delay = 0.5 * (2**attempt)
            time.sleep(delay)
            continue
        break
    if response is None:
        raise RuntimeError(f"Frotcom GET {path} no produjo respuesta.")
    if allow_no_content and response.status_code == 204:
        return None
    if response.status_code != 200:
        raise FrotcomRequestError(path, response.status_code, response.text)
    return response.json()


def list_vehicles(config: FrotcomConfig) -> list[dict[str, Any]]:
    data = _frotcom_get(config, "/v2/vehicles", {})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("vehicles", "data", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _normalize_plate(value: Any) -> str:
    if value is None:
        return ""
    return "".join(char for char in str(value).strip().upper() if char.isalnum())


def _extract_plate_from_vehicle(vehicle: dict[str, Any]) -> str | None:
    for key in ("registration", "licensePlate", "license_plate", "plate", "placa"):
        value = vehicle.get(key)
        if value:
            return str(value).strip()
    return None


def _extract_id_from_vehicle(vehicle: dict[str, Any]) -> str | None:
    for key in ("id", "vehicleId", "vehicle_id"):
        value = vehicle.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def find_vehicle_id_by_plate(
    plate: str,
    config: FrotcomConfig,
    vehicles: list[dict[str, Any]] | None = None,
) -> str | None:
    normalized = _normalize_plate(plate)
    if not normalized:
        return None
    collection = vehicles if vehicles is not None else list_vehicles(config)
    for vehicle in collection:
        if not isinstance(vehicle, dict):
            continue
        if _normalize_plate(_extract_plate_from_vehicle(vehicle)) == normalized:
            return _extract_id_from_vehicle(vehicle)
    return None


def get_mileage_and_time(
    config: FrotcomConfig, vehicle_id: str, df: str, dt: str
) -> dict[str, Any] | None:
    data = _frotcom_get(
        config,
        f"/v2/vehicles/{vehicle_id}/mileageandtime",
        {"df": df, "dt": dt},
    )
    return data if isinstance(data, dict) else None


def get_can_day_records(
    config: FrotcomConfig, vehicle_id: str, date_str: str
) -> list[dict[str, Any]]:
    data = _frotcom_get(
        config,
        f"/v2/vehicles/{vehicle_id}/vehicleCanInfo",
        {"date": date_str},
    )
    if not isinstance(data, list):
        return []
    valid = [
        row
        for row in data
        if isinstance(row, dict)
        and any(
            _to_float(row.get(field)) not in (None, 0.0)
            for field in ("odometer", "totalFuelUsed", "engineHours")
        )
    ]
    return sorted(valid, key=lambda row: str(row.get("date") or ""))


@dataclass(frozen=True)
class FrotcomReading:
    odometer: float | None
    total_fuel_used: float | None
    date: str | None
    engine_hours: float | None = None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_to_reading(row: dict[str, Any]) -> FrotcomReading:
    engine_hours_seconds = _to_float(row.get("engineHours"))
    return FrotcomReading(
        odometer=_to_float(row.get("odometer")),
        total_fuel_used=_to_float(row.get("totalFuelUsed")),
        date=str(row.get("date") or "") or None,
        # Aunque el campo se llama engineHours, el API entrega el acumulado
        # en segundos (confirmado con las respuestas reales del tenant).
        engine_hours=(engine_hours_seconds / 3600.0 if engine_hours_seconds is not None else None),
    )


def _effective_can_end_date(end_date: datetime) -> datetime:
    """Evita consultar dias futuros cuando se calcula el mes en curso."""
    if end_date.tzinfo is not None:
        now = datetime.now(end_date.tzinfo)
    else:
        now = datetime.now(timezone(_BOGOTA_UTC_OFFSET)).replace(tzinfo=None)
    return min(end_date, now)


def find_first_reading(
    config: FrotcomConfig,
    vehicle_id: str,
    start_date: datetime,
    end_date: datetime,
    daily_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> FrotcomReading | None:
    end_date = _effective_can_end_date(end_date)
    current = start_date
    max_days = abs((end_date - start_date).days) + 2
    attempts = 0
    while attempts <= max_days and current <= end_date:
        date_str = current.strftime("%Y-%m-%d")
        records = daily_cache.get(date_str) if daily_cache is not None else None
        if records is None:
            records = get_can_day_records(config, vehicle_id, date_str)
            if daily_cache is not None:
                daily_cache[date_str] = records
        if records:
            return _row_to_reading(records[0])
        current = current + timedelta(days=1)
        attempts += 1
    return None


def find_last_reading(
    config: FrotcomConfig,
    vehicle_id: str,
    start_date: datetime,
    end_date: datetime,
    daily_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> FrotcomReading | None:
    end_date = _effective_can_end_date(end_date)
    current = end_date
    max_days = abs((end_date - start_date).days) + 2
    attempts = 0
    while attempts <= max_days and current >= start_date:
        date_str = current.strftime("%Y-%m-%d")
        records = daily_cache.get(date_str) if daily_cache is not None else None
        if records is None:
            records = get_can_day_records(config, vehicle_id, date_str)
            if daily_cache is not None:
                daily_cache[date_str] = records
        if records:
            return _row_to_reading(records[-1])
        current = current - timedelta(days=1)
        attempts += 1
    return None


def _parse_trip_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def trip_start_datetime(trip: dict[str, Any]) -> datetime | None:
    for field in _TRIP_DATE_FIELDS:
        parsed = _parse_trip_datetime(trip.get(field))
        if parsed is not None:
            return parsed
    return None


def trip_distance(trip: dict[str, Any]) -> float | None:
    for field in _TRIP_DISTANCE_FIELDS:
        value = _to_float(trip.get(field))
        if value is not None:
            return value
    return None


def sort_trips_chronologically(trips: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    dated: list[tuple[datetime, dict[str, Any]]] = []
    undated = 0
    for trip in trips:
        started = trip_start_datetime(trip)
        if started is None:
            undated += 1
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        dated.append((started, trip))
    dated.sort(key=lambda pair: pair[0])
    return [trip for _, trip in dated], undated


def format_frotcom_utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_trips_range(
    config: FrotcomConfig,
    vehicle_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> tuple[list[dict[str, Any]], list[str]]:
    trips_by_key: dict[Any, dict[str, Any]] = {}
    warnings: list[str] = []
    synthetic_index = 0

    current = start_utc
    while current < end_utc:
        block_end = min(current + timedelta(days=_TRIPS_CHUNK_SPAN_DAYS), end_utc)
        df = format_frotcom_utc(current)
        dt = format_frotcom_utc(block_end)
        try:
            data = _frotcom_get(
                config,
                f"/v2/vehicles/{vehicle_id}/trips",
                {"df": df, "dt": dt},
                allow_no_content=True,
            )
        except FrotcomAuthError:
            raise
        except RuntimeError as exc:
            warnings.append(f"Bloque {df}–{dt} de viajes Frotcom fallo: {exc}")
            data = None
            # 403/404 indican que este tenant o vehiculo no expone /trips.
            # Repetir todos los bloques solo agrega latencia y warnings iguales.
            if isinstance(exc, FrotcomRequestError) and exc.status_code in (403, 404):
                break
        if isinstance(data, list):
            for trip in data:
                if not isinstance(trip, dict):
                    continue
                trip_id = trip.get("id")
                if trip_id in (None, ""):
                    key: Any = ("noid", synthetic_index)
                    synthetic_index += 1
                else:
                    key = str(trip_id)
                trips_by_key[key] = trip
        current = min(current + timedelta(days=_TRIPS_CHUNK_STEP_DAYS), end_utc)

    return list(trips_by_key.values()), warnings


@dataclass(frozen=True)
class FrotcomTripOdometers:
    odo_start: float | None
    odo_end: float | None
    reconstructed_start: bool
    reconstruction_incomplete: bool
    trip_count: int
    warnings: list[str]


def derive_trip_odometers(trips_sorted: list[dict[str, Any]]) -> FrotcomTripOdometers:
    warnings: list[str] = []

    odo_end = None
    for trip in reversed(trips_sorted):
        odo_end = _to_float(trip.get("endOdometer"))
        if odo_end is not None:
            break

    odo_start = None
    first_odo_index = None
    for index, trip in enumerate(trips_sorted):
        odo_start = _to_float(trip.get("startOdometer"))
        if odo_start is not None:
            first_odo_index = index
            break

    reconstructed_start = False
    reconstruction_incomplete = False
    if odo_start is not None and first_odo_index:
        reconstructed = 0.0
        for trip in trips_sorted[:first_odo_index]:
            distance = trip_distance(trip)
            if distance is None:
                reconstruction_incomplete = True
            else:
                reconstructed += distance
        odo_start -= reconstructed
        reconstructed_start = True
        warnings.append(
            f"Odometro inicial reconstruido restando distancias de {first_odo_index} viaje(s) previo(s) sin odometro."
        )
        if reconstruction_incomplete:
            warnings.append(
                "Algunos viajes previos sin odometro tampoco reportan distancia; el odometro inicial puede quedar sobreestimado."
            )

    return FrotcomTripOdometers(
        odo_start=odo_start,
        odo_end=odo_end,
        reconstructed_start=reconstructed_start,
        reconstruction_incomplete=reconstruction_incomplete,
        trip_count=len(trips_sorted),
        warnings=warnings,
    )


def get_trip_based_odometers(
    config: FrotcomConfig,
    vehicle_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> FrotcomTripOdometers:
    trips, fetch_warnings = fetch_trips_range(config, vehicle_id, start_utc, end_utc)
    trips_sorted, undated = sort_trips_chronologically(trips)
    result = derive_trip_odometers(trips_sorted)
    warnings = [*fetch_warnings, *result.warnings]
    if undated:
        warnings.append(f"{undated} viaje(s) Frotcom sin fecha de inicio fueron excluidos del ordenamiento.")
    return FrotcomTripOdometers(
        odo_start=result.odo_start,
        odo_end=result.odo_end,
        reconstructed_start=result.reconstructed_start,
        reconstruction_incomplete=result.reconstruction_incomplete,
        trip_count=result.trip_count,
        warnings=warnings,
    )


def get_frotcom_month_range(year: int, month_number: int) -> tuple[str, str, datetime, datetime]:
    start = datetime(year, month_number, 1, 0, 0, 0)
    if month_number == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month_number + 1, 1)
    end = (next_month - timedelta(days=1)).replace(hour=23, minute=59, second=59)
    start_utc, end_utc = get_frotcom_month_range_utc_bounds(year, month_number)
    return format_frotcom_utc(start_utc), format_frotcom_utc(end_utc), start, end


def get_frotcom_month_range_utc_bounds(year: int, month_number: int) -> tuple[datetime, datetime]:
    bogota = timezone(_BOGOTA_UTC_OFFSET)
    start = datetime(year, month_number, 1, 0, 0, 0, tzinfo=bogota)
    if month_number == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=bogota)
    else:
        next_month = datetime(year, month_number + 1, 1, tzinfo=bogota)
    end = next_month - timedelta(seconds=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def liters_to_gallons(liters: float | None) -> float | None:
    if liters is None:
        return None
    return liters / _LITERS_PER_GALLON


def hours_from_seconds(seconds: Any) -> float | None:
    value = _to_float(seconds)
    if value is None:
        return None
    return value / 3600.0
