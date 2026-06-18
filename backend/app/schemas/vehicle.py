from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# Categorias de cliente/vehiculo. "Ninguna" es el valor por defecto/neutro.
CustomerCategory = Literal["Ninguna", "Experiencia Superior", "Flota Administrada"]
CUSTOMER_CATEGORIES: tuple[str, ...] = (
    "Ninguna",
    "Experiencia Superior",
    "Flota Administrada",
)


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
    marca: str | None = Field(default=None, description="Marca del vehiculo (Fenix)")
    linea: str | None = Field(default=None, description="Linea del vehiculo (Fenix)")
    ano_modelo: str | None = Field(default=None, description="Año modelo del vehiculo (Fenix)")
    tipo_combustible: str | None = Field(default=None, description="Tipo de combustible del vehiculo (Fenix)")
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
    cached: bool = Field(default=False, description="True si los datos provienen del cache local")


class MotorCatalogUpsertRequest(BaseModel):
    technical_number: str = Field(..., min_length=1, description="Numero tecnico de motor")
    engine_name: str = Field(..., min_length=1, description="Nombre del motor")


class MotorUpdateRequest(BaseModel):
    engine_name: str = Field(..., min_length=1, description="Nuevo nombre del motor")
    technical_number: str | None = Field(default=None, min_length=1, description="Nuevo numero tecnico (opcional)")


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
    geotab_device_id: str | None = Field(
        default=None,
        description="Codigo interno del device en la db Geotab del cliente (geotab_customer_database_id)",
    )
    geotab_device_synced_at: datetime | None = Field(
        default=None, description="Ultima vez que se sincronizo el geotab_device_id"
    )
    engine_number: str | None = Field(default=None, description="Numero de motor")
    technical_number: str = Field(..., description="Numero tecnico de motor")
    cpl: str | None = Field(default=None, description="CPL del motor consultado")
    marca: str | None = Field(default=None, description="Marca del vehiculo (Fenix)")
    linea: str | None = Field(default=None, description="Linea del vehiculo (Fenix)")
    ano_modelo: str | None = Field(default=None, description="Año modelo del vehiculo (Fenix)")
    tipo_combustible: str | None = Field(default=None, description="Tipo de combustible (Fenix)")
    nombre_vehiculo: str | None = Field(default=None, description="Nombre del vehiculo (Fenix)")
    engine_name: str | None = Field(default=None, description="Nombre visible del motor")
    client_name: str | None = Field(default=None, description="Cliente asociado al vehiculo")
    category: CustomerCategory = Field(
        default="Ninguna",
        description="Categoria efectiva del vehiculo (override propio o heredada del cliente)",
    )
    category_is_inherited: bool = Field(
        default=True,
        description="True si la categoria se hereda del cliente (sin override propio en el vehiculo)",
    )
    customer_category: CustomerCategory = Field(
        default="Ninguna",
        description="Categoria del cliente del vehiculo, usada como valor heredado por defecto",
    )
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
    provider_vehicle_id: str | None = Field(
        default=None,
        description="ID externo manual del vehiculo para el proveedor GPS de la database asignada (si existe)",
    )
    is_provider_vehicle_id_manual: bool = Field(
        default=False,
        description="True si el provider_vehicle_id viene de un binding marcado como manual",
    )
    created_at: datetime = Field(..., description="Fecha de primer registro")
    updated_at: datetime = Field(..., description="Fecha de ultima actualizacion")
    last_seen_at: datetime = Field(..., description="Fecha de ultima consulta exitosa")


class VehicleDatabaseAssignmentRequest(BaseModel):
    customer_database_id: int | None = Field(default=None, description="ID de la database seleccionada")
    access_url: str | None = Field(default=None, description="Enlace de acceso externo (para databases no-Geotab)")
    provider_vehicle_id: str | None = Field(
        default=None,
        max_length=128,
        description="ID externo del vehiculo para el proveedor GPS de la database asignada. None = no modificar el binding manual; string vacio = borrar binding manual; string no vacio = guardar binding manual.",
    )


