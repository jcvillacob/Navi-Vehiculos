from __future__ import annotations

from pydantic import BaseModel, Field


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
    status: str = Field(..., description="ok | partial | not_found | error")
    message: str = Field(..., description="Descripcion del resultado")
