"""
Tests del webhook Geotab "En taller" y del snapshot del mapa.

Cubre:
- Limpieza del payload (campos de texto, decimales, ids).
- Parseo del timestamp.
- Clasificacion enter / exit / unknown.
- Autenticacion del webhook (query string y header, 503 sin claves).
- Resolucion del vehiculo (VIN y fallback placa).
- Filtro de categoria efectiva.
- Idempotencia por exception_id.
- Transiciones de estado en Redis (enter, exit, reingreso, expiracion).
- Persistencia de auditoria en geotab_taller_events.
- Endpoint del mapa: auth, snapshot, ETag/304, cache.
- Sweep periodico de la gracia.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import psycopg
import pytest
from psycopg.rows import dict_row
from redis import Redis

from app.core.config import settings
from app.services import geotab_taller


# ── Helpers ───────────────────────────────────────────────────────────────


def _connect():
    raw = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(raw, row_factory=dict_row)


def _truncate_all() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE TABLE
                    geotab_taller_events,
                    vehicle_connection_log,
                    geotab_rule_group_rules,
                    geotab_rule_groups,
                    geotab_rules,
                    customer_database_credentials,
                    vehicle_motor_assignments,
                    customer_databases,
                    customers,
                    motor_catalog
                RESTART IDENTITY CASCADE;
                """
            )
        conn.commit()


def _insert_customer(name: str, category: str = "Ninguna") -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customers (name, category) VALUES (%s, %s) RETURNING id;",
                (name, category),
            )
            cid = int(cur.fetchone()["id"])
        conn.commit()
    return cid


def _insert_geotab_db(customer_id: int, db_name: str = "db_test") -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO customer_databases
                    (customer_id, database_name, username, password, connection_type)
                VALUES (%s, %s, 'u@t.c', 'p', 'geotab')
                RETURNING id;
                """,
                (customer_id, db_name),
            )
            did = int(cur.fetchone()["id"])
        conn.commit()
    return did


def _insert_vehicle(
    plate: str,
    vin: str,
    customer_id: int,
    database_id: int,
    technical_number: str = "TEC-1",
    category: str | None = None,
) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicle_motor_assignments
                    (plate, vin, technical_number, customer_id, customer_database_id, category)
                VALUES (%s, %s, %s, %s, %s, %s);
                """,
                (plate, vin, technical_number, customer_id, database_id, category),
            )
        conn.commit()


def _insert_motor(technical_number: str, engine_name: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO motor_catalog (technical_number, engine_name) VALUES (%s, %s);",
                (technical_number, engine_name),
            )
        conn.commit()


def _raw_payload(
    *,
    rule: str = "En_taller_-_Proyecto",
    date: str = "Jun,_24,_2026",
    time: str = "4:27:21_PM",
    timezone_str: str = "UTC",
    device_id: str = "b945",
    device_name: str = "LDYCCS8D0V0000035",
    vin: str = "LDYCCS8D0V0000035",
    zone_id: str = "b38FC7",
    zone_name: str = "Navitrans_Itagui",
    latitude: str = "6_15622",
    longitude: str = "-75_62516",
    odometer: str = "1,721_km",
    exception_id: str = "a2SRCOgOAx0iOM8LRsUWQyA",
) -> bytes:
    payload = {
        "event_info": {
            "exception_id": exception_id,
            "rule_triggered": rule,
            "date": date,
            "time": time,
            "timezone": timezone_str,
        },
        "asset_info": {
            "device_id": device_id,
            "device_name": device_name,
            "vin": vin,
        },
        "telemetry_info": {
            "zone_id": zone_id,
            "zone_name": zone_name,
            "latitude": latitude,
            "longitude": longitude,
            "odometer": odometer,
        },
    }
    return json.dumps(payload).encode("utf-8")


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def taller_db():
    _truncate_all()
    yield
    _truncate_all()