class VehicleCategoryUpdateRequest(BaseModel):
    category: CustomerCategory | None = Field(
        default=None,
        description=(
            "Categoria override del vehiculo. None = heredar la del cliente; "
            "Ninguna | Experiencia Superior | Flota Administrada = fijar override propio."
        ),
    )


class ManualVehicleAssignmentRequest(BaseModel):
    technical_number: str = Field(..., min_length=1, description="Technical Engine Configuration #")
    cpl: str | None = Field(default=None, description="CPL del motor")
    vin: str | None = Field(default=None, description="VIN del vehiculo")
    engine_number: str | None = Field(default=None, description="Numero de motor (ESN)")
    marca: str | None = Field(default=None, description="Marca del vehiculo")
    linea: str | None = Field(default=None, description="Linea del vehiculo")
    ano_modelo: str | None = Field(default=None, description="Año modelo del vehiculo")
    tipo_combustible: str | None = Field(default=None, description="Tipo de combustible")
    nombre_vehiculo: str | None = Field(default=None, description="Nombre del vehiculo")
    geotab_status: str = Field(default="unknown", description="Estado Geotab Navitrans")


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
    category: CustomerCategory = Field(
        default="Ninguna",
        description="Categoria del cliente: Ninguna | Experiencia Superior | Flota Administrada",
    )


class CustomerUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Nuevo nombre del cliente")
    category: CustomerCategory = Field(
        default="Ninguna",
        description="Categoria del cliente: Ninguna | Experiencia Superior | Flota Administrada",
    )


class CustomerActiveUpdateRequest(BaseModel):
    is_active: bool = Field(
        ..., description="True para reactivar el cliente, False para archivarlo (inactivo)."
    )


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


class CustomerDatabaseCredentialCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, description="Usuario de la credencial")
    password: str = Field(..., min_length=1, description="Contrasena de la credencial")
    label: str | None = Field(default=None, description="Etiqueta descriptiva (ej. 'cuenta reportes')")
    is_active: bool = Field(default=True, description="Si la credencial participa en la rotacion")


class CustomerDatabaseCredentialUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=1, description="Nuevo usuario (None = no cambiar)")
    password: str | None = Field(default=None, min_length=1, description="Nueva contrasena (None = no cambiar)")
    label: str | None = Field(default=None, description="Nueva etiqueta (None = no cambiar)")
    is_active: bool | None = Field(default=None, description="Activar/desactivar (None = no cambiar)")


class CustomerDatabaseCredentialRecord(BaseModel):
    id: int = Field(..., description="ID de la credencial")
    customer_database_id: int = Field(..., description="ID de la database asociada")
    username: str = Field(..., description="Usuario de la credencial")
    label: str | None = Field(default=None, description="Etiqueta descriptiva")
    is_active: bool = Field(default=True, description="Si participa en la rotacion")
    last_used_at: datetime | None = Field(default=None, description="Ultimo uso en la rotacion")
    last_auth_error_at: datetime | None = Field(
        default=None, description="Ultimo fallo de autenticacion registrado"
    )
    created_at: datetime = Field(..., description="Fecha de creacion")
    updated_at: datetime = Field(..., description="Fecha de actualizacion")


class GeotabRuleCreateRequest(BaseModel):
    rule_id: str = Field(..., min_length=1, description="ID alfanumerico de la regla en Geotab")
    category: str = Field(
        default="operacion",
        description="Categoria de la regla: 'operacion' (motor) o 'habito_seguro'",
    )


