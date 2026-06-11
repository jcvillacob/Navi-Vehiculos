"""Tests de los cambios para la integracion con Portal Clientes:

- geotab_device_id capturado en la validacion del vehiculo
- categoria de reglas (operacion / habito_seguro)
- pool de credenciales con rotacion LRU
- endpoint /integration con X-API-Key
"""
from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from app.schemas.vehicle import (
    CustomerDatabaseCredentialCreateRequest,
    CustomerDatabaseCredentialUpdateRequest,
    GeotabRuleCreateRequest,
    GeotabRuleGroupCreateRequest,
    GeotabRuleInspection,
)
from app.services import motor_catalog


def _connect():
    raw = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(raw, row_factory=dict_row)


@pytest.fixture
def motor_tables():
    # Garantiza las tablas y limpia el estado del dominio motor/cliente.
    with _connect() as conn:
        motor_catalog._ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE TABLE
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
    yield


@pytest.fixture
def geotab_db(motor_tables):
    """Cliente + database geotab con su credencial primaria sincronizada."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO customers (name) VALUES ('Cliente Portal') RETURNING id;"
            )
            customer_id = int(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO customer_databases
                    (customer_id, database_name, username, password, connection_type)
                VALUES (%s, 'db_portal', 'primario@navi.co', 'secret1', 'geotab')
                RETURNING id;
                """,
                (customer_id,),
            )
            database_id = int(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO customer_database_credentials
                    (customer_database_id, username, password)
                VALUES (%s, 'primario@navi.co', 'secret1');
                """,
                (database_id,),
            )
        conn.commit()
    return {"customer_id": customer_id, "database_id": database_id}


@pytest.fixture
def vehicle(geotab_db):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicle_motor_assignments
                    (plate, vin, technical_number, customer_id, customer_database_id)
                VALUES ('ABC123', '3HSDJAPR1KN123456', 'TEC-1', %s, %s);
                """,
                (geotab_db["customer_id"], geotab_db["database_id"]),
            )
        conn.commit()
    return {"plate": "ABC123", **geotab_db}


def _vehicle_row(plate: str) -> dict:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM vehicle_motor_assignments WHERE plate = %s;", (plate,)
            )
            return cur.fetchone()


# ── geotab_device_id ──────────────────────────────────────────────────


def test_validate_and_store_captures_device_id(vehicle, monkeypatch):
    monkeypatch.setattr(
        "app.clients.geotab_client.get_cached_device_from_plate",
        lambda plate, cfg, plate_prefix=None: {"id": "b1F2", "licensePlate": plate},
    )
    motor_catalog._validate_and_store_customer_geotab(
        "ABC123", "3HSDJAPR1KN123456", vehicle["database_id"]
    )
    row = _vehicle_row("ABC123")
    assert row["geotab_customer_status"] == "found"
    assert row["geotab_device_id"] == "b1F2"
    assert row["geotab_device_synced_at"] is not None
    assert row["geotab_customer_database_id"] == vehicle["database_id"]


def test_not_found_clears_device_id(vehicle, monkeypatch):
    monkeypatch.setattr(
        "app.clients.geotab_client.get_cached_device_from_plate",
        lambda plate, cfg, plate_prefix=None: {"id": "b1F2"},
    )
    motor_catalog._validate_and_store_customer_geotab("ABC123", None, vehicle["database_id"])
    assert _vehicle_row("ABC123")["geotab_device_id"] == "b1F2"

    monkeypatch.setattr(
        "app.clients.geotab_client.get_cached_device_from_plate",
        lambda plate, cfg, plate_prefix=None: None,
    )
    monkeypatch.setattr(
        "app.clients.geotab_client.get_cached_device_from_vin",
        lambda vin, cfg: None,
    )
    motor_catalog._validate_and_store_customer_geotab("ABC123", None, vehicle["database_id"])
    row = _vehicle_row("ABC123")
    assert row["geotab_customer_status"] == "not_found"
    assert row["geotab_device_id"] is None
    assert row["geotab_device_synced_at"] is None


# ── Categoria de reglas ───────────────────────────────────────────────


def _fake_inspection(rule_id: str) -> GeotabRuleInspection:
    return GeotabRuleInspection(
        exists=True,
        rule_id=rule_id,
        name=f"Regla {rule_id}",
        status="Activa",
        headline="",
    )


