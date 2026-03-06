from __future__ import annotations

import mygeotab

from app.core.config import GeotabConfig


def build_client(cfg: GeotabConfig):
    client = mygeotab.API(
        username=cfg.username,
        password=cfg.password,
        database=cfg.database,
    )
    client.authenticate()
    return client


def find_device_by_plate(client, plate: str) -> dict | None:
    devices = client.call("Get", typeName="Device", search={"licensePlate": plate})
    if not devices:
        return None
    return devices[0]


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