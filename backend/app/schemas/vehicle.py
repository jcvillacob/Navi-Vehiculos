from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RecentMotorRecord(BaseModel):
    id: int
    engine_name: str
    technical_number: str
    created_at: datetime


class RecentVehicleRecord(BaseModel):
    plate: str
    client_name: str | None = None
    engine_name: str | None = None
    updated_at: datetime


class DashboardSummary(BaseModel):
    motors_count: int = 0
    vehicles_count: int = 0
    vehicles_without_motor: int = 0
    customers_count: int = 0
    databases_count: int = 0
    recent_motors: list[RecentMotorRecord] = Field(default_factory=list)
    recent_vehicles: list[RecentVehicleRecord] = Field(default_factory=list)


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
    geotab_status: str = Field(default="unknown", description="Geotab global: found | not_found | unknown")
    geotab_customer_status: str = Field(
        default="not_applicable",
        description="Geotab cliente: found | not_found | unknown | not_applicable",
    )
    engine_number: str | None = Field(
        default=None, description="Numero de motor (ESN) obtenido desde inventario"
    )
    technical_engine_configuration: str | None = Field(
        default=None,
        description="Technical Engine Configuration # obtenido desde QuickServe",
    )
    cpl: str | None = Field(default=None, description="N.o CPL obtenido desde QuickServe")
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


class MotorUpdateRequest(BaseModel):
    engine_name: str = Field(..., min_length=1, description="Nuevo nombre del motor")


class MotorAttachmentRecord(BaseModel):
    id: int = Field(..., description="ID del adjunto")
    motor_id: int = Field(..., description="ID del motor asociado")
    cpl: str | None = Field(default=None, description="CPL asociado al adjunto")
    original_filename: str = Field(..., description="Nombre original del archivo")
    content_type: str = Field(..., description="Tipo MIME del archivo")
    file_size: int = Field(..., description="Tamano del archivo en bytes")
    download_url: str = Field(..., description="Ruta para descargar o abrir el archivo")
    created_at: datetime = Field(..., description="Fecha de carga")
    updated_at: datetime = Field(..., description="Fecha de ultima actualizacion")


class MotorCatalogRecord(BaseModel):
    id: int = Field(..., description="ID del registro")
    technical_number: str = Field(..., description="Numero tecnico de motor")
    engine_name: str = Field(..., description="Nombre del motor")
    vehicle_count: int = Field(default=0, description="Cantidad de vehiculos asociados")
    last_seen_at: datetime | None = Field(
        default=None, description="Ultima vez que se consulto un vehiculo con este motor"
    )
    attachments: list[MotorAttachmentRecord] = Field(
        default_factory=list, description="Adjuntos asociados al motor"
    )
    available_cpls: list[str] = Field(
        default_factory=list, description="CPLs conocidos para ese motor"
    )
    created_at: datetime = Field(..., description="Fecha de creacion")
    updated_at: datetime = Field(..., description="Fecha de actualizacion")


class VehicleAssignmentRecord(BaseModel):
    plate: str = Field(..., description="Placa del vehiculo")
    customer_id: int | None = Field(default=None, description="ID local del cliente")
    customer_database_id: int | None = Field(default=None, description="ID local de la database")
    vin: str | None = Field(default=None, description="VIN asociado")
    geotab_status: str = Field(default="unknown", description="Estado en Geotab global Navitrans")
    geotab_customer_status: str = Field(
        default="not_applicable",
        description="Estado en Geotab del cliente: found | not_found | unknown | not_applicable",
    )
    geotab_customer_database_id: int | None = Field(
        default=None, description="ID de la database Geotab del cliente usada para validar"
    )
    engine_number: str | None = Field(default=None, description="Numero de motor")
    technical_number: str = Field(..., description="Numero tecnico de motor")
    cpl: str | None = Field(default=None, description="CPL del motor consultado")
    engine_name: str | None = Field(default=None, description="Nombre visible del motor")
    client_name: str | None = Field(default=None, description="Cliente asociado al vehiculo")
    database_name: str | None = Field(default=None, description="Database asociada al vehiculo")
    database_username: str | None = Field(
        default=None, description="Usuario de la database asociada al vehiculo"
    )
    database_connection_type: str | None = Field(
        default=None, description="Tipo de conexion de la database asignada"
    )
    has_database_password: bool = Field(
        default=False, description="Indica si existe contrasena almacenada"
    )
    access_url: str | None = Field(default=None, description="Enlace de acceso externo")
    has_motor_rules: bool = Field(
        default=False,
        description="Indica si el motor del vehiculo tiene reglas Geotab configuradas en su database (o databases con mismas credenciales)",
    )
    attachments: list[MotorAttachmentRecord] = Field(
        default_factory=list, description="Adjuntos del motor asociado"
    )
    created_at: datetime = Field(..., description="Fecha de primer registro")
    updated_at: datetime = Field(..., description="Fecha de ultima actualizacion")
    last_seen_at: datetime = Field(..., description="Fecha de ultima consulta exitosa")