def test_create_rule_with_category(geotab_db, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    record = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(rule_id="aRule1", category="habito_seguro"),
    )
    assert record.category == "habito_seguro"

    default_record = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(rule_id="aRule2"),
    )
    assert default_record.category == "operacion"


def test_create_rule_invalid_category_rejected(geotab_db, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    with pytest.raises(ValueError, match="categoria"):
        motor_catalog.create_geotab_rule(
            geotab_db["database_id"],
            GeotabRuleCreateRequest(rule_id="aRule3", category="otra"),
        )


def test_rule_group_rejects_safe_habit_rules(geotab_db, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    safe_rule = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(rule_id="aSafe1", category="habito_seguro"),
    )
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO motor_catalog (technical_number, engine_name) VALUES ('TEC-1', 'Motor X') RETURNING id;"
            )
            motor_id = int(cur.fetchone()["id"])
        conn.commit()

    with pytest.raises(ValueError, match="operacion"):
        motor_catalog.create_geotab_rule_group(
            geotab_db["database_id"],
            GeotabRuleGroupCreateRequest(motor_id=motor_id, rule_record_ids=[safe_rule.id]),
        )


# ── Pool de credenciales ──────────────────────────────────────────────


def test_lru_rotation_alternates_credentials(geotab_db):
    motor_catalog.create_database_credential(
        geotab_db["database_id"],
        CustomerDatabaseCredentialCreateRequest(
            username="secundario@navi.co", password="secret2", label="segunda"
        ),
    )
    seen_users = []
    for _ in range(4):
        cfg, credential_id = motor_catalog.get_geotab_config_for_database(
            geotab_db["database_id"]
        )
        assert credential_id is not None
        seen_users.append(cfg.username)

    # LRU: con 2 credenciales activas deben alternarse.
    assert set(seen_users) == {"primario@navi.co", "secundario@navi.co"}
    assert seen_users[0] != seen_users[1]
    assert seen_users[1] != seen_users[2]


def test_cannot_remove_last_active_credential(geotab_db):
    credentials = motor_catalog.list_database_credentials(geotab_db["database_id"])
    assert len(credentials) == 1
    with pytest.raises(ValueError, match="ultima credencial activa"):
        motor_catalog.delete_database_credential(credentials[0].id)
    with pytest.raises(ValueError, match="ultima credencial activa"):
        motor_catalog.update_database_credential(
            credentials[0].id,
            CustomerDatabaseCredentialUpdateRequest(is_active=False),
        )


def test_rotation_on_auth_failure(geotab_db):
    motor_catalog.create_database_credential(
        geotab_db["database_id"],
        CustomerDatabaseCredentialCreateRequest(username="secundario@navi.co", password="secret2"),
    )

    attempts: list[str] = []

    def flaky(cfg):
        attempts.append(cfg.username)
        if len(attempts) == 1:
            raise Exception("Incorrect MyGeotab login credentials")
        return cfg.username

    result = motor_catalog.call_with_geotab_credentials(geotab_db["database_id"], flaky)
    assert len(attempts) == 2
    assert result == attempts[1]
    assert attempts[0] != attempts[1]

    # La credencial fallida queda marcada con last_auth_error_at.
    failed = [
        cred
        for cred in motor_catalog.list_database_credentials(geotab_db["database_id"])
        if cred.username == attempts[0]
    ][0]
    assert failed.last_auth_error_at is not None


def test_non_auth_errors_propagate_without_rotation(geotab_db):
    attempts: list[str] = []

    def broken(cfg):
        attempts.append(cfg.username)
        raise RuntimeError("otra cosa fallo")

    with pytest.raises(RuntimeError, match="otra cosa"):
        motor_catalog.call_with_geotab_credentials(geotab_db["database_id"], broken)
    assert len(attempts) == 1


# ── Sharing por db fisica (mismo database_name, distinto cliente) ─────


