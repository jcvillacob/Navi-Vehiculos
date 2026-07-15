from __future__ import annotations

from app.clients.geotab_client import (
    _find_device_in_collection,
    find_matching_devices,
)


# Escenario real: Geotab tiene placas duplicadas (dos devices con la misma
# licensePlate/name). El bug de LQK264/LQK266 fue que el match por placa
# devolvia arbitrariamente el duplicado sin datos. Estos tests fijan el
# desempate estable.

def _dev(device_id: str, plate: str, active: bool = True) -> dict:
    return {
        "id": device_id,
        "name": plate,
        "licensePlate": plate,
        "activeTo": "2050-01-01T00:00:00.000Z" if active else "2020-01-01T00:00:00.000Z",
    }


def test_find_matching_devices_returns_all_duplicates():
    devices = [_dev("b33B", "LQK264"), _dev("b35D", "LQK264"), _dev("aaa", "XYZ999")]
    matches = find_matching_devices(devices, plate="LQK264")
    assert {d["id"] for d in matches} == {"b33B", "b35D"}


def test_preferred_id_wins_over_inventory_order():
    # El duplicado sin datos (b33B) aparece primero en el inventario, pero el
    # device que ya venia usandose (b35D) debe ganar.
    devices = [_dev("b33B", "LQK264"), _dev("b35D", "LQK264")]
    match = _find_device_in_collection(devices, plate="LQK264", preferred_id="b35D")
    assert match["id"] == "b35D"


def test_falls_back_to_active_when_no_preferred():
    devices = [_dev("b33B", "LQK264", active=False), _dev("b35D", "LQK264", active=True)]
    match = _find_device_in_collection(devices, plate="LQK264")
    assert match["id"] == "b35D"


def test_active_beats_archived_preferred_id_self_heal():
    # Binding auto quedo corrupto apuntando al device archivado (b33B). El
    # activo debe ganar igual: el proximo calculo se auto-sana.
    devices = [_dev("b33B", "LQK264", active=False), _dev("b35D", "LQK264", active=True)]
    match = _find_device_in_collection(devices, plate="LQK264", preferred_id="b33B")
    assert match["id"] == "b35D"


def test_single_match_is_returned_directly():
    devices = [_dev("b35D", "LQK264")]
    match = _find_device_in_collection(devices, plate="LQK264", preferred_id="does-not-exist")
    assert match["id"] == "b35D"


def test_no_match_returns_none():
    devices = [_dev("b35D", "LQK264")]
    assert _find_device_in_collection(devices, plate="ZZZ000") is None


def test_preferred_id_ignored_when_not_among_matches():
    # preferred_id apunta a un device de otra placa: se ignora y cae al activo.
    devices = [_dev("b33B", "LQK264", active=True), _dev("b35D", "LQK264", active=False)]
    match = _find_device_in_collection(devices, plate="LQK264", preferred_id="other-plate-id")
    assert match["id"] == "b33B"