class VehicleDatabaseAssignmentRequest(BaseModel):
    customer_database_id: int | None = Field(default=None, description="ID de la database seleccionada")
    access_url: str | None = Field(default=None, description="Enlace de acceso externo (para databases no-Geotab)")


class CustomerDatabaseCreateRequest(BaseModel):
    database_name: str = Field(..., min_length=1, description="Nombre de la database")
    username: str = Field(..., min_length=1, description="Usuario de la database")
    password: str = Field(..., min_length=1, description="Contrasena no hasheada de la database")
    connection_type: str = Field(
        default="database",
        description="Tipo de conexion: 'database', 'geotab' o 'artimo'",
    )
    access_url: str | None = Field(
        default=None, description="Enlace de acceso externo (solo para databases no-Geotab)"
    )
    provider_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Configuracion especifica del proveedor, por ejemplo Artimo",
    )


class CustomerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Nombre del cliente")


class CustomerUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Nuevo nombre del cliente")


class CustomerDatabaseUpdateRequest(BaseModel):
    database_name: str = Field(..., min_length=1, description="Nombre de la database")
    username: str = Field(..., min_length=1, description="Usuario de la database")
    password: str | None = Field(
        default=None,
        description="Nueva contrasena (None = no cambiar)",
    )
    connection_type: str = Field(
        default="database",
        description="Tipo de conexion: 'database', 'geotab' o 'artimo'",
    )
    access_url: str | None = Field(
        default=None, description="Enlace de acceso externo (solo para databases no-Geotab)"
    )
    provider_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Configuracion especifica del proveedor, por ejemplo Artimo",
    )


class GeotabRuleCreateRequest(BaseModel):
    rule_id: str = Field(..., min_length=1, description="ID alfanumerico de la regla en Geotab")


class GeotabRuleRecord(BaseModel):
    id: int = Field(..., description="ID del registro")
    database_id: int = Field(..., description="ID de la database Geotab asociada")
    name: str = Field(..., description="Nombre descriptivo de la regla")
    rule_id: str = Field(..., description="ID alfanumerico de la regla en Geotab")
    created_at: datetime = Field(..., description="Fecha de creacion")


class GeotabRuleGroupRuleRecord(BaseModel):
    rule_record_id: int = Field(..., description="ID del registro local de la regla")
    name: str = Field(..., description="Nombre visible de la regla")
    rule_id: str = Field(..., description="ID alfanumerico de la regla en Geotab")


class GeotabRuleGroupCreateRequest(BaseModel):
    motor_id: int = Field(..., gt=0, description="ID del motor asociado al grupo")
    name: str | None = Field(default=None, description="Nombre opcional del grupo")
    match_mode: str = Field(
        default="all",
        description="Modo de coincidencia: 'all' para todas o 'any' para cualquiera",
    )
    rule_record_ids: list[int] = Field(
        default_factory=list,
        description="IDs de reglas locales incluidas en el grupo",
    )


class GeotabRuleGroupRecord(BaseModel):
    id: int = Field(..., description="ID del grupo")
    database_id: int = Field(..., description="ID de la database Geotab asociada")
    motor_id: int = Field(..., description="ID del motor asociado")
    motor_name: str = Field(..., description="Nombre visible del motor")
    technical_number: str = Field(..., description="Technical Engine Configuration # del motor")
    name: str = Field(..., description="Nombre visible del grupo")
    match_mode: str = Field(..., description="Modo de coincidencia: all | any")
    rules: list[GeotabRuleGroupRuleRecord] = Field(
        default_factory=list,
        description="Reglas incluidas en el grupo",
    )
    created_at: datetime = Field(..., description="Fecha de creacion")
    updated_at: datetime = Field(..., description="Fecha de ultima actualizacion")


class GeotabRuleConditionNode(BaseModel):
    kind: str = Field(..., description="Tipo de nodo visual: group | comparison | duration | leaf")
    label: str = Field(..., description="Texto legible del nodo")
    children: list["GeotabRuleConditionNode"] = Field(
        default_factory=list,
        description="Nodos hijos cuando aplique",
    )


class GeotabRuleInspection(BaseModel):
    exists: bool = Field(..., description="Indica si la regla existe en Geotab al momento de consultar")
    rule_id: str = Field(..., description="ID alfanumerico de la regla en Geotab")
    name: str | None = Field(default=None, description="Nombre resuelto o snapshot guardado")
    status: str = Field(default="Inexistente", description="Activa | Archivada/Desactivada | Inexistente")
    type: str | None = Field(default=None, description="Predefinida | Personalizada")
    groups_count: int = Field(default=0, description="Cantidad de grupos asociados")
    comment: str | None = Field(default=None, description="Comentario visible de la regla")
    headline: str = Field(default="", description="Resumen humano corto de la condicion")
    facts: list[str] = Field(default_factory=list, description="Hechos/chips clave de la regla")
    tree: GeotabRuleConditionNode | None = Field(
        default=None,
        description="Arbol tecnico legible de la condicion",
    )
    raw_condition: Any = Field(default=None, description="Condicion cruda devuelta por Geotab")
    message: str | None = Field(default=None, description="Mensaje auxiliar para errores o fallback")


