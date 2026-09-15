"""Derivacion de carroceria y ejes desde el nombre Fenix.

Los nombres usados aqui son textos reales observados en
`dbo.T_DIM_VEHICULO_CONFIABILIDAD`. No tocan base de datos: el modulo es puro.
"""

from __future__ import annotations

import pytest

from app.services.vehicle_body_type import (
    BODY_TYPES,
    derive_axle_config,
    derive_body_type,
    normalize_axle_config,
    normalize_body_type,
    resolve_axle_config,
    resolve_body_type,
)


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("TRACTOCAMION AUMAN GTL BJ4259 6X4", "Tractocamion"),
        ("CHASIS CAMION AUMAN M4 BJ1186 4X2 PBV 17T", "Chasis camion"),
        ("CHASIS CAMION EST 4MANOS 8X4", "Chasis camion"),
        ("CHASIS CAMION 7600 6X4 STD (WORKSTAR)", "Chasis camion"),
        ("VOLQUETA AUMAN BJ3319 8X4", "Volqueta"),
        ("CAMION MIXER 8X4", "Mixer"),
        ("BUS INTERCITY", "Bus"),
        ("VAN TOANO VB1 DIESEL", "Van"),
    ],
)
def test_derive_body_type_reconoce_nombres_reales(nombre, esperado):
    assert derive_body_type(nombre) == esperado


def test_derive_body_type_prefiere_la_carroceria_mas_especifica():
    # "CHASIS CAMION" y "MIXER" contienen o conviven con "CAMION": debe ganar
    # la variante especifica, no la generica.
    assert derive_body_type("CHASIS CAMION AUMAN") == "Chasis camion"
    assert derive_body_type("CAMION MIXER 8X4") == "Mixer"
    assert derive_body_type("CAMIONETA 4X4") == "Camioneta"
    # TRACTOCAMION contiene "CAMION" como subcadena, pero no como palabra.
    assert derive_body_type("TRACTOCAMION 6X4") == "Tractocamion"


def test_derive_body_type_no_confunde_subcadenas():
    # "ADVANCE" contiene "VAN" como subcadena: sin limite de palabra daria Van.
    assert derive_body_type("WB 5.150 E6 ADVANCE") is None
    # Los usados sin ficha comercial no dicen nada de la carroceria.
    assert derive_body_type("VEHICULO MARCA FOTON USADO") is None
    assert derive_body_type("") is None
    assert derive_body_type(None) is None


def test_derive_body_type_ignora_tildes_y_minusculas():
    assert derive_body_type("tractocamión 6x4") == "Tractocamion"
    assert derive_body_type("grúa articulada") == "Grua"


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("CHASIS CAMION AUMAN M4 BJ1186 4X2 PBV 17T", "4X2"),
        ("TRACTOCAMION 6X4", "6X4"),
        ("CHASIS CAMION EST 4MANOS 8X4", "8X4"),
        ("9400i 6x4", "6X4"),
        ("CAMION 6 X 4", "6X4"),
    ],
)
def test_derive_axle_config_reconoce_configuraciones(nombre, esperado):
    assert derive_axle_config(nombre) == esperado


@pytest.mark.parametrize(
    "nombre",
    [
        "BJ4259SMFKB-C1",  # codigo de linea, la X no separa dos numeros
        "HX220S",
        "ESPECIAL 15+C TECHO MEDIO",
        "VEHICULO MARCA FOTON USADO",
        "M.A 8.7",
    ],
)
def test_derive_axle_config_descarta_ruido(nombre):
    assert derive_axle_config(nombre) is None


def test_derive_axle_config_descarta_valores_implausibles():
    # 99X99 casa con el patron pero no es una configuracion real.
    assert derive_axle_config("MODELO 99X99") is None
    # Mas ruedas tractoras que totales tampoco existe.
    assert derive_axle_config("MODELO 4X6") is None


def test_normalize_body_type_acepta_el_catalogo_sin_importar_formato():
    assert normalize_body_type("tractocamion") == "Tractocamion"
    assert normalize_body_type("  VOLQUETA  ") == "Volqueta"
    assert normalize_body_type("Chasis camión") == "Chasis camion"
    for body_type in BODY_TYPES:
        assert normalize_body_type(body_type) == body_type


def test_normalize_body_type_vacio_limpia_el_override():
    assert normalize_body_type(None) is None
    assert normalize_body_type("   ") is None


def test_normalize_body_type_rechaza_valores_fuera_del_catalogo():
    with pytest.raises(ValueError, match="Carroceria invalida"):
        normalize_body_type("Tractomula")


def test_normalize_axle_config_normaliza_y_valida():
    assert normalize_axle_config(" 6x4 ") == "6X4"
    assert normalize_axle_config("4 X 2") == "4X2"
    assert normalize_axle_config(None) is None
    assert normalize_axle_config("") is None
    with pytest.raises(ValueError, match="invalida"):
        normalize_axle_config("seis por cuatro")
    with pytest.raises(ValueError, match="fuera de rango"):
        normalize_axle_config("99X99")


def test_resolve_prefiere_el_override_sobre_la_derivacion():
    nombre = "TRACTOCAMION 6X4"

    assert resolve_body_type(nombre, None) == ("Tractocamion", True)
    assert resolve_body_type(nombre, "Volqueta") == ("Volqueta", False)
    # Un override en blanco equivale a no tenerlo.
    assert resolve_body_type(nombre, "   ") == ("Tractocamion", True)

    assert resolve_axle_config(nombre, None) == ("6X4", True)
    assert resolve_axle_config(nombre, "8X4") == ("8X4", False)


def test_resolve_marca_como_derivado_aunque_no_haya_dato():
    # Sin override y sin senal en el nombre: efectivo None, pero sigue siendo
    # "derivado" para que la UI lo muestre como sin clasificar, no como override.
    assert resolve_body_type("VEHICULO MARCA FOTON USADO", None) == (None, True)
    assert resolve_axle_config(None, None) == (None, True)
