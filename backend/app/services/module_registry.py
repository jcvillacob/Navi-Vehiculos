"""
Registro de módulos y mapeo módulo×nivel → codenames.

Es la **única fuente de verdad** que conecta la matriz Módulo × {Lectura, Escritura}
que ve el administrador, con los codenames granulares (`motors.create`, etc.)
que consumen los `require_permission(...)` en las rutas.

Mantener la coherencia: todo codename sembrado en `permissions` debe estar
alcanzable desde la matriz (lectura o escritura de algún módulo).
"""
from __future__ import annotations

from typing import Iterable


# Catálogo de módulos expuesto al frontend. El orden es el que verá el admin
# en la matriz. `levels` define los radios disponibles (sin "ninguno" — ése es
# el default si la entrada no está presente en la matriz).
MODULES: list[dict] = [
    {
        "key": "dashboard",
        "label": "Dashboard",
        "description": "Resumen general del sistema.",
        "levels": ["lectura"],
    },
    {
        "key": "consulta_motor",
        "label": "Consulta de motor",
        "description": "Consulta individual y por lote del motor por placa.",
        "levels": ["lectura"],
    },
    {
        "key": "rendimientos",
        "label": "Rendimientos",
        "description": "Kms, horas, consumo y disponibilidad mensual por vehículo.",
        "levels": ["lectura", "escritura"],
    },
    {
        "key": "vehiculos",
        "label": "Vehículos",
        "description": "Listado, edición y reproceso de placas asociadas.",
        "levels": ["lectura", "escritura"],
    },
    {
        "key": "motores",
        "label": "Motores",
        "description": "Catálogo técnico de motores y sus adjuntos.",
        "levels": ["lectura", "escritura"],
    },
    {
        "key": "clientes",
        "label": "Clientes y databases",
        "description": "Administración de clientes, databases y reglas Geotab.",
        "levels": ["lectura", "escritura"],
    },
    {
        "key": "usuarios",
        "label": "Usuarios",
        "description": "Gestión de cuentas, roles y sesiones de usuario.",
        "levels": ["lectura", "escritura"],
    },
    {
        "key": "roles",
        "label": "Roles y permisos",
        "description": "Crear, editar y asignar permisos a roles del sistema.",
        "levels": ["escritura"],
    },
    {
        "key": "auditoria",
        "label": "Auditoría",
        "description": "Consulta de logs y eventos del sistema.",
        "levels": ["lectura"],
    },
    {
        "key": "mapa",
        "label": "Mapa de taller",
        "description": "Mapa de vehículos en taller. Gestión manual de estado.",
        "levels": ["escritura"],
    },
]


# Mapeo (módulo, nivel) → codenames que ese nivel otorga.
# "escritura" siempre incluye los codenames de "lectura" del mismo módulo.
MODULE_CODENAMES: dict[tuple[str, str], tuple[str, ...]] = {
    ("dashboard", "lectura"): ("dashboard.view",),
    ("consulta_motor", "lectura"): (
        "engine_lookup.use",
        "engine_lookup.batch",
    ),
    ("rendimientos", "lectura"): ("rendimientos.view",),
    ("rendimientos", "escritura"): (
        "rendimientos.view",
        "rendimientos.refresh",
    ),
    ("vehiculos", "lectura"): ("vehicles.list",),
    ("vehiculos", "escritura"): (
        "vehicles.list",
        "vehicles.edit",
        "vehicles.refresh",
    ),
    ("motores", "lectura"): ("motors.list",),
    ("motores", "escritura"): (
        "motors.list",
        "motors.create",
        "motors.edit",
        "motors.delete",
        "motors.attachments",
    ),
    ("clientes", "lectura"): ("customers.list",),
    ("clientes", "escritura"): (
        "customers.list",
        "customers.create",
        "customers.edit",
    ),
    ("usuarios", "lectura"): ("users.list",),
    ("usuarios", "escritura"): (
        "users.list",
        "users.create",
        "users.edit",
    ),
    ("roles", "escritura"): ("roles.manage",),
    ("auditoria", "lectura"): ("audit.view",),
    ("mapa", "escritura"): ("mapa.taller.manage",),
}


