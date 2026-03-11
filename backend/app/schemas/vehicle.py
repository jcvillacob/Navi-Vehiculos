from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RegisteredMotorSummary(BaseModel):
    id: int = Field(..., description="ID del motor registrado")
    technical_number: str = Field(..., description="Technical Engine Configuration #")
    engine_name: str = Field(..., description="Nombre del motor")


class AssignedDatabaseSummary(BaseModel):
    client_name: str | None = Field(default=None, description="Cliente asociado al vehiculo")
    database_name: str | None = Field(default=None, description="Nombre de la database asociada")
    database_username: str | None = Field(
        default=None, description="Usuario de la database asociada"
    )
    has_database_password: bool = Field(
        default=False,
        description="Indica si la database asociada tiene una contrasena almacenada",
    )


class VehicleSourceDetails(BaseModel):
    fenix: dict[str, str | None] = Field(default_factory=dict, description="Datos desde SQL/Fenix")
    cummins: dict[str, str] = Field(default_factory=dict, description="Dataplate desde Cummins")


class VehicleLookupResponse(BaseModel):
    plate: str | None = Field(default=None, description="Placa consultada")
    lookup_value: str = Field(..., description="Valor usado para la consulta")
    lookup_type: str = Field(..., description="plate | vin")
    vin: str | None = Field(default=None, description="VIN obtenido desde Geotab")
    geotab_status: str = Field(default="unknown", description="found | not_found | unknown")
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
    assigned_database: AssignedDatabaseSummary = Field(
        default_factory=AssignedDatabaseSummary,
        description="Cliente y database asociados al vehiculo",
    )
    source_details: VehicleSourceDetails = Field(
        default_factory=VehicleSourceDetails,
        description="Detalle ampliado por fuente",
    )
    warnings: list[str] = Field(default_factory=list, description="Advertencias no bloqueantes")
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


class VehicleAssignmentRecord(BaseModel):
    plate: str = Field(..., description="Placa del vehiculo")
    vin: str | None = Field(default=None, description="VIN asociado")
    engine_number: str | None = Field(default=None, description="Numero de motor")
    technical_number: str = Field(..., description="Numero tecnico de motor")
    engine_name: str | None = Field(default=None, description="Nombre visible del motor")
    client_name: str | None = Field(default=None, description="Cliente asociado al vehiculo")
    database_name: str | None = Field(default=None, description="Database asociada al vehiculo")
    database_username: str | None = Field(
        default=None, description="Usuario de la database asociada al vehiculo"
    )
    has_database_password: bool = Field(
        default=False, description="Indica si existe contrasena almacenada"
    )
    created_at: datetime = Field(..., description="Fecha de primer registro")
    updated_at: datetime = Field(..., description="Fecha de ultima actualizacion")
    last_seen_at: datetime = Field(..., description="Fecha de ultima consulta exitosa")


class VehicleDatabaseAssignmentRequest(BaseModel):
    customer_database_id: int = Field(..., description="ID de la database seleccionada")


class CustomerDatabaseCreateRequest(BaseModel):
    database_name: str = Field(..., min_length=1, description="Nombre de la database")
    username: str = Field(..., min_length=1, description="Usuario de la database")
    password: str = Field(..., min_length=1, description="Contrasena no hasheada de la database")


class CustomerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Nombre del cliente")


class CustomerDatabaseRecord(BaseModel):
    id: int = Field(..., description="ID de la database del cliente")
    customer_id: int = Field(..., description="ID del cliente")
    database_name: str = Field(..., description="Nombre de la database")
    username: str = Field(..., description="Usuario configurado")
    has_password: bool = Field(..., description="Indica si tiene contrasena almacenada")
    created_at: datetime = Field(..., description="Fecha de creacion")
    updated_at: datetime = Field(..., description="Fecha de actualizacion")


class CustomerRecord(BaseModel):
    id: int = Field(..., description="ID del cliente")
    name: str = Field(..., description="Nombre del cliente")
    database_count: int = Field(default=0, description="Cantidad de databases asociadas")
    databases: list[CustomerDatabaseRecord] = Field(
        default_factory=list, description="Databases configuradas para el cliente"
    )
    created_at: datetime = Field(..., description="Fecha de creacion")
    updated_at: datetime = Field(..., description="Fecha de actualizacion")
