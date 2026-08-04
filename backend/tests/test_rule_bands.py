"""Tests del sugeridor de bandas de RPM (app/services/rule_bands.py).

Pura logica de strings: no requiere base de datos.
"""
from __future__ import annotations

import pytest

from app.services.rule_bands import (
    RULE_BANDS,
    normalize_rule_name,
    suggest_band,
    suggest_is_descenso,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        # Los 7 mapeos basicos.
        ("Rango Bajo X11", "rango_bajo"),
        ("Rango Economico X11", "rango_economico"),
        ("Rango Balanceado X11", "rango_balanceado"),
        ("Rango Potencia X11", "rango_potencia"),
        ("Rango Potencia Ineficiente X11", "rango_potencia_ineficiente"),
        ("Exceso de RPM X11", "exceso_rpm"),
        ("Ralenti Prolongado", "ralenti"),
        # Regla de negocio: "eficiente" = economico, NO balanceado.
        ("Potencia Eficiente", "rango_potencia"),  # potencia gana antes que eficiente
        ("Zona Eficiente", "rango_economico"),
        # "consumo" -> ineficiente.
        ("Rango Consumo X11", "rango_potencia_ineficiente"),
        # Prioridad de solapamiento: ineficiente contiene eficiente.
        ("Potencia Ineficiente", "rango_potencia_ineficiente"),
        # Acentos y espacios.
        ("Rango Económico   Descenso", "rango_economico"),
        ("RALENTÍ", "ralenti"),
        # Sin match.
        ("Regla Rara Sin Palabra Clave", None),
        ("", None),
        (None, None),
    ],
)
def test_suggest_band(name, expected):
    assert suggest_band(name) == expected


def test_suggest_band_values_are_in_enum():
    for name in ["Rango Bajo", "Exceso RPM", "Ralenti"]:
        band = suggest_band(name)
        assert band in RULE_BANDS


@pytest.mark.parametrize(
    "name,band,expected",
    [
        ("Rango Economico Descenso", "rango_economico", True),
        ("Rango Económico Descenso", "rango_economico", True),  # con tilde
        ("Rango Economico", "rango_economico", False),  # sin descenso
        ("Ralenti Descenso", "ralenti", False),  # ralenti nunca descenso
        ("Descenso Raro", None, False),  # band None -> no descenso (CHECK)
    ],
)
def test_suggest_is_descenso(name, band, expected):
    assert suggest_is_descenso(name, band) is expected


def test_normalize_rule_name():
    assert normalize_rule_name("  Rango   Económico  ") == "rango economico"
    assert normalize_rule_name(None) == ""