# Descripciones legibles de los codenames. Sigue siendo útil para auditoría,
# depuración y mensajes de error.
PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "dashboard.view": "Ver dashboard",
    "motors.list": "Listar motores",
    "motors.create": "Crear motor",
    "motors.edit": "Editar motor",
    "motors.delete": "Eliminar motor",
    "motors.attachments": "Gestionar adjuntos",
    "vehicles.list": "Listar vehiculos",
    "vehicles.edit": "Editar vehiculo",
    "vehicles.refresh": "Refrescar datos Geotab",
    "customers.list": "Listar clientes",
    "customers.create": "Crear cliente",
    "customers.edit": "Editar cliente y databases",
    "rendimientos.view": "Ver rendimientos",
    "rendimientos.refresh": "Refrescar rendimientos",
    "users.list": "Listar usuarios",
    "users.create": "Crear usuario",
    "users.edit": "Editar usuario",
    "roles.manage": "Gestionar roles y permisos",
    "audit.view": "Ver auditoria",
    "engine_lookup.use": "Consultar motor por placa",
    "engine_lookup.batch": "Consultar motores en lote",
    "mapa.taller.manage": "Gestionar estado manual del mapa de taller",
}


# Codenames críticos que el rol admin nunca debe perder (anti-lockout).
ADMIN_PROTECTED_CODENAMES: frozenset[str] = frozenset(
    {"roles.manage", "users.list", "users.create", "users.edit"}
)


def modules_catalog() -> list[dict]:
    """Catálogo para el frontend. Devuelve copia ligera, sin map interna."""
    return [
        {
            "key": m["key"],
            "label": m["label"],
            "description": m["description"],
            "levels": list(m["levels"]),
        }
        for m in MODULES
    ]


def is_valid_module(module: str) -> bool:
    return any(m["key"] == module for m in MODULES)


def is_valid_level(module: str, level: str) -> bool:
    if level == "ninguno":
        return True
    for m in MODULES:
        if m["key"] == module:
            return level in m["levels"]
    return False


def codenames_for(module: str, level: str) -> tuple[str, ...]:
    """Devuelve los codenames que otorga `module` a nivel `level`.

    Si el módulo o nivel no existe, devuelve tupla vacía.
    """
    if level == "ninguno":
        return ()
    return MODULE_CODENAMES.get((module, level), ())


def permissions_for_matrix(matrix: dict[str, str]) -> set[str]:
    """Traduce una matriz {modulo: nivel} a un set de codenames.

    Niveles válidos por módulo son los declarados en `MODULES`; cualquier
    entrada inválida se ignora silenciosamente (la validación estricta se hace
    en el endpoint que recibe la matriz).
    """
    result: set[str] = set()
    for module, level in matrix.items():
        if not is_valid_module(module):
            continue
        result.update(codenames_for(module, level))
    return result


def level_for_role(role_permissions: Iterable[str]) -> dict[str, str]:
    """
    Inverso: dado un set de codenames del rol, deduce el nivel por módulo.

    Reglas:
      - Si el módulo no tiene codenames del rol → "ninguno".
      - Si el rol tiene todos los codenames de "escritura" → "escritura".
      - Si tiene los de "lectura" → "lectura".
      - Cualquier otro caso (mixto, parcial) → "lectura" (no se queda en
        "ninguno" si el rol tiene al menos un codename del módulo).
    """
    perms = set(role_permissions)
    result: dict[str, str] = {}

    for module in MODULES:
        key = module["key"]
        levels = module["levels"]
        write_codenames = set(codenames_for(key, "escritura"))
        read_codenames = set(codenames_for(key, "lectura"))

        if "escritura" in levels and write_codenames and write_codenames.issubset(perms):
            result[key] = "escritura"
        elif read_codenames and read_codenames.issubset(perms):
            result[key] = "lectura"
        elif read_codenames & perms:
            # Permisos parciales del módulo: trátalo como lectura, no "ninguno".
            result[key] = "lectura"
        else:
            result[key] = "ninguno"
    return result


def all_known_codenames() -> set[str]:
    """Set de todos los codenames que la matriz puede otorgar."""
    out: set[str] = set()
    for codenames in MODULE_CODENAMES.values():
        out.update(codenames)
    return out