@pytest.fixture
def flota_customer(taller_db):
    cid = _insert_customer("Cliente Flota", "Flota Administrada")
    did = _insert_geotab_db(cid)
    return {"customer_id": cid, "database_id": did}


@pytest.fixture
def experiencia_customer(taller_db):
    cid = _insert_customer("Cliente Experiencia", "Experiencia Superior")
    did = _insert_geotab_db(cid, "db_exp")
    return {"customer_id": cid, "database_id": did}


@pytest.fixture
def ninguna_customer(taller_db):
    cid = _insert_customer("Cliente Neutro", "Ninguna")
    did = _insert_geotab_db(cid, "db_neutro")
    return {"customer_id": cid, "database_id": did}


@pytest.fixture
def flota_vehicle(flota_customer, taller_db):
    _insert_motor("TEC-1", "ISD")
    _insert_vehicle(
        plate="TLK240",
        vin="LDYCCS8D0V0000035",
        customer_id=flota_customer["customer_id"],
        database_id=flota_customer["database_id"],
    )
    return {"plate": "TLK240", **flota_customer}


@pytest.fixture
def experiencia_vehicle(experiencia_customer, taller_db):
    _insert_motor("TEC-1", "X15")
    _insert_vehicle(
        plate="EXP999",
        vin="EXPVIN0000000001",
        customer_id=experiencia_customer["customer_id"],
        database_id=experiencia_customer["database_id"],
    )
    return {"plate": "EXP999", **experiencia_customer}


@pytest.fixture
def ninguna_vehicle(ninguna_customer, taller_db):
    _insert_motor("TEC-1", "ISB")
    _insert_vehicle(
        plate="NEU111",
        vin="NEUVIN0000000001",
        customer_id=ninguna_customer["customer_id"],
        database_id=ninguna_customer["database_id"],
    )
    return {"plate": "NEU111", **ninguna_customer}


@pytest.fixture
def override_vehicle(taller_db):
    """Vehiculo con override propio 'Flota Administrada' aunque su cliente sea 'Ninguna'."""
    cid = _insert_customer("Cliente Base", "Ninguna")
    did = _insert_geotab_db(cid, "db_base")
    _insert_motor("TEC-1", "ISX")
    _insert_vehicle(
        plate="OVR001",
        vin="OVRVIN0000000001",
        customer_id=cid,
        database_id=did,
        category="Flota Administrada",
    )
    return {"plate": "OVR001", "customer_id": cid, "database_id": did}


@pytest.fixture
def plate_only_vehicle(taller_db):
    """Vehiculo que solo se matchea por placa (su device_name sera la placa, no el vin)."""
    cid = _insert_customer("Cliente Placa", "Flota Administrada")
    did = _insert_geotab_db(cid, "db_plate")
    _insert_motor("TEC-1", "ISX")
    _insert_vehicle(
        plate="PLT777",
        vin="PLTVIN0000000001",
        customer_id=cid,
        database_id=did,
    )
    return {"plate": "PLT777", "customer_id": cid, "database_id": did}


@pytest.fixture
def redis_raw() -> Redis:
    return Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


@pytest.fixture
def mapa_manager_user(admin_user):
    """Admin no tiene mapa.taller.manage en los seeds base; lo agregamos para
    los tests de operaciones manuales sin tocar codigo de produccion."""
    from app.services.auth_service import clear_role_permissions_cache

    with _connect() as conn:
        with conn.cursor() as cur:
            # El catalogo `permissions` se siembra por migraciones y no incluye
            # los codenames nuevos de module_registry en la DB de test.
            cur.execute(
                """
                INSERT INTO permissions (codename, description)
                VALUES ('mapa.taller.manage', 'Gestionar estado manual del mapa de taller')
                ON CONFLICT (codename) DO NOTHING;
                """
            )
            cur.execute(
                """
                INSERT INTO role_permissions (role, permission)
                VALUES ('admin', 'mapa.taller.manage')
                ON CONFLICT (role, permission) DO NOTHING;
                """
            )
        conn.commit()
    clear_role_permissions_cache("admin")
    return admin_user


