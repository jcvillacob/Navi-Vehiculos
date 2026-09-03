from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.clients.http_retry import retry_http

logger = logging.getLogger(__name__)


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


def parse_local_datetime(value: Any) -> datetime | None:
    """Los reportes devuelven fecha local sin zona ('2026-06-30 21:44:11')."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(pattern) + 2].strip(), pattern)
        except ValueError:
            continue
    return None


def extract_trip_end(row: dict[str, Any] | None) -> datetime | None:
    return parse_local_datetime(_first_value(row, "enddate", "end_date", "end"))


def extract_trip_start(row: dict[str, Any] | None) -> datetime | None:
    return parse_local_datetime(_first_value(row, "startdate", "start_date", "start"))


@dataclass(frozen=True)
class ArtimoTripWindow:
    """Viajes del periodo, repartidos por su fecha de FIN.

    Artimo filtra los viajes por su inicio, así que el último de una ventana
    puede terminar ya entrado el periodo siguiente. Cada viaje cuenta en el
    periodo donde termina: así el cierre nunca se pasa del corte y ningún
    kilómetro se pierde entre meses.
    """

    close_trip: dict[str, Any] | None
    distance_km: float | None
    engine_hours: float | None
    fuel_liters: float | None
    trip_count: int = 0
    trips_after_window: int = 0
    # Viajes dentro de la ventana que NO traen el campo (venian sumando 0.0 en
    # silencio). El proveedor avisa y, si superan el umbral, anula la metrica.
    missing_distance: int = 0
    missing_hours: int = 0
    missing_consumption: int = 0

    @property
    def odometer(self) -> float | None:
        return extract_odometer(self.close_trip)

    @property
    def horometer(self) -> float | None:
        return extract_horometer(self.close_trip)


def select_trips_in_window(
    rows: list[dict[str, Any]],
    *,
    window_start_local: datetime,
    window_end_local: datetime,
) -> ArtimoTripWindow:
    inside: list[dict[str, Any]] = []
    after = 0
    for row in rows:
        trip_end = extract_trip_end(row)
        if trip_end is None:
            continue
        if trip_end > window_end_local:
            after += 1
            continue
        if trip_end < window_start_local:
            continue
        inside.append(row)

    if not inside:
        return ArtimoTripWindow(
            close_trip=None,
            distance_km=None,
            engine_hours=None,
            fuel_liters=None,
            trips_after_window=after,
        )

    inside.sort(key=lambda row: extract_trip_end(row) or datetime.min)
    distances = [extract_distance_km(row) for row in inside]
    hours = [extract_engine_time_hours(row) for row in inside]
    liters = [extract_consumption_liters(row) for row in inside]
    return ArtimoTripWindow(
        close_trip=inside[-1],
        distance_km=sum(value or 0.0 for value in distances),
        engine_hours=sum(value or 0.0 for value in hours),
        fuel_liters=sum(value or 0.0 for value in liters),
        trip_count=len(inside),
        trips_after_window=after,
        missing_distance=sum(1 for value in distances if value is None),
        missing_hours=sum(1 for value in hours if value is None),
        missing_consumption=sum(1 for value in liters if value is None),
    )


_REPORT_PAGE_LIMIT = 50000
_MIN_SPLIT_SECONDS = 3600
# Tope de profundidad al partir ventanas (2**6 = 64 ventanas) y presupuesto
# total de peticiones por llamada a `get_report_paged`. Sin ellos un recurso
# con muchisimos puntos GPS podia disparar ~1500 requests y colgar el job.
_MAX_SPLIT_DEPTH = 6
_MAX_REQUESTS_PER_REPORT = 200
_HTTP_MAX_ATTEMPTS = 3


class ArtimoReportTooLarge(RuntimeError):
    """El reporte no cabe en el presupuesto de peticiones / profundidad de split.

    Lleva en `rows` lo recolectado hasta el corte y en `requests_made` el
    numero de peticiones hechas, por si el llamador quiere aprovecharlas.
    """

    def __init__(self, message: str, *, rows: list[dict[str, Any]] | None = None, requests_made: int = 0) -> None:
        super().__init__(message)
        self.rows = rows or []
        self.requests_made = requests_made


@dataclass
class _PagingBudget:
    requests_made: int = 0
    collected: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ArtimoReportPage:
    rows: list[dict[str, Any]]
    truncated: bool = False


def _parse_api_instant(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _format_api_instant(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(sorted((str(k), str(v)) for k, v in row.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


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
        response = retry_http(
            lambda: self.session.post(
                f"{self.config.auth_base_url.rstrip('/')}/auth/sign-in",
                headers={"authorization": f"Basic {auth}"},
                json={"userBrowserDetails": {"browser": "Chrome", "os": "Linux"}},
                timeout=30,
            ),
            describe="Artimo POST /auth/sign-in",
            max_attempts=_HTTP_MAX_ATTEMPTS,
            logger=logger,
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
        group_param: int = 1,
        use_group: bool | None = None,
    ) -> list[dict[str, Any]]:
        """`groupParam` decide el nivel de agregación del reporte de viajes:
        1 agrupa el periodo en una fila por placa, 3 devuelve viaje por viaje.
        """
        if not self.cookies:
            self.login()
        if use_group is None:
            use_group = report_type == "trips" and group_param != 3
        report_id = uuid.uuid4().hex
        payload = {
            "config": {
                "execute": "execute",
                "start": 0,
                "end": _REPORT_PAGE_LIMIT,
                "length": _REPORT_PAGE_LIMIT,
                "reportName": report_type,
            },
            "params": {
                "Customer": self.config.customer_id,
                "Groups": [self.config.group_name] if use_group else [],
                "Resources": [resource_id] if resource_id else [],
                "Params": {
                    "start": start_date,
                    "end": end_date,
                    "reportBy": 0,
                    "groupParam": group_param,
                    "Resources": resource_id or "",
                },
            },
        }
        response = retry_http(
            lambda: self.session.post(
                f"{self.config.api_base_url.rstrip('/')}/reports/getreport/{report_id}?",
                cookies=self.cookies,
                json=payload,
                timeout=60,
            ),
            describe=f"Artimo POST /reports/getreport ({report_type}, {start_date}..{end_date})",
            max_attempts=_HTTP_MAX_ATTEMPTS,
            logger=logger,
        )
        response.raise_for_status()
        body = response.json()
        data = body.get("data", {}).get("data", [])
        return data if isinstance(data, list) else []

    def get_trip_details(
        self,
        start_date: str,
        end_date: str,
        *,
        resource_id: str,
    ) -> list[dict[str, Any]]:
        """Viajes uno por uno del recurso (el agregado por placa cierra con el
        último viaje aunque termine fuera de la ventana)."""
        return self.get_report(
            "trips",
            start_date,
            end_date,
            resource_id=resource_id,
            group_param=3,
            use_group=False,
        )

    def get_report_paged(
        self,
        report_type: str,
        start_date: str,
        end_date: str,
        *,
        resource_id: str | None = None,
        group_param: int = 1,
        use_group: bool | None = None,
    ) -> ArtimoReportPage:
        """Igual que `get_report`, pero parte la ventana cuando la respuesta
        llega al tope de filas: la API trunca en silencio.

        Acotado por `_MAX_SPLIT_DEPTH` y `_MAX_REQUESTS_PER_REPORT`; al
        excederlos lanza `ArtimoReportTooLarge` con lo recolectado.
        """
        budget = _PagingBudget()
        return self._get_report_paged(
            report_type,
            start_date,
            end_date,
            resource_id=resource_id,
            group_param=group_param,
            use_group=use_group,
            depth=0,
            budget=budget,
        )

    def _get_report_paged(
        self,
        report_type: str,
        start_date: str,
        end_date: str,
        *,
        resource_id: str | None,
        group_param: int,
        use_group: bool | None,
        depth: int,
        budget: _PagingBudget,
    ) -> ArtimoReportPage:
        if budget.requests_made >= _MAX_REQUESTS_PER_REPORT:
            self._raise_too_large(
                report_type,
                resource_id,
                budget,
                f"supero el presupuesto de {_MAX_REQUESTS_PER_REPORT} peticiones",
            )
        budget.requests_made += 1
        rows = self.get_report(
            report_type,
            start_date,
            end_date,
            resource_id=resource_id,
            group_param=group_param,
            use_group=use_group,
        )
        if len(rows) < _REPORT_PAGE_LIMIT:
            budget.collected.extend(rows)
            return ArtimoReportPage(rows=rows)

        start = _parse_api_instant(start_date)
        end = _parse_api_instant(end_date)
        if (end - start).total_seconds() <= _MIN_SPLIT_SECONDS:
            budget.collected.extend(rows)
            return ArtimoReportPage(rows=rows, truncated=True)

        if depth >= _MAX_SPLIT_DEPTH:
            budget.collected.extend(rows)
            self._raise_too_large(
                report_type,
                resource_id,
                budget,
                f"la ventana {start_date}..{end_date} sigue truncada tras {_MAX_SPLIT_DEPTH} niveles de split",
            )

        middle = start + (end - start) / 2
        first = self._get_report_paged(
            report_type,
            start_date,
            _format_api_instant(middle),
            resource_id=resource_id,
            group_param=group_param,
            use_group=use_group,
            depth=depth + 1,
            budget=budget,
        )
        second = self._get_report_paged(
            report_type,
            _format_api_instant(middle + timedelta(milliseconds=1)),
            end_date,
            resource_id=resource_id,
            group_param=group_param,
            use_group=use_group,
            depth=depth + 1,
            budget=budget,
        )
        return ArtimoReportPage(
            rows=_dedupe_rows(first.rows + second.rows),
            truncated=first.truncated or second.truncated,
        )

    @staticmethod
    def _raise_too_large(
        report_type: str,
        resource_id: str | None,
        budget: _PagingBudget,
        reason: str,
    ) -> None:
        message = (
            f"Reporte Artimo '{report_type}' demasiado grande para el recurso "
            f"{resource_id or '(grupo)'}: {reason} "
            f"({budget.requests_made} peticiones, {len(budget.collected)} filas recolectadas)."
        )
        logger.warning(message)
        raise ArtimoReportTooLarge(
            message,
            rows=_dedupe_rows(budget.collected),
            requests_made=budget.requests_made,
        )

    def get_month_range(self, year: int, month: int) -> tuple[str, str]:
        """Mes calendario completo en hora local del cliente.

        Artimo filtra por el instante UTC que se envía pero devuelve fechas
        locales, así que `month_start_hour_utc` es el desfase de la medianoche
        local (5 = UTC-5, Colombia). El fin es el arranque del mes siguiente
        menos 1 ms: el último día entra completo. `month_end_hour_utc` quedó
        obsoleto — recortaba el mes al mediodía del último día.
        """
        start = datetime(year, month, 1, self.config.month_start_hour_utc, 0, 0, tzinfo=timezone.utc)
        if month == 12:
            next_start = datetime(year + 1, 1, 1, self.config.month_start_hour_utc, 0, 0, tzinfo=timezone.utc)
        else:
            next_start = datetime(year, month + 1, 1, self.config.month_start_hour_utc, 0, 0, tzinfo=timezone.utc)
        end = next_start - timedelta(milliseconds=1)
        return (start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), end.strftime("%Y-%m-%dT%H:%M:%S.999Z"))

    def get_local_month_bounds(self, year: int, month: int) -> tuple[datetime, datetime]:
        """Inicio y fin del mes en hora local, para comparar contra `enddate` /
        `date` de los reportes (que vienen locales y sin zona)."""
        start_utc, end_utc = self.get_month_range(year, month)
        offset = timedelta(hours=self.config.month_start_hour_utc)
        start_local = datetime.strptime(start_utc, "%Y-%m-%dT%H:%M:%S.%fZ") - offset
        end_local = datetime.strptime(end_utc, "%Y-%m-%dT%H:%M:%S.%fZ") - offset
        return start_local, end_local

    def get_local_month_end(self, year: int, month: int) -> datetime:
        return self.get_local_month_bounds(year, month)[1]

    def get_trip_lookback_range(self, year: int, month: int, *, days: int = 2) -> tuple[str, str]:
        """Ventana ampliada hacia atrás: recoge los viajes que arrancaron el mes
        anterior y terminaron dentro de este."""
        start_utc, end_utc = self.get_month_range(year, month)
        widened = _parse_api_instant(start_utc) - timedelta(days=days)
        return _format_api_instant(widened), end_utc