@pytest.fixture
def sibling_geotab_db(geotab_db):
    """Otro cliente con una fila apuntando a la MISMA db fisica (db_portal).

    Usa otra capitalizacion y otra credencial para validar que el sharing es
    por nombre de database (case-insensitive), no por cliente ni username.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO customers (name) VALUES ('Cliente Vigia') RETURNING id;")
            customer_id = int(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO customer_databases
                    (customer_id, database_name, username, password, connection_type)
                VALUES (%s, 'DB_Portal', 'vigia@navi.co', 'secret9', 'geotab')
                RETURNING id;
                """,
                (customer_id,),
            )
            database_id = int(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO customer_database_credentials
                    (customer_database_id, username, password)
                VALUES (%s, 'vigia@navi.co', 'secret9');
                """,
                (database_id,),
            )
        conn.commit()
    return {"customer_id": customer_id, "database_id": database_id}


def test_rules_shared_across_customers_with_same_database(
    geotab_db, sibling_geotab_db, monkeypatch
):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(rule_id="aShared1", category="operacion"),
    )

    # La regla registrada bajo el primer cliente es visible desde la fila del otro.
    sibling_rules = motor_catalog._list_rules_for_database(sibling_geotab_db["database_id"])
    assert [rule.rule_id for rule in sibling_rules] == ["aShared1"]

    # Y no se puede volver a registrar desde la fila hermana.
    with pytest.raises(ValueError, match="comparten"):
        motor_catalog.create_geotab_rule(
            sibling_geotab_db["database_id"],
            GeotabRuleCreateRequest(rule_id="aShared1", category="operacion"),
        )


def test_credential_rotation_spans_sibling_databases(geotab_db, sibling_geotab_db):
    seen_users = set()
    for _ in range(4):
        cfg, credential_id = motor_catalog.get_geotab_config_for_database(
            geotab_db["database_id"]
        )
        assert credential_id is not None
        seen_users.add(cfg.username)
    # El pool abarca la db fisica completa: rota credenciales de ambos clientes.
    assert seen_users == {"primario@navi.co", "vigia@navi.co"}


def test_can_remove_credential_if_sibling_has_active_one(geotab_db, sibling_geotab_db):
    credentials = motor_catalog.list_database_credentials(geotab_db["database_id"])
    assert len(credentials) == 1
    # Es la unica de SU fila, pero la hermana tiene otra activa → se permite.
    motor_catalog.delete_database_credential(credentials[0].id)
    assert motor_catalog.list_database_credentials(geotab_db["database_id"]) == []


# ── Endpoint /integration ─────────────────────────────────────────────


async def test_snapshot_requires_api_key(client, vehicle, monkeypatch):
    monkeypatch.delenv("INTEGRATION_API_KEYS", raising=False)
    response = await client.get("/api/v1/integration/snapshot")
    assert response.status_code == 503

    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    response = await client.get("/api/v1/integration/snapshot")
    assert response.status_code == 401

    response = await client.get(
        "/api/v1/integration/snapshot", headers={"X-API-Key": "clave-mala"}
    )
    assert response.status_code == 401


async def test_snapshot_shape_and_credential_masking(client, vehicle, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    response = await client.get("/api/v1/integration/snapshot", headers=headers)
    assert response.status_code == 200
    payload = response.json()

    assert payload["generated_at"]
    customer = next(c for c in payload["customers"] if c["name"] == "Cliente Portal")
    database = customer["databases"][0]
    assert database["database_name"] == "db_portal"
    assert database["database_key"] == "db_portal"
    assert database["connection_type"] == "geotab"
    assert "plate_prefix" in database["provider_config"]
    assert database["credentials"][0]["username"] == "primario@navi.co"
    # Sin include_credentials los passwords van enmascarados.
    assert database["credentials"][0]["password"] == "********"

    vehicle_row = next(v for v in payload["vehicles"] if v["plate"] == "ABC123")
    assert vehicle_row["vin"] == "3HSDJAPR1KN123456"
    assert vehicle_row["customer_id"] == vehicle["customer_id"]
    assert "geotab_device_id" in vehicle_row

    response = await client.get(
        "/api/v1/integration/snapshot",
        headers=headers,
        params={"include_credentials": "true"},
    )
    payload = response.json()
    customer = next(c for c in payload["customers"] if c["name"] == "Cliente Portal")
    assert customer["databases"][0]["credentials"][0]["password"] == "secret1"


async def test_snapshot_incremental_since(client, vehicle, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    response = await client.get(
        "/api/v1/integration/snapshot",
        headers=headers,
        params={"since": "2050-01-01T00:00:00Z"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["customers"] == []
    assert payload["vehicles"] == []

    response = await client.get(
        "/api/v1/integration/snapshot",
        headers=headers,
        params={"since": "no-es-fecha"},
    )
    assert response.status_code == 422


async def test_vehicles_pagination(client, vehicle, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    response = await client.get(
        "/api/v1/integration/vehicles",
        headers=headers,
        params={"limit": 1, "offset": 0},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["vehicles"][0]["plate"] == "ABC123"