# ── Limpieza / parseo / clasificacion (unit) ─────────────────────────────


def test_clean_payload_underscore_rules():
    raw = {
        "event_info": {
            "exception_id": "a1",
            "rule_triggered": "En_taller_-_Proyecto",
            "date": "Jun,_24,_2026",
            "time": "4:27:21_PM",
            "timezone": "UTC",
        },
        "asset_info": {
            "device_id": "b945",
            "device_name": "LDYCCS8D0V0000035",
            "vin": "LDYCCS8D0V0000035",
        },
        "telemetry_info": {
            "zone_id": "b38FC7",
            "zone_name": "Navitrans_Itagui",
            "latitude": "6_15622",
            "longitude": "-75_62516",
            "odometer": "1,721_km",
        },
    }
    cleaned = geotab_taller._clean_payload(raw)
    assert cleaned["event_info"]["rule_triggered"] == "En taller - Proyecto"
    assert cleaned["event_info"]["date"] == "Jun, 24, 2026"
    assert cleaned["event_info"]["time"] == "4:27:21 PM"
    assert cleaned["telemetry_info"]["zone_name"] == "Navitrans Itagui"
    assert cleaned["telemetry_info"]["latitude"] == 6.15622
    assert cleaned["telemetry_info"]["longitude"] == -75.62516
    assert cleaned["telemetry_info"]["odometer"] == "1,721 km"
    # Ids sin transformacion
    assert cleaned["asset_info"]["device_id"] == "b945"
    assert cleaned["asset_info"]["device_name"] == "LDYCCS8D0V0000035"


def test_parse_event_ts_utc():
    ts = geotab_taller._parse_event_ts_utc("Jun, 24, 2026", "4:27:21 PM", "UTC")
    assert ts.tzinfo is not None
    assert ts.hour == 16 and ts.minute == 27 and ts.second == 21
    assert ts.year == 2026 and ts.month == 6 and ts.day == 24


def test_classify_rule():
    assert geotab_taller.classify_rule("En taller - Proyecto") == "enter"
    assert geotab_taller.classify_rule("Salida taller - Proyecto") == "exit"
    assert geotab_taller.classify_rule("Otra regla") == "unknown"
    assert geotab_taller.classify_rule("") == "unknown"


# ── Resolucion de vehiculo ───────────────────────────────────────────────


def test_resolve_vehicle_by_vin(flota_vehicle):
    res = geotab_taller.resolve_vehicle(
        device_name="LDYCCS8D0V0000035", vin="LDYCCS8D0V0000035"
    )
    assert res is not None
    assert res["plate"] == "TLK240"
    assert res["category"] == "Flota Administrada"
    assert res["client_name"] == "Cliente Flota"
    assert res["motor"] == "ISD"


def test_resolve_vehicle_fallback_plate(plate_only_vehicle):
    """Cuando device_name coincide con la placa, se resuelve por placa."""
    res = geotab_taller.resolve_vehicle(
        device_name="PLT777", vin="PLTVIN0000000001"
    )
    # El primer intento (vin) no matchea -> fallback por placa.
    assert res is not None
    assert res["plate"] == "PLT777"


def test_resolve_vehicle_not_found(taller_db):
    res = geotab_taller.resolve_vehicle(
        device_name="NOEXISTE000000000", vin="NOEXISTE000000000"
    )
    assert res is None


def test_vehicle_category_override(override_vehicle):
    res = geotab_taller.resolve_vehicle(
        device_name="OVRVIN0000000001", vin="OVRVIN0000000001"
    )
    assert res is not None
    assert res["category"] == "Flota Administrada"  # override propio


# ── Webhook: autenticacion ──────────────────────────────────────────────


