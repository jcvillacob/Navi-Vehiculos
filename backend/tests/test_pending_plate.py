"""
Tests de la logica de placa pendiente (registro sin placa real).

Un vehiculo consultado por VIN que Fenix no resuelve a placa se registraba en
ningun lado: se perdia el cliente, la database y las credenciales. Ahora entra
con una placa temporal marcada ``plate_pending`` y despues solo se completa la
placa.

Cubren (sin DB):
- Reconocimiento de placas temporales.
- ``register_vehicle_assignment`` sin identificacion suficiente.
- Reuso de la fila existente por VIN vs. placa nueva de la secuencia.
- Validaciones de ``set_vehicle_plate`` antes de tocar la DB.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.motor_catalog import (
    PENDING_PLATE_PREFIX,
    _resolve_pending_plate,
    is_pending_plate,
    register_vehicle_assignment,
    set_vehicle_plate,
)


# ── Dobles de conexion ──────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows: list[Any]):
        self._rows = rows
        self._result: Any = None
        self.queries: list[str] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.queries.append(query)
        self._result = self._rows.pop(0) if self._rows else None

    def fetchone(self) -> Any:
        return self._result


class _FakeConn:
    def __init__(self, rows: list[Any]):
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self, **_kwargs: Any) -> _FakeCursor:
        return self.cursor_obj


# ── Placas temporales ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "plate,expected",
    [
        ("P-000001", True),
        ("p-000042", True),
        (f"{PENDING_PLATE_PREFIX}123456", True),
        ("ABC123", False),
        ("P123456", False),
        ("", False),
        (None, False),
    ],
)
def test_is_pending_plate(plate: str | None, expected: bool) -> None:
    assert is_pending_plate(plate) is expected


# ── Registro sin placa ──────────────────────────────────────────────────────


def test_register_sin_placa_ni_vin_no_registra() -> None:
    """Sin placa y sin VIN no hay nada que identifique al vehiculo."""
    assert register_vehicle_assignment(plate=None, technical_number="D563027BX03") is None


def test_register_sin_technical_number_no_registra() -> None:
    assert register_vehicle_assignment(plate="ABC123", technical_number="   ") is None


def test_resolve_pending_plate_reusa_fila_del_vin() -> None:
    """Si el VIN ya esta registrado no se duplica el vehiculo."""
    conn = _FakeConn([{"plate": "ABC123", "plate_pending": False}])

    plate, pending = _resolve_pending_plate(conn, "3HCEJTAR1VL418226")

    assert plate == "ABC123"
    assert pending is False


def test_resolve_pending_plate_toma_de_la_secuencia() -> None:
    """VIN nuevo: placa temporal formateada a 6 digitos."""
    conn = _FakeConn([None, {"seq": 7}])

    plate, pending = _resolve_pending_plate(conn, "3HCEJTAR8VL409071")

    assert plate == "P-000007"
    assert pending is True
    assert len(plate) <= 10


def test_resolve_pending_plate_sin_vin_no_busca_fila() -> None:
    conn = _FakeConn([{"seq": 12}])

    plate, pending = _resolve_pending_plate(conn, None)

    assert plate == "P-000012"
    assert pending is True


# ── Completar la placa ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,new,expected_message",
    [
        ("", "ABC123", "placa actual es obligatoria"),
        ("P-000001", "", "placa nueva es obligatoria"),
        ("P-000001", "ABCDE1234567", "no puede superar 10"),
        ("P-000001", "P-000002", "prefijo de placas pendientes"),
        ("ABC123", "abc123", "igual a la actual"),
    ],
)
def test_set_vehicle_plate_validaciones(current: str, new: str, expected_message: str) -> None:
    """Las validaciones corren antes de abrir conexion a la DB."""
    with pytest.raises(ValueError) as exc:
        set_vehicle_plate(current, new)

    assert expected_message in str(exc.value)