class CustomerDatabaseRecord(BaseModel):
    id: int = Field(..., description="ID de la database del cliente")
    customer_id: int = Field(..., description="ID del cliente")
    database_name: str = Field(..., description="Nombre de la database")
    username: str = Field(..., description="Usuario configurado")
    has_password: bool = Field(..., description="Indica si tiene contrasena almacenada")
    connection_type: str = Field(
        default="database",
        description="Tipo de conexion: 'database', 'geotab' o 'artimo'",
    )
    access_url: str | None = Field(
        default=None, description="Enlace de acceso externo (solo para databases no-Geotab)"
    )
    provider_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Configuracion publica del proveedor, sin secretos",
    )
    rules: list[GeotabRuleRecord] = Field(
        default_factory=list, description="Reglas Geotab asociadas (solo para tipo geotab)"
    )
    rule_groups: list[GeotabRuleGroupRecord] = Field(
        default_factory=list,
        description="Grupos de reglas asociados a motores (solo para tipo geotab)",
    )
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


class MonthlyPerformanceCalculateRequest(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$", description="Mes a calcular en formato YYYY-MM")
    customer_id: int | None = Field(default=None, gt=0, description="Filtro opcional por cliente")
    customer_ids: list[int] = Field(
        default_factory=list,
        description="Lista opcional de clientes a calcular; vacio = todos los clientes Artimo elegibles",
    )
    customer_database_id: int | None = Field(
        default=None, gt=0, description="Filtro opcional por database del cliente"
    )
    force_recalculate: bool = Field(
        default=False,
        description="Si es true, recalcula aunque ya exista un corte mensual persistido",
    )


class MonthlyPerformanceRecord(BaseModel):
    customer_id: int | None = Field(default=None, description="ID local del cliente")
    customer_database_id: int = Field(..., description="ID local de la database")
    client_name: str | None = Field(default=None, description="Nombre del cliente")
    database_name: str | None = Field(default=None, description="Nombre de la database")
    source_provider: str = Field(..., description="Proveedor origen del calculo")
    plate: str = Field(..., description="Placa del vehiculo")
    provider_vehicle_id: str | None = Field(
        default=None,
        description="ID externo del vehiculo en el proveedor GPS",
    )
    technical_number: str | None = Field(default=None, description="TEC# del motor")
    engine_name: str | None = Field(default=None, description="Nombre del motor")
    period_month: str = Field(..., description="Mes del corte en formato YYYY-MM")
    odo_start: float | None = Field(default=None, description="Odometro inicial")
    odo_end: float | None = Field(default=None, description="Odometro final")
    horo_start: float | None = Field(default=None, description="Horometro inicial")
    horo_end: float | None = Field(default=None, description="Horometro final")
    kms_ecm: float | None = Field(default=None, description="Kilometros ECM")
    kms_gps: float | None = Field(default=None, description="Kilometros GPS")
    hours_ecm: float | None = Field(default=None, description="Horas ECM")
    hours_gps: float | None = Field(default=None, description="Horas GPS")
    fuel_gallons: float | None = Field(default=None, description="Combustible consumido en galones")
    calculation_status: str = Field(
        ...,
        description="Estado del calculo: calculated | partial | unbound | no_data | error",
    )
    warnings: list[str] = Field(default_factory=list, description="Advertencias del calculo")
    calculated_at: datetime | None = Field(default=None, description="Fecha del ultimo calculo")


class MonthlyPerformanceSummary(BaseModel):
    total: int = Field(default=0, description="Total de placas retornadas")
    calculated: int = Field(default=0, description="Cantidad de placas calculadas")
    partial: int = Field(default=0, description="Cantidad de placas parciales")
    unbound: int = Field(default=0, description="Cantidad de placas sin binding")
    no_data: int = Field(default=0, description="Cantidad de placas sin datos")
    error: int = Field(default=0, description="Cantidad de placas con error")


class MonthlyPerformanceResponse(BaseModel):
    month: str = Field(..., description="Mes consultado o calculado en formato YYYY-MM")
    month_from: str | None = Field(default=None, description="Inicio del rango cuando es consulta por rango")
    month_to: str | None = Field(default=None, description="Fin del rango cuando es consulta por rango")
    summary: MonthlyPerformanceSummary = Field(
        default_factory=MonthlyPerformanceSummary,
        description="Resumen del lote retornado",
    )
    rows: list[MonthlyPerformanceRecord] = Field(
        default_factory=list,
        description="Cortes mensuales por placa",
    )


GeotabRuleConditionNode.model_rebuild()