async def test_webhook_requires_api_key(client, flota_vehicle, monkeypatch):
    monkeypatch.delenv("GEOTAB_WEBHOOK_API_KEYS", raising=False)
    body = _raw_payload()
    response = await client.post(
        "/api/v1/geotab/taller", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 503

    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "clave-geo")
    response = await client.post(
        "/api/v1/geotab/taller", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401

    response = await client.post(
        "/api/v1/geotab/taller?api_key=mala", content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401


async def test_webhook_accepts_query_key(client, flota_vehicle, monkeypatch):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "clave-geo,otra")
    body = _raw_payload(exception_id="q1")
    response = await client.post(
        f"/api/v1/geotab/taller?{urlencode({'api_key': 'clave-geo'})}",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["plate"] == "TLK240"
    assert payload["ignored"] is False


async def test_webhook_accepts_header_key(client, flota_vehicle, monkeypatch):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "clave-geo")
    body = _raw_payload(exception_id="h1")
    response = await client.post(
        "/api/v1/geotab/taller",
        content=body,
        headers={"Content-Type": "application/json", "X-API-Key": "clave-geo"},
    )
    assert response.status_code == 200
    assert response.json()["plate"] == "TLK240"


# ── Webhook: flujos de evento ──────────────────────────────────────────


async def test_webhook_enter_creates_state(
    client, flota_vehicle, redis_raw, monkeypatch
):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    body = _raw_payload(exception_id="e1")
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] is False

    # Estado en Redis.
    state = redis_raw.hgetall("taller:state:TLK240")
    assert state["status"] == "in"
    assert state["zone_name"] == "Navitrans Itagui"
    assert float(state["lat"]) == 6.15622
    assert state["category"] == "Flota Administrada"
    assert redis_raw.sismember("taller:active", "TLK240") == 1

    # Evento persistido.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT event_kind, ignored, plate FROM geotab_taller_events ORDER BY id DESC LIMIT 1;"
            )
            row = cur.fetchone()
    assert row["event_kind"] == "enter"
    assert row["ignored"] is False
    assert row["plate"] == "TLK240"


async def test_webhook_enter_ignored_vehicle_not_found(client, taller_db, monkeypatch):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    body = _raw_payload(exception_id="n1")
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ignored"] is True
    assert payload["reason"] == "vehicle_not_found"


async def test_webhook_enter_ignored_category_ninguna(
    client, ninguna_vehicle, monkeypatch
):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    body = _raw_payload(
        exception_id="ng1",
        device_name="NEUVIN0000000001",
        vin="NEUVIN0000000001",
    )
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ignored"] is True
    assert payload["reason"] == "category_ninguna"
    assert payload["plate"] == "NEU111"


async def test_webhook_enter_override_accepted(
    client, override_vehicle, redis_raw, monkeypatch
):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    body = _raw_payload(
        exception_id="ov1",
        device_name="OVRVIN0000000001",
        vin="OVRVIN0000000001",
    )
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] is False
    assert redis_raw.hget("taller:state:OVR001", "category") == "Flota Administrada"


async def test_webhook_exit_enters_grace(
    client, flota_vehicle, redis_raw, monkeypatch
):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Enter primero
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="ex1"),
        headers={"Content-Type": "application/json"},
    )
    # Exit
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="ex2", rule="Salida_taller_-_Proyecto"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] is False
    state = redis_raw.hgetall("taller:state:TLK240")
    assert state["status"] == "grace"
    assert state["exit_ts"]


