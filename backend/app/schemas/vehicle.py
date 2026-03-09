from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RegisteredMotorSummary(BaseModel):
    id: int = Field(..., description="ID del motor registrado")
    technical_number: str = Field(..., description="Technical Engine Configuration #")
    engine_name: str = Field(..., description="Nombre del motor")


class VehicleLookupResponse(BaseModel):
    plate: str = Field(..., description="Placa consultada")
    vin: str | None = Field(default=None, description="VIN obtenido desde Geotab")
    engine_number: str | None = Field(
        default=None, description="Numero de motor (ESN) obtenido desde inventario"
    )
    technical_engine_configuration: str | None = Field(
        default=None,
        description="Technical Engine Configuration # obtenido desde QuickServe",
    )
    registered_motor: RegisteredMotorSummary | None = Field(
        default=None,
        description="Motor registrado asociado al Technical Engine Configuration #",
    )
    status: str = Field(..., description="ok | partial | not_found | error")
    message: str = Field(..., description="Descripcion del resultado")


class MotorCatalogUpsertRequest(BaseModel):
    technical_number: str = Field(..., min_length=1, description="Numero tecnico de motor")
    engine_name: str = Field(..., min_length=1, description="Nombre del motor")


class MotorCatalogRecord(BaseModel):
    id: int = Field(..., description="ID del registro")
    technical_number: str = Field(..., description="Numero tecnico de motor")
    engine_name: str = Field(..., description="Nombre del motor")
    vehicle_count: int = Field(default=0, description="Cantidad de vehiculos asociados")
    last_seen_at: datetime | None = Field(
        default=None, description="Ultima vez que se consulto un vehiculo con este motor"
    )
    created_at: datetime = Field(..., description="Fecha de creacion")
    updated_at: datetime = Field(..., description="Fecha de actualizacion")
