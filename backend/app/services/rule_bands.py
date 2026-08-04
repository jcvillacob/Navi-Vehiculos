"""Bandas de RPM explicitas para aplicaciones de reglas Geotab (category 'operacion').

Portal Clientes calcula la distribucion de tiempo por banda de RPM. Antes lo hacia
infiriendo la banda desde el NOMBRE de la regla por palabras clave, lo cual es fragil:
si una regla se renombra y deja de matchear, su tiempo desaparece en silencio.

Aqui la banda es un dato explicito. Este modulo es la UNICA fuente de verdad del
sugeridor por palabra clave: lo usan el backfill de la migracion, la creacion/edicion
de aplicaciones y la UI (via GeotabRuleInspection.suggested_*).
"""
from __future__ import annotations

import re
import unicodedata

# Enum cerrado de bandas. El ORDEN de esta tupla no importa para validacion, pero se
# expone para la UI y las validaciones de payload.
RULE_BANDS: tuple[str, ...] = (
    "rango_bajo",
    "rango_economico",
    "rango_balanceado",
    "rango_potencia",
    "rango_potencia_ineficiente",
    "exceso_rpm",
    "ralenti",
)

# Mapeo por palabra clave. El ORDEN importa: gana la primera coincidencia y resuelve
# solapamientos de substring ("ineficiente" contiene "eficiente"; "potencia
# ineficiente" debe ganar sobre "potencia").
_BAND_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ineficiente", "consumo"), "rango_potencia_ineficiente"),
    (("potencia",), "rango_potencia"),
    (("balanceado",), "rango_balanceado"),
    (("economico", "eficiente"), "rango_economico"),
    (("bajo",), "rango_bajo"),
    (("exceso",), "exceso_rpm"),
    (("ralenti",), "ralenti"),
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_rule_name(name: str | None) -> str:
    """Minusculas, sin acentos, espacios colapsados."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(name))
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WHITESPACE_RE.sub(" ", without_accents).strip().lower()


def suggest_band(name: str | None) -> str | None:
    """Banda sugerida segun el nombre de la regla, o None si no matchea nada."""
    normalized = normalize_rule_name(name)
    if not normalized:
        return None
    for keywords, band in _BAND_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return band
    return None


def suggest_is_descenso(name: str | None, band: str | None) -> bool:
    """True si el nombre contiene 'descenso' y la banda existe y no es ralenti.

    Respeta los CHECK: ralenti nunca lleva descenso; is_descenso exige band no nulo.
    """
    if band is None or band == "ralenti":
        return False
    return "descenso" in normalize_rule_name(name)