async def test_webhook_reentry_preserves_original_enter_ts(
    client, flota_vehicle, redis_raw, monkeypatch
):
    """Reingreso dentro de la ventana de gracia: conserva el enter_ts original."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Enter
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="re1"),
        headers={"Content-Type": "application/json"},
    )
    original_enter = redis_raw.hget("taller:state:TLK240", "enter_ts")
    # Exit
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="re2", rule="Salida_taller_-_Proyecto"),
        headers={"Content-Type": "application/json"},
    )
    assert redis_raw.hget("taller:state:TLK240", "status") == "grace"
    # Reingreso
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="re3"),
        headers={"Content-Type": "application/json"},
    )
    state = redis_raw.hgetall("taller:state:TLK240")
    assert state["status"] == "in"
    assert state["enter_ts"] == original_enter  # no se reinicia
    assert not state.get("exit_ts")  # limpio


async def test_webhook_reentry_after_grace_expired_is_fresh_start(
    client, flota_vehicle, redis_raw, monkeypatch
):
    """Reingreso >1h tras salida: fresh start (enter_ts nuevo, no conserva el original).

    Req: "si sale y demora mas de una hora ya se quita del todo".
    El estado grace puede seguir vivo en Redis por el buffer del TTL, pero
    la logica debe tratarlo como entrada nueva.
    """
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Enter con timestamp en el pasado
    past_enter = datetime.now(tz=timezone.utc) - timedelta(hours=3)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="late1",
            date=past_enter.strftime("%b, %d, %Y"),
            time=past_enter.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    original_enter = redis_raw.hget("taller:state:TLK240", "enter_ts")

    # Exit 2h despues del enter (se veia, >30 min)
    exit_ts = past_enter + timedelta(hours=2)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="late2",
            rule="Salida_taller_-_Proyecto",
            date=exit_ts.strftime("%b, %d, %Y"),
            time=exit_ts.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    assert redis_raw.hget("taller:state:TLK240", "status") == "grace"

    # Reingreso 1h15 despues del exit (>1h → gracia expirada → fresh start)
    return_ts = exit_ts + timedelta(hours=1, minutes=15)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="late3",
            date=return_ts.strftime("%b, %d, %Y"),
            time=return_ts.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    state = redis_raw.hgetall("taller:state:TLK240")
    assert state["status"] == "in"
    assert state["enter_ts"] != original_enter  # fresh start: enter_ts reiniciado
    assert not state.get("exit_ts")


async def test_webhook_reentry_within_grace_keeps_counting_even_if_never_visible(
    client, flota_vehicle, redis_raw, monkeypatch
):
    """Vehiculo <30 min (nunca visible) sale y vuelve <1h: sigue contando desde el enter original.

    Req: "menos de 30 min y sale, no cuenta" + "si sale y demora menos de una
    hora en volver a entrar, sigue contando". Al volver, el tiempo acumulado
    (enter original → ahora) puede superar 30 min y aparecer en el mapa.
    """
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Enter 40 min en el pasado (nunca visible por si solo hasta el momento del exit)
    enter_ts = datetime.now(tz=timezone.utc) - timedelta(minutes=40)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="nv1",
            date=enter_ts.strftime("%b, %d, %Y"),
            time=enter_ts.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    original_enter = redis_raw.hget("taller:state:TLK240", "enter_ts")

    # Exit 20 min despues del enter (vehicle estuvo 20 min, <30, nunca visible)
    exit_ts = enter_ts + timedelta(minutes=20)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="nv2",
            rule="Salida_taller_-_Proyecto",
            date=exit_ts.strftime("%b, %d, %Y"),
            time=exit_ts.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    assert redis_raw.hget("taller:state:TLK240", "status") == "grace"

    # Reingreso 30 min despues del exit (<1h → reingreso, sigue contando)
    return_ts = exit_ts + timedelta(minutes=30)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="nv3",
            date=return_ts.strftime("%b, %d, %Y"),
            time=return_ts.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    state = redis_raw.hgetall("taller:state:TLK240")
    assert state["status"] == "in"
    assert state["enter_ts"] == original_enter  # conserva: sigue contando
    assert not state.get("exit_ts")
    # minutes_inside desde el enter original = 40+20+30 = 70 min → ya visible
    enter_dt = datetime.fromisoformat(state["enter_ts"])
    minutes = int((datetime.now(tz=timezone.utc) - enter_dt).total_seconds() // 60)
    assert minutes >= 30


async def test_webhook_reenter_when_already_in_is_fresh_start(
    client, flota_vehicle, redis_raw, monkeypatch
):
    """Re-enter cuando ya esta `in` → fresh start (missed exit).

    Geotab no repite enters mientras el vehiculo esta dentro — solo dispara
    uno al cruzar la geocerca. Un segundo enter significa que salio y volvio
    a entrar sin que detectaramos la salida.
    """
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Enter 1h ago
    t0 = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="mi1",
            date=t0.strftime("%b, %d, %Y"),
            time=t0.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    original_enter = redis_raw.hget("taller:state:TLK240", "enter_ts")

    # Segundo enter 30 min despues (sin exit previo → missed exit → fresh)
    t1 = t0 + timedelta(minutes=30)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="mi2",
            date=t1.strftime("%b, %d, %Y"),
            time=t1.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    state = redis_raw.hgetall("taller:state:TLK240")
    assert state["status"] == "in"
    assert state["enter_ts"] != original_enter  # fresh: nuevo enter_ts
    assert state["manual"] == "false"


# ── Operaciones manuales ────────────────────────────────────────────────


async def test_manual_add_creates_state(
    client, flota_vehicle, mapa_manager_user, redis_raw
):
    """Agregar manualmente crea estado in con manual=true."""
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    response = await client.post(
        "/api/v1/mapa/taller/manual",
        json={"plate": "TLK240", "action": "add"},
    )
    assert response.status_code == 200
    state = redis_raw.hgetall("taller:state:TLK240")
    assert state["status"] == "in"
    assert state["manual"] == "true"
    assert state["hidden"] == "false"
    assert state["enter_ts"]


async def test_manual_add_requires_permission(client, flota_vehicle, viewer_user):
    """Solo quienes tienen mapa.taller.manage pueden agregar."""
    await client.post(
        "/api/v1/auth/login",
        json={"username": "viewer", "password": "ViewerPass1!"},
    )
    response = await client.post(
        "/api/v1/mapa/taller/manual",
        json={"plate": "TLK240", "action": "add"},
    )
    assert response.status_code == 403


async def test_manual_hide_excludes_from_snapshot(
    client, flota_vehicle, mapa_manager_user, redis_raw, monkeypatch
):
    """Ocultar manualmente quita el vehiculo del snapshot del mapa."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Crear estado real con >30 min
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=45)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="hd1",
            date=past.strftime("%b, %d, %Y"),
            time=past.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    # Aparece antes de ocultar
    r1 = await client.get("/api/v1/mapa/taller")
    assert any(v["plate"] == "TLK240" for v in r1.json()["vehicles"])
    # Ocultar
    response = await client.post(
        "/api/v1/mapa/taller/manual",
        json={"plate": "TLK240", "action": "hide"},
    )
    assert response.status_code == 200
    assert redis_raw.hget("taller:state:TLK240", "hidden") == "true"
    # Ya no aparece
    r2 = await client.get("/api/v1/mapa/taller")
    assert all(v["plate"] != "TLK240" for v in r2.json()["vehicles"])


