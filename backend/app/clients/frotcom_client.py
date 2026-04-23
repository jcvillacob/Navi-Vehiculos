from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests

FROTCOM_BASE_URL = "https://v2api.frotcom.com"
FROTCOM_PROVIDER = "frotcom"

_LITERS_PER_GALLON = 3.7854118
_HTTP_TIMEOUT = 45
_AUTH_FAILURE_TTL_SECONDS = 60
_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[tuple[str, str, str], str] = {}
_AUTH_FAILURE_CACHE: dict[tuple[str, str, str], tuple[float, str]] = {}


class FrotcomAuthError(RuntimeError):
    """Credenciales Frotcom invalidas o rechazadas por el servidor."""


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
            "Credenciales Frotcom invalidas. Revisa usuario y contrasena de la database."
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


def _frotcom_get(config: FrotcomConfig, path: str, params: dict[str, Any]) -> Any:
    token = get_access_token(config)
    url = f"{config.base_url.rstrip('/')}{path}"
    query = {"api_key": token, **params}
    response = requests.get(url, params=query, timeout=_HTTP_TIMEOUT)
    if response.status_code == 401:
        _invalidate_token(config)
        token = get_access_token(config, force_refresh=True)
        query["api_key"] = token
        response = requests.get(url, params=query, timeout=_HTTP_TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(
            f"Frotcom GET {path} fallo (HTTP {response.status_code}): {response.text[:200]}"
        )
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
    valid = [row for row in data if isinstance(row, dict) and _to_float(row.get("odometer")) not in (None, 0.0)]
    return sorted(valid, key=lambda row: str(row.get("date") or ""))


@dataclass(frozen=True)
class FrotcomReading:
    odometer: float | None
    total_fuel_used: float | None
    date: str | None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_to_reading(row: dict[str, Any]) -> FrotcomReading:
    return FrotcomReading(
        odometer=_to_float(row.get("odometer")),
        total_fuel_used=_to_float(row.get("totalFuelUsed")),
        date=str(row.get("date") or "") or None,
    )


def find_first_reading(
    config: FrotcomConfig,
    vehicle_id: str,
    start_date: datetime,
    end_date: datetime,
) -> FrotcomReading | None:
    current = start_date
    max_days = abs((end_date - start_date).days) + 2
    attempts = 0
    while attempts <= max_days and current <= end_date:
        records = get_can_day_records(config, vehicle_id, current.strftime("%Y-%m-%d"))
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
) -> FrotcomReading | None:
    current = end_date
    max_days = abs((end_date - start_date).days) + 2
    attempts = 0
    while attempts <= max_days and current >= start_date:
        records = get_can_day_records(config, vehicle_id, current.strftime("%Y-%m-%d"))
        if records:
            return _row_to_reading(records[-1])
        current = current - timedelta(days=1)
        attempts += 1
    return None


def get_frotcom_month_range(year: int, month_number: int) -> tuple[str, str, datetime, datetime]:
    start = datetime(year, month_number, 1, 0, 0, 0)
    if month_number == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month_number + 1, 1)
    end = (next_month - timedelta(days=1)).replace(hour=23, minute=59, second=59)
    return start.isoformat(), end.isoformat(), start, end


def liters_to_gallons(liters: float | None) -> float | None:
    if liters is None:
        return None
    return liters / _LITERS_PER_GALLON


def hours_from_seconds(seconds: Any) -> float | None:
    value = _to_float(seconds)
    if value is None:
        return None
    return value / 3600.0
