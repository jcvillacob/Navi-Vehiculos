"""Carroceria y configuracion de ejes de un vehiculo.

Fenix no expone la carroceria en una columna propia utilizable: `Tipo de
vehiculo` solo esta poblada en ~39 % de las placas que gestionamos y
`Configuracion` es el nivel de equipamiento (PLUS, CLASSIC, AMT EU 6), no la
carroceria. El dato real vive en el texto libre de `Nombre Vehiculo`
("CHASIS CAMION AUMAN M4 BJ1186 4X2 PBV 17T"), que ya se persiste en
`vehicle_motor_assignments.nombre_vehiculo` y cubre ~95 % de la flota.

Por eso la carroceria se DERIVA del nombre en cada lectura en vez de
almacenarse: si se afinan las palabras clave, toda la flota se reclasifica sin
backfill. Las columnas `body_type` / `axle_config` de la tabla guardan solo el
override manual (NULL = usar el valor derivado), igual que `category`.
"""

from __future__ import annotations

import re
import unicodedata

# Carrocerias soportadas. El orden es el de prioridad de deteccion: la primera
# que coincide gana, asi que las mas especificas van antes que las genericas
# ("CHASIS CAMION" antes que "CAMION", "CAMIONETA" antes que "CAMION").
BODY_TYPES: tuple[str, ...] = (
    "Tractocamion",
    "Mixer",
    "Volqueta",
    "Chasis camion",
    "Chasis bus",
    "Camioneta",
    "Camion",
    "Buseta",
    "Bus",
    "Van",
    "Grua",
)

# Palabras clave por carroceria, ya normalizadas (mayusculas, sin tildes). Se
# comparan con limites de palabra: sin ellos "ADVANCE" haria match con "VAN".
_BODY_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Tractocamion", ("TRACTOCAMION", "TRACTO CAMION")),
    ("Mixer", ("MIXER", "MEZCLADOR", "MEZCLADORA")),
    ("Volqueta", ("VOLQUETA", "VOLCO")),
    ("Chasis camion", ("CHASIS CAMION",)),
    ("Chasis bus", ("CHASIS BUS",)),
    ("Camioneta", ("CAMIONETA",)),
    ("Camion", ("CAMION",)),
    ("Buseta", ("BUSETA",)),
    ("Bus", ("BUS",)),
    ("Van", ("VAN",)),
    ("Grua", ("GRUA",)),
)

_BODY_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        body_type,
        re.compile(
            "|".join(r"\b" + re.escape(keyword) + r"\b" for keyword in keywords)
        ),
    )
    for body_type, keywords in _BODY_TYPE_KEYWORDS
)

# "4X2", "6 x 4", "8X4". Se exige limite de palabra a ambos lados para no picar
# dentro de codigos de linea tipo "BJ4259SMFKB-C1".
_AXLE_PATTERN = re.compile(r"\b(\d{1,2})\s*X\s*(\d{1,2})\b")

# Rango plausible de una configuracion de ejes real: total de ruedas entre 4 y
# 12, tractoras entre 2 (4X2) y el total (6X6). Descarta ruido numerico del
# nombre, como los "17T" o los codigos de linea.
_AXLE_TOTAL_MIN = 4
_AXLE_TOTAL_MAX = 12
_AXLE_DRIVEN_MIN = 2


def _is_plausible_axle_config(total: int, driven: int) -> bool:
    """True si "<total>X<driven>" puede ser una configuracion de ejes real."""
    return (
        _AXLE_TOTAL_MIN <= total <= _AXLE_TOTAL_MAX
        and _AXLE_DRIVEN_MIN <= driven <= total
    )


def _normalize_text(value: str | None) -> str:
    """Mayusculas sin tildes, para comparar palabras clave de forma estable."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.upper()


def derive_body_type(nombre_vehiculo: str | None) -> str | None:
    """Carroceria inferida del nombre Fenix. None si el nombre no la revela.

    Los vehiculos usados sin ficha comercial llegan como "VEHICULO MARCA FOTON
    USADO" y no dan ninguna senal: se devuelve None para que la UI los muestre
    como "Sin clasificar" y el usuario fije el override manual.
    """
    normalized = _normalize_text(nombre_vehiculo)
    if not normalized:
        return None
    for body_type, pattern in _BODY_TYPE_PATTERNS:
        if pattern.search(normalized):
            return body_type
    return None


def derive_axle_config(nombre_vehiculo: str | None) -> str | None:
    """Configuracion de ejes ("6X4") inferida del nombre Fenix, o None."""
    normalized = _normalize_text(nombre_vehiculo)
    if not normalized:
        return None
    for match in _AXLE_PATTERN.finditer(normalized):
        total = int(match.group(1))
        driven = int(match.group(2))
        if _is_plausible_axle_config(total, driven):
            return f"{total}X{driven}"
    return None


def normalize_body_type(value: str | None) -> str | None:
    """Valida un override de carroceria. Vacio -> None (volver a derivar)."""
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    normalized = _normalize_text(cleaned)
    for body_type in BODY_TYPES:
        if _normalize_text(body_type) == normalized:
            return body_type
    raise ValueError("Carroceria invalida. Usa: " + ", ".join(BODY_TYPES) + ".")


def normalize_axle_config(value: str | None) -> str | None:
    """Valida un override de ejes. Vacio -> None (volver a derivar)."""
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    match = _AXLE_PATTERN.fullmatch(_normalize_text(cleaned))
    if match is None:
        raise ValueError("Configuracion de ejes invalida. Usa un formato como 6X4.")
    total = int(match.group(1))
    driven = int(match.group(2))
    if not _is_plausible_axle_config(total, driven):
        raise ValueError(
            "Configuracion de ejes fuera de rango. El total debe estar entre "
            f"{_AXLE_TOTAL_MIN} y {_AXLE_TOTAL_MAX}, y las tractoras entre "
            f"{_AXLE_DRIVEN_MIN} y el total."
        )
    return f"{total}X{driven}"


def resolve_body_type(
    nombre_vehiculo: str | None,
    override: str | None = None,
) -> tuple[str | None, bool]:
    """Devuelve (carroceria efectiva, si viene del nombre en vez del override)."""
    cleaned = (override or "").strip()
    if cleaned:
        return cleaned, False
    return derive_body_type(nombre_vehiculo), True


def resolve_axle_config(
    nombre_vehiculo: str | None,
    override: str | None = None,
) -> tuple[str | None, bool]:
    """Devuelve (ejes efectivos, si vienen del nombre en vez del override)."""
    cleaned = (override or "").strip()
    if cleaned:
        return cleaned, False
    return derive_axle_config(nombre_vehiculo), True