class GeotabRuleRecord(BaseModel):
    id: int = Field(..., description="ID del registro")
    database_id: int = Field(..., description="ID de la database Geotab asociada")
    name: str = Field(..., description="Nombre descriptivo de la regla")
    rule_id: str = Field(..., description="ID alfanumerico de la regla en Geotab")
    category: str = Field(
        default="operacion",
        description="Categoria de la regla: 'operacion' (motor) o 'habito_seguro'",
    )
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
    category: CustomerCategory = Field(
        default="Ninguna",
        description="Categoria del cliente: Ninguna | Experiencia Superior | Flota Administrada",
    )
    is_active: bool = Field(
        default=True,
        description="Estado del cliente. False = archivado (inactivo), conserva su histórico.",
    )
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
    compute_availability: bool = Field(
        default=False,
        description="Si es true, despues de los rendimientos calcula tambien la disponibilidad de proyectos desde CloudFleet.",
    )
    include_adhoc: bool = Field(
        default=False,
        description="Si true, incluye vehiculos sin database asignada usando credenciales Navitrans Geotab.",
    )
    adhoc_plates: list[str] = Field(
        default_factory=list,
        description="Placas especificas a calcular en modo ad-hoc (vacio = todas las que apliquen por filtros).",
    )
    adhoc_filters: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Filtros avanzados ad-hoc: {marca: [...], linea: [...], nombre_vehiculo: [...]}",
    )
    adhoc_only: bool = Field(
        default=False,
        description="Si true, calcula SOLO vehiculos ad-hoc sin incluir clientes asignados.",
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
    vin: str | None = Field(default=None, description="VIN del vehiculo")
    cpl: str | None = Field(default=None, description="CPL del vehiculo")
    marca: str | None = Field(default=None, description="Marca del vehiculo")
    linea: str | None = Field(default=None, description="Linea del vehiculo")
    ano_modelo: str | None = Field(default=None, description="Año modelo del vehiculo")
    tipo_combustible: str | None = Field(default=None, description="Tipo de combustible")
    nombre_vehiculo: str | None = Field(default=None, description="Nombre del vehiculo")
    is_adhoc: bool = Field(default=False, description="True si fue calculado con credenciales Navitrans (ad-hoc)")


class AvailabilitySummary(BaseModel):
    total: int = Field(default=0, description="Total de placas evaluadas en disponibilidad")
    calculated: int = Field(default=0, description="Placas con disponibilidad calculada")
    no_orders: int = Field(default=0, description="Placas sin ordenes en el mes (100% por default)")
    not_in_cloudfleet: int = Field(default=0, description="Placas locales que no aparecen en CloudFleet")
    error: int = Field(default=0, description="Placas con error puntual")


class MonthlyPerformanceSummary(BaseModel):
    total: int = Field(default=0, description="Total de placas retornadas")
    calculated: int = Field(default=0, description="Cantidad de placas calculadas")
    partial: int = Field(default=0, description="Cantidad de placas parciales")
    unbound: int = Field(default=0, description="Cantidad de placas sin binding")
    no_data: int = Field(default=0, description="Cantidad de placas sin datos")
    error: int = Field(default=0, description="Cantidad de placas con error")
    availability: AvailabilitySummary | None = Field(
        default=None,
        description="Resumen de disponibilidad cuando el job corrio con compute_availability=true.",
    )


class MonthlyVehicleAvailabilityRow(BaseModel):
    plate: str = Field(..., description="Placa local")
    period_month: str = Field(..., description="Mes en formato YYYY-MM")
    calculation_status: str = Field(
        ...,
        description="calculated | no_orders | not_in_cloudfleet | error",
    )
    project_availability_pct: float | None = Field(
        default=None, description="Porcentaje de disponibilidad de proyectos"
    )
    h_total: float | None = Field(default=None, description="Horas teoricas del mes (dias*24)")
    h_no_disp: float | None = Field(default=None, description="Horas no disponibles en el mes")
    orders_considered: int = Field(default=0, description="Cantidad de ordenes que afectaron disponibilidad")
    error_message: str | None = Field(default=None, description="Detalle del error si lo hubo")
    last_calculated_at: datetime = Field(..., description="Cuando se calculo por ultima vez")
    source: str = Field(default="cloudfleet", description="Fuente del calculo")


class MonthlyVehicleAvailabilityResponse(BaseModel):
    month_from: str = Field(..., description="Inicio del rango consultado")
    month_to: str = Field(..., description="Fin del rango consultado")
    rows: list[MonthlyVehicleAvailabilityRow] = Field(default_factory=list)


# ── Dashboard de Disponibilidad (agregaciones por flota/cliente) ──────────────


class AvailabilityFleet(BaseModel):
    customer_id: int | None = Field(default=None, description="Id del cliente (None = sin cliente)")
    customer_name: str = Field(..., description="Nombre del cliente/flota")
    vehicle_count: int = Field(default=0, description="Placas con dato en el mes")
    h_total: float = Field(default=0.0, description="Horas teoricas sumadas")
    h_no_disp: float = Field(default=0.0, description="Horas no disponibles sumadas")
    availability_pct: float | None = Field(default=None, description="Disponibilidad agregada de la flota")
    status: str = Field(default="no_data", description="good | warning | critical | no_data")
    status_breakdown: dict[str, int] = Field(default_factory=dict, description="Conteo por calculation_status")


class AvailabilityOverall(BaseModel):
    vehicle_count: int = Field(default=0)
    h_total: float = Field(default=0.0)
    h_no_disp: float = Field(default=0.0)
    availability_pct: float | None = Field(default=None)
    status: str = Field(default="no_data")
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    critical_fleets: int = Field(default=0, description="Flotas en estado critico")
    fleet_count: int = Field(default=0, description="Total de flotas con datos")


class AvailabilityOverviewResponse(BaseModel):
    month: str = Field(..., description="Mes consultado YYYY-MM")
    generated_at: str = Field(..., description="Timestamp de generacion")
    overall: AvailabilityOverall = Field(default_factory=AvailabilityOverall)
    fleets: list[AvailabilityFleet] = Field(default_factory=list)


class AvailabilityRankingItem(BaseModel):
    plate: str
    customer_id: int | None = Field(default=None)
    customer_name: str = Field(...)
    availability_pct: float = Field(...)
    h_no_disp: float = Field(default=0.0)
    h_total: float = Field(default=0.0)
    orders_considered: int = Field(default=0)
    status: str = Field(default="no_data")


class AvailabilityRankingResponse(BaseModel):
    month: str = Field(...)
    customer_id: int | None = Field(default=None)
    order: str = Field(default="worst")
    items: list[AvailabilityRankingItem] = Field(default_factory=list)


class AvailabilityTrendResponse(BaseModel):
    month_from: str = Field(...)
    month_to: str = Field(...)
    customer_id: int | None = Field(default=None)
    labels: list[str] = Field(default_factory=list)
    availability_pct: list[float | None] = Field(default_factory=list)


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


class PerformanceCalculationJob(BaseModel):
    id: int = Field(..., description="ID del job")
    status: str = Field(..., description="queued | running | done | error")
    month: str = Field(..., description="Mes calculado en formato YYYY-MM")
    customer_id: int | None = Field(default=None)
    customer_ids: list[int] = Field(default_factory=list)
    customer_database_id: int | None = Field(default=None)
    force_recalculate: bool = Field(default=True)
    compute_availability: bool = Field(
        default=False,
        description="Si es true, el job tambien corrio el calculo de disponibilidad CloudFleet.",
    )
    include_adhoc: bool = Field(
        default=False,
        description="Si true, el job incluye vehiculos ad-hoc (sin cliente, con Navitrans Geotab).",
    )
    adhoc_only: bool = Field(
        default=False,
        description="Si true, el job calcula SOLO vehiculos ad-hoc sin incluir clientes.",
    )
    total_targets: int = Field(default=0)
    processed_targets: int = Field(default=0)
    progress_pct: float = Field(default=0.0, description="Porcentaje 0-100 derivado de processed/total")
    summary: MonthlyPerformanceSummary | None = Field(default=None)
    error_message: str | None = Field(default=None)
    triggered_by: str = Field(default="ui", description="ui | cron | cli")
    created_by_user_id: int | None = Field(default=None)
    created_at: datetime = Field(...)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)


class PerformanceJobListResponse(BaseModel):
    jobs: list[PerformanceCalculationJob] = Field(default_factory=list)


GeotabRuleConditionNode.model_rebuild()


class BatchLookupRequest(BaseModel):
    identifiers: list[str] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Lista de placas o VINs a consultar",
    )
    force: bool = Field(
        default=False,
        description="Forzar consulta externa ignorando cache local",
    )
    scope: str = Field(
        default="all",
        pattern="^(all|fenix|cummins)$",
        description="Alcance del reprocesamiento: all (todo), fenix (solo datos Fenix/Geotab), cummins (solo datos Cummins)",
    )
    skip_geotab: bool = Field(
        default=False,
        description="Omitir consultas a Geotab (mas rapido)",
    )


