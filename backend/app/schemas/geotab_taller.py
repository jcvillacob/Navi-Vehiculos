from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


EventKind = Literal["enter", "exit", "unknown"]
TallerStatus = Literal["in", "grace"]


class GeotabTallerEventInfo(BaseModel):
    """Sub-payload event_info del webhook de Geotab (ya limpio)."""

    exception_id: str | None = None
    rule_triggered: str = Field(..., description="Nombre de la regla disparada (limpio)")
    date: str = Field(..., description="Fecha en formato 'Jun, 24, 2026'")
    time: str = Field(..., description="Hora en formato '4:27:21 PM'")
    timezone: str = "UTC"
    event_ts_utc: datetime = Field(..., description="Timestamp parseado en UTC (tz-aware)")


class GeotabTallerAssetInfo(BaseModel):
    """Sub-payload asset_info (ya limpio)."""

    device_id: str | None = None
    device_name: str | None = None
    vin: str | None = None


class GeotabTallerTelemetryInfo(BaseModel):
    """Sub-payload telemetry_info (ya limpio)."""

    zone_id: str | None = None
    zone_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    odometer: str | None = None


class CleanGeotabTallerPayload(BaseModel):
    """Payload completo de Geotab ya normalizado."""

    event: GeotabTallerEventInfo
    asset: GeotabTallerAssetInfo
    telemetry: GeotabTallerTelemetryInfo
    event_kind: EventKind = Field(..., description="enter | exit | unknown")
    identifier: str = Field(..., description="device_name o vin normalizado para resolver el vehículo")


class TallerZone(BaseModel):
    id: str
    name: str
    lat: float
    lng: float


class TallerVehicleSnapshot(BaseModel):
    plate: str
    lat: float
    lng: float
    zone_id: str | None
    zone_name: str | None
    category: str
    client_name: str | None
    motor: str | None
    enter_ts_local: datetime
    minutes_inside: int
    odometer: str | None = None


class TallerExitedVehicle(BaseModel):
    """Vehiculo que ya salio del taller (marcador atenuado en el mapa)."""

    plate: str
    lat: float
    lng: float
    zone_id: str | None
    zone_name: str | None
    category: str
    client_name: str | None
    motor: str | None
    exit_ts_local: datetime
    minutes_ago: int


class MapaTallerResponse(BaseModel):
    generated_at: datetime
    vehicles: list[TallerVehicleSnapshot]
    exited: list[TallerExitedVehicle] = Field(default_factory=list)
    zones: list[TallerZone]
    etag: str = Field(..., description="Hash debil del snapshot; usarlo como ETag en el cliente")


class TallerHistoryVisit(BaseModel):
    plate: str
    zone_id: str | None = None
    zone_name: str | None = None
    client_name: str | None = None
    motor: str | None = None
    enter_ts_local: datetime | None = None
    exit_ts_local: datetime | None = None
    minutes_inside: int | None = None


class TallerHistoryResponse(BaseModel):
    days: int
    count: int
    visits: list[TallerHistoryVisit]


class WebhookResult(BaseModel):
    status: Literal["ok", "error"]
    event_kind: EventKind | None = None
    plate: str | None = None
    ignored: bool = False
    reason: str | None = None
    message: str | None = None


class CleanedPayloadDebug(BaseModel):
    """Solo para tests: payload crudo + el resultado de la limpieza."""

    raw: dict[str, Any]
    cleaned: dict[str, Any]


class ManualTallerAction(BaseModel):
    plate: str = Field(..., min_length=1, max_length=10, description="Placa del vehiculo")
    action: Literal["add", "hide", "unhide", "close"] = Field(
        ..., description="Acción manual a ejecutar"
    )
    enter_ts: str | None = Field(
        default=None,
        description="ISO-8601 opcional para 'add'. Si se omite, usa now() UTC.",
    )
