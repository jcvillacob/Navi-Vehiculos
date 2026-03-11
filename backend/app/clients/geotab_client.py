from __future__ import annotations

import re

import mygeotab

from app.core.config import GeotabConfig

_PLATE_PATTERN = re.compile(r"^[A-Z]{3}[0-9]{3}$")


def build_client(cfg: GeotabConfig):
    client = mygeotab.API(
        username=cfg.username,
        password=cfg.password,
        database=cfg.database,
    )
    client.authenticate()
    return client


def _normalize_plate(value: str | None) -> str | None:
    if not value:
        return None
    normalized = "".join(char for char in str(value).strip().upper() if char.isalnum())
    if _PLATE_PATTERN.fullmatch(normalized):
        return normalized
    return normalized or None


def _normalize_vin(value: str | None) -> str | None:
    if not value:
        return None
    normalized = "".join(char for char in str(value).strip().upper() if char.isalnum())
    return normalized or None


def _search_devices(client, search: dict | None = None) -> list[dict]:
    response = client.call("Get", typeName="Device", search=search or {})
    return response or []


def _device_matches_plate(device: dict, plate: str) -> bool:
    normalized_plate = _normalize_plate(plate)
    if not normalized_plate:
        return False

    for key in ("licensePlate", "name"):
        if _normalize_plate(device.get(key)) == normalized_plate:
            return True
    return False


def _device_matches_vin(device: dict, vin: str) -> bool:
    normalized_vin = _normalize_vin(vin)
    if not normalized_vin:
        return False
    return _normalize_vin(extract_vin(device)) == normalized_vin


def _find_device_in_collection(
    devices: list[dict], *, plate: str | None = None, vin: str | None = None
) -> dict | None:
    for device in devices:
        if plate and _device_matches_plate(device, plate):
            return device
        if vin and _device_matches_vin(device, vin):
            return device
    return None


def _get_all_devices(client) -> list[dict]:
    return _search_devices(client)


def find_device(client, *, plate: str | None = None, vin: str | None = None) -> dict | None:
    normalized_plate = _normalize_plate(plate)
    normalized_vin = _normalize_vin(vin)

    if normalized_plate:
        for field in ("licensePlate", "name"):
            devices = _search_devices(client, {field: normalized_plate})
            match = _find_device_in_collection(devices, plate=normalized_plate)
            if match:
                return match

    all_devices: list[dict] | None = None

    if normalized_vin:
        all_devices = _get_all_devices(client)
        match = _find_device_in_collection(all_devices, vin=normalized_vin)
        if match:
            return match

    if normalized_plate:
        if all_devices is None:
            all_devices = _get_all_devices(client)
        match = _find_device_in_collection(all_devices, plate=normalized_plate)
        if match:
            return match

    return None


def find_device_by_plate(client, plate: str) -> dict | None:
    return find_device(client, plate=plate)


def extract_vin(device: dict) -> str | None:
    for key in ("vehicleIdentificationNumber", "vin", "VIN"):
        value = device.get(key)
        if value:
            return str(value).strip()
    return None


def get_vin_from_plate(plate: str, cfg: GeotabConfig) -> str | None:
    client = build_client(cfg)
    device = find_device_by_plate(client, plate)
    if not device:
        return None
    return extract_vin(device)


def get_device_from_plate(plate: str, cfg: GeotabConfig) -> dict | None:
    client = build_client(cfg)
    return find_device_by_plate(client, plate)


def get_device_from_vin(vin: str, cfg: GeotabConfig) -> dict | None:
    client = build_client(cfg)
    return find_device(client, vin=vin)


def vehicle_exists_for_plate(plate: str, cfg: GeotabConfig) -> bool:
    return get_device_from_plate(plate, cfg) is not None