async def test_manual_unhide_restores_to_snapshot(
    client, flota_vehicle, mapa_manager_user, redis_raw, monkeypatch
):
    """Des-ocultar vuelve a mostrar el vehiculo en el mapa."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=45)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="uh1",
            date=past.strftime("%b, %d, %Y"),
            time=past.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    await client.post(
        "/api/v1/mapa/taller/manual",
        json={"plate": "TLK240", "action": "hide"},
    )
    # Des-ocultar
    response = await client.post(
        "/api/v1/mapa/taller/manual",
        json={"plate": "TLK240", "action": "unhide"},
    )
    assert response.status_code == 200
    assert redis_raw.hget("taller:state:TLK240", "hidden") == "false"
    r = await client.get("/api/v1/mapa/taller")
    assert any(v["plate"] == "TLK240" for v in r.json()["vehicles"])


async def test_manual_close_deletes_state(
    client, flota_vehicle, mapa_manager_user, redis_raw, monkeypatch
):
    """Cerrar elimina el estado por completo."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=45)
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="cl1",
            date=past.strftime("%b, %d, %Y"),
            time=past.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    response = await client.post(
        "/api/v1/mapa/taller/manual",
        json={"plate": "TLK240", "action": "close"},
    )
    assert response.status_code == 200
    assert redis_raw.exists("taller:state:TLK240") == 0
    assert redis_raw.sismember("taller:active", "TLK240") == 0


