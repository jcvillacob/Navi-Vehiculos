from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


def _normalize_key(value: str) -> str:
    return "".join(char for char in value.strip().lower() if char.isalnum())


def _build_field_index(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {_normalize_key(str(key)): value for key, value in row.items()}


def _first_value(row: dict[str, Any] | None, *candidates: str) -> Any:
    field_index = _build_field_index(row)
    for candidate in candidates:
        normalized = _normalize_key(candidate)
        if normalized in field_index:
            return field_index[normalized]
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_plate(row: dict[str, Any] | None) -> str | None:
    value = _first_value(row, "plate", "placa", "licenseplate")
    if value is None:
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def extract_provider_vehicle_id(row: dict[str, Any] | None) -> str | None:
    value = _first_value(
        row,
        "resourceid",
        "resource_id",
        "resourceuuid",
        "resource_uuid",
        "resource",
        "deviceid",
        "device_id",
        "vehicleid",
        "vehicle_id",
        "unitid",
        "unit_id",
        "id",
    )
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def extract_odometer(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "odometer", "odometro"))


def extract_horometer(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "horometer", "horometro", "hourmeter"))


def extract_consumption_liters(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "consumption", "fuelconsumption", "consumocombustible"))


def extract_engine_time_hours(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "enginetime", "enginehours", "hours", "horas"))


def extract_distance_km(row: dict[str, Any] | None) -> float | None:
    return _to_float(
        _first_value(
            row,
            "distance",
            "distancekm",
            "distance_km",
            "kms",
            "kilometers",
            "kilometros",
            "mileage",
        )
    )


def extract_timestamp(row: dict[str, Any] | None) -> str | None:
    value = _first_value(
        row,
        "datetime",
        "date",
        "timestamp",
        "eventtime",
        "gpsdatetime",
        "createdat",
    )
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def gallons_from_liters(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 3.78541


def sort_rows_by_timestamp(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: extract_timestamp(row) or "")


@dataclass(frozen=True)
class ArtimoConfig:
    username: str
    password: str
    customer_id: str
    group_name: str
    api_base_url: str = "https://api.artimo.com.co"
    auth_base_url: str = "https://apifront.artimo.com.co"
    month_start_hour_utc: int = 5
    month_end_hour_utc: int = 16


class ArtimoAuthError(RuntimeError):
    """Credenciales Artimo invalidas o rechazadas por el servidor."""


class ArtimoClient:
    def __init__(self, config: ArtimoConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "accept": "application/json",
                "content-type": "application/json",
            }
        )
        self.cookies: dict[str, str] | None = None

    def login(self) -> None:
        hashed_password = hashlib.sha256(self.config.password.encode()).hexdigest()
        auth = base64.b64encode(f"{self.config.username}:{hashed_password}".encode()).decode()
        response = self.session.post(
            f"{self.config.auth_base_url.rstrip('/')}/auth/sign-in",
            headers={"authorization": f"Basic {auth}"},
            json={"userBrowserDetails": {"browser": "Chrome", "os": "Linux"}},
            timeout=30,
        )
        if response.status_code in (401, 403):
            raise ArtimoAuthError(
                "Credenciales Artimo invalidas. Revisa usuario y contraseña de la database."
            )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("token") or "").strip()
        refresh = str(payload.get("refresh") or "").strip()
        if not token or not refresh:
            raise RuntimeError("Artimo no devolvio credenciales de sesion.")
        self.cookies = {"token": token, "refresh": refresh}

    def get_report(
        self,
        report_type: str,
        start_date: str,
        end_date: str,
        *,
        resource_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.cookies:
            self.login()
        report_id = uuid.uuid4().hex
        payload = {
            "config": {
                "execute": "execute",
                "start": 0,
                "end": 50000,
                "length": 50000,
                "reportName": report_type,
            },
            "params": {
                "Customer": self.config.customer_id,
                "Groups": [self.config.group_name] if report_type == "trips" else [],
                "Resources": [resource_id] if resource_id else [],
                "Params": {
                    "start": start_date,
                    "end": end_date,
                    "reportBy": 0,
                    "groupParam": 1,
                    "Resources": resource_id or "",
                },
            },
        }
        response = self.session.post(
            f"{self.config.api_base_url.rstrip('/')}/reports/getreport/{report_id}?",
            cookies=self.cookies,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        data = body.get("data", {}).get("data", [])
        return data if isinstance(data, list) else []

    def get_month_range(self, year: int, month: int) -> tuple[str, str]:
        start = datetime(year, month, 1, self.config.month_start_hour_utc, 0, 0, tzinfo=timezone.utc)
        if month == 12:
            next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        last_day = next_month - timedelta(days=1)
        end = last_day.replace(
            hour=self.config.month_end_hour_utc,
            minute=59,
            second=59,
            microsecond=999000,
        )
        return (start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), end.strftime("%Y-%m-%dT%H:%M:%S.999Z"))
