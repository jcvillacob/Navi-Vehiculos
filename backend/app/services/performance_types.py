from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.vehicle import MonthlyPerformanceRecord


@dataclass(frozen=True)
class PerformanceTarget:
    provider_key: str
    customer_id: int | None
    customer_database_id: int
    client_name: str | None
    database_name: str | None
    plate: str
    technical_number: str | None
    engine_name: str | None
    username: str
    password: str
    provider_config: dict[str, Any]


@dataclass(frozen=True)
class BindingSnapshot:
    provider_vehicle_id: str | None
    binding_status: str
    is_manual: bool = False


@dataclass(frozen=True)
class BindingUpsert:
    target: PerformanceTarget
    provider_vehicle_id: str | None
    binding_status: str
    last_error: str | None


@dataclass(frozen=True)
class ProviderCalculationResult:
    records: list[MonthlyPerformanceRecord]
    binding_updates: list[BindingUpsert]