async def test_enter_real_replaces_manual(
    client, flota_vehicle, mapa_manager_user, redis_raw, monkeypatch
):
    """Un enter real de Geotab reemplaza el estado manual (fresh, manual=false)."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Agregar manualmente
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    await client.post(
        "/api/v1/mapa/taller/manual",
        json={"plate": "TLK240", "action": "add"},
    )
    assert redis_raw.hget("taller:state:TLK240", "manual") == "true"
    # Enter real
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="rm1"),
        headers={"Content-Type": "application/json"},
    )
    state = redis_raw.hgetall("taller:state:TLK240")
    assert state["manual"] == "false"  # reemplazado
    assert state["status"] == "in"


async def test_snapshot_includes_manual_flag(
    client, flota_vehicle, mapa_manager_user, redis_raw, monkeypatch
):
    """El snapshot incluye el flag manual para que el front muestre el badge."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Agregar manualmente con enter_ts 45 min en el pasado
    past = (datetime.now(tz=timezone.utc) - timedelta(minutes=45)).isoformat()
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    await client.post(
        "/api/v1/mapa/taller/manual",
        json={"plate": "TLK240", "action": "add", "enter_ts": past},
    )
    r = await client.get("/api/v1/mapa/taller")
    tlk = next(v for v in r.json()["vehicles"] if v["plate"] == "TLK240")
    assert tlk["manual"] is True


async def test_webhook_exit_without_state_is_ignored(
    client, flota_vehicle, monkeypatch
):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="ws1", rule="Salida_taller_-_Proyecto"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ignored"] is True
    assert payload["reason"] == "exit_without_state"


async def test_webhook_unknown_rule_is_ignored(
    client, flota_vehicle, monkeypatch, redis_raw
):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="ur1", rule="Regla_rara"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "unknown_rule"
    # No se crea estado
    assert redis_raw.exists("taller:state:TLK240") == 0


async def test_webhook_duplicate_exception_id_is_ignored(
    client, flota_vehicle, redis_raw, monkeypatch
):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    body = _raw_payload(exception_id="dup1")
    r1 = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r1.json()["ignored"] is False
    r2 = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert r2.status_code == 200
    assert r2.json()["ignored"] is True
    assert r2.json()["reason"] == "duplicate"


async def test_webhook_bad_json_returns_error(client, monkeypatch):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "error"


# ── Mapa: endpoint ─────────────────────────────────────────────────────


async def test_mapa_requires_auth(client, flota_vehicle, monkeypatch):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    response = await client.get("/api/v1/mapa/taller")
    assert response.status_code == 401


async def test_mapa_returns_only_vehicles_over_min_minutes(
    client, flota_vehicle, admin_user, monkeypatch
):
    """Vehiculos con >= TALLER_MIN_MINUTES (default 30) aparecen en el mapa."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Forzamos event_ts 45 min en el pasado para superar el umbral de 30 min.
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=45)
    body = _raw_payload(
        exception_id="mm1",
        date=past.strftime("%b, %d, %Y"),
        time=past.strftime("%I:%M:%S %p"),
    )
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] is False

    # El endpoint del mapa requiere auth.
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    assert login.status_code == 200
    response = await client.get("/api/v1/mapa/taller")
    assert response.status_code == 200
    payload = response.json()
    assert any(v["plate"] == "TLK240" for v in payload["vehicles"])
    tlk = next(v for v in payload["vehicles"] if v["plate"] == "TLK240")
    assert tlk["minutes_inside"] >= settings.taller_min_minutes
    assert "enter_ts_local" in tlk
    # Zonas derivadas
    assert any(z["id"] == "b38FC7" for z in payload["zones"])


async def test_mapa_hides_vehicles_under_min_minutes(
    client, flota_vehicle, admin_user, monkeypatch
):
    """Un vehiculo con < TALLER_MIN_MINUTES no aparece en el mapa (req #2)."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Enter "ahora" (sin minutos en el pasado) -> 0 min, debajo del umbral.
    now = datetime.now(tz=timezone.utc)
    response = await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(
            exception_id="hide1",
            date=now.strftime("%b, %d, %Y"),
            time=now.strftime("%I:%M:%S %p"),
        ),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    response = await client.get("/api/v1/mapa/taller")
    assert response.status_code == 200
    payload = response.json()
    assert payload["vehicles"] == []


async def test_mapa_etag_returns_304(
    client, flota_vehicle, admin_user, monkeypatch
):
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    # Crea estado con minutos suficientes via past timestamp.
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=45)
    body = _raw_payload(
        exception_id="etag1",
        date=past.strftime("%b, %d, %Y"),
        time=past.strftime("%I:%M:%S %p"),
    )
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    r1 = await client.get("/api/v1/mapa/taller")
    assert r1.status_code == 200
    etag = r1.headers["ETag"]
    r2 = await client.get("/api/v1/mapa/taller", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.headers["ETag"] == etag


async def test_mapa_excludes_grace_state_from_map(
    client, flota_vehicle, admin_user, monkeypatch
):
    """Vehiculo en gracia NO aparece en el mapa (req #4)."""
    monkeypatch.setenv("GEOTAB_WEBHOOK_API_KEYS", "k")
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=45)
    body_enter = _raw_payload(
        exception_id="gr1",
        date=past.strftime("%b, %d, %Y"),
        time=past.strftime("%I:%M:%S %p"),
    )
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=body_enter,
        headers={"Content-Type": "application/json"},
    )
    await client.post(
        "/api/v1/geotab/taller?api_key=k",
        content=_raw_payload(exception_id="gr2", rule="Salida_taller_-_Proyecto"),
        headers={"Content-Type": "application/json"},
    )
    await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass1!"},
    )
    response = await client.get("/api/v1/mapa/taller")
    payload = response.json()
    assert all(v["plate"] != "TLK240" for v in payload["vehicles"])


# ── Snapshot cache + sweep ─────────────────────────────────────────────


def test_snapshot_cache_reuses_redis(taller_db, flota_vehicle, redis_raw):
    """Dos llamadas seguidas con cache caliente devuelven el mismo ETag."""
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=45)
    body = _raw_payload(
        exception_id="sc1",
        date=past.strftime("%b, %d, %Y"),
        time=past.strftime("%I:%M:%S %p"),
    )
    # Llamada directa al servicio (sin HTTP) para simplificar.
    result = geotab_taller.process_webhook(body)
    assert result["ignored"] is False
    snap1, etag1 = geotab_taller.get_mapa_snapshot_cacheable()
    snap2, etag2 = geotab_taller.get_mapa_snapshot_cacheable()
    assert etag1 == etag2
    # El cache vive en Redis
    assert redis_raw.exists("mapa:snapshot") == 1
    assert redis_raw.exists("mapa:snapshot:hash") == 1


def test_sweep_purges_grace_after_window(flota_vehicle, redis_raw):
    """Sweep elimina estados grace cuya exit_ts supere TALLER_GRACE_HOURS (default 1h)."""
    # Crear estado in
    geotab_taller.process_webhook(_raw_payload(exception_id="sw1"))
    # Forzar estado grace con exit_ts 2h en el pasado (>1h default).
    old_exit = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
    redis_raw.hset(
        "taller:state:TLK240",
        mapping={"status": "grace", "exit_ts": old_exit},
    )
    purged = geotab_taller.sweep_expired_grace()
    assert purged == 1
    assert redis_raw.exists("taller:state:TLK240") == 0
    assert redis_raw.sismember("taller:active", "TLK240") == 0
