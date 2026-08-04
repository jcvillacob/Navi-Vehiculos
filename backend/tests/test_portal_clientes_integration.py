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
    GeotabRuleApplicationUpdateRequest,
    GeotabRuleCreateRequest,
    GeotabRuleGroupCreateRequest,
    GeotabRuleInspection,
)
from app.services import availability_store, integration_export, motor_catalog, rendimientos
from app.services.rule_bands import suggest_band, suggest_is_descenso


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
def rule_motor_id(geotab_db):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO motor_catalog (technical_number, engine_name)
                VALUES ('TEC-RULE', 'Motor Reglas')
                RETURNING id;
                """
            )
            motor_id = int(cur.fetchone()["id"])
        conn.commit()
    return motor_id


@pytest.fixture
def vehicle(geotab_db):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vehicle_motor_assignments
                    (
                        plate,
                        vin,
                        technical_number,
                        marketing_model_name,
                        service_model_name,
                        customer_id,
                        customer_database_id
                    )
                VALUES (
                    'ABC123',
                    '3HSDJAPR1KN123456',
                    'TEC-1',
                    'L9 370',
                    'L9 CM2450 L126B',
                    %s,
                    %s
                );
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


def _insert_geotab_binding(
    plate: str,
    database_id: int,
    provider_vehicle_id: str,
    *,
    is_manual: bool = False,
    updated_at: str | None = None,
) -> None:
    with _connect() as conn:
        rendimientos._ensure_performance_tables(conn)
        with conn.cursor() as cur:
            updated_sql = "%s::timestamptz" if updated_at else "NOW()"
            cur.execute(
                f"""
                INSERT INTO vehicle_provider_bindings
                    (plate, customer_database_id, provider, provider_vehicle_id,
                     binding_status, is_manual, updated_at)
                VALUES (%s, %s, 'geotab', %s, 'resolved', %s, {updated_sql});
                """,
                (
                    (plate, database_id, provider_vehicle_id, is_manual, updated_at)
                    if updated_at
                    else (plate, database_id, provider_vehicle_id, is_manual)
                ),
            )
        conn.commit()


def _exported_vehicle(plate: str) -> dict:
    payload = integration_export.export_vehicles()
    return next(v for v in payload["vehicles"] if v["plate"] == plate)


def test_snapshot_uses_geotab_binding_as_device_id(vehicle):
    # Sin geotab_device_id en la columna, el snapshot toma el "ID externo" del
    # binding geotab (provider_vehicle_id) como device id para Portal Clientes.
    _insert_geotab_binding("ABC123", vehicle["database_id"], "GEO-DEV-1")
    assert _exported_vehicle("ABC123")["geotab_device_id"] == "GEO-DEV-1"


def test_snapshot_prefers_binding_over_validated_column(vehicle, monkeypatch):
    # Columna validada y binding difieren -> gana el binding (ID externo manual).
    monkeypatch.setattr(
        "app.clients.geotab_client.get_cached_device_from_plate",
        lambda plate, cfg, plate_prefix=None: {"id": "COL-DEV"},
    )
    motor_catalog._validate_and_store_customer_geotab(
        "ABC123", "3HSDJAPR1KN123456", vehicle["database_id"]
    )
    assert _vehicle_row("ABC123")["geotab_device_id"] == "COL-DEV"

    _insert_geotab_binding("ABC123", vehicle["database_id"], "BIND-DEV", is_manual=True)
    assert _exported_vehicle("ABC123")["geotab_device_id"] == "BIND-DEV"


def test_incremental_vehicles_include_geotab_binding_updates(vehicle):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vehicle_motor_assignments
                SET updated_at = '2026-01-01T00:00:00Z'
                WHERE plate = 'ABC123';
                """
            )
        conn.commit()

    _insert_geotab_binding(
        "ABC123",
        vehicle["database_id"],
        "BIND-NEW",
        updated_at="2026-03-01T00:00:00Z",
    )

    payload = integration_export.export_vehicles(since="2026-02-01T00:00:00Z")
    vehicle_row = next(v for v in payload["vehicles"] if v["plate"] == "ABC123")

    assert vehicle_row["geotab_device_id"] == "BIND-NEW"
    assert vehicle_row["updated_at"].startswith("2026-03-01T00:00:00")


def _set_customer_category(customer_id: int, category: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE customers SET category = %s WHERE id = %s;",
                (category, customer_id),
            )
        conn.commit()


def test_snapshot_exports_customer_category_as_effective(vehicle):
    # Sin override propio, la categoría del cliente es la efectiva del vehículo.
    _set_customer_category(vehicle["customer_id"], "Flota Administrada")
    assert _exported_vehicle("ABC123")["category"] == "Flota Administrada"


def test_snapshot_vehicle_category_override_wins(vehicle):
    # El override del vehículo ('Ninguna') gana sobre la categoría del cliente.
    _set_customer_category(vehicle["customer_id"], "Flota Administrada")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE vehicle_motor_assignments SET category = 'Ninguna' WHERE plate = 'ABC123';"
            )
        conn.commit()
    assert _exported_vehicle("ABC123")["category"] == "Ninguna"


# ── Categoria de reglas ───────────────────────────────────────────────


def _fake_inspection(rule_id: str) -> GeotabRuleInspection:
    return GeotabRuleInspection(
        exists=True,
        rule_id=rule_id,
        name=f"Regla {rule_id}",
        status="Activa",
        headline="",
    )


def test_create_rule_with_category(geotab_db, rule_motor_id, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    record = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aRule1",
            category="habito_seguro",
            description="Giros bruscos",
        ),
    )
    assert record.category == "habito_seguro"
    assert record.applications[0].category == "habito_seguro"
    assert record.applications[0].description == "Giros bruscos"

    default_record = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(rule_id="aRule2", motor_id=rule_motor_id),
    )
    assert default_record.category == "operacion"
    assert default_record.applications[0].category == "operacion"
    assert default_record.applications[0].motor_id == rule_motor_id


def test_create_operation_rule_requires_motor(geotab_db):
    with pytest.raises(ValueError, match="motor"):
        motor_catalog.create_geotab_rule(
            geotab_db["database_id"],
            GeotabRuleCreateRequest(rule_id="aRuleWithoutMotor"),
        )


def test_database_rejects_operation_application_without_motor(geotab_db):
    with pytest.raises(psycopg.errors.CheckViolation):
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO geotab_rules (database_id, name, rule_id, category)
                    VALUES (%s, 'Regla invalida', 'aInvalidScope', 'operacion')
                    RETURNING id;
                    """,
                    (geotab_db["database_id"],),
                )
                rule_record_id = int(cur.fetchone()["id"])
                cur.execute(
                    """
                    INSERT INTO geotab_rule_applications (
                        geotab_rule_id, category, motor_id
                    )
                    VALUES (%s, 'operacion', NULL);
                    """,
                    (rule_record_id,),
                )
            conn.commit()


def test_deleted_group_does_not_export_unassigned_operation(
    geotab_db, rule_motor_id, monkeypatch
):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    rule = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aReassign1",
            category="operacion",
            motor_id=rule_motor_id,
        ),
    )
    group = motor_catalog.create_geotab_rule_group(
        geotab_db["database_id"],
        GeotabRuleGroupCreateRequest(
            motor_id=rule_motor_id,
            rule_record_ids=[rule.id],
        ),
    )

    motor_catalog.delete_geotab_rule_group(group.id)

    listed_rule = next(
        current
        for current in motor_catalog._list_rules_for_database(geotab_db["database_id"])
        if current.id == rule.id
    )
    assert listed_rule.applications == []

    snapshot = integration_export.export_customers(include_credentials=False)
    customer = next(
        current
        for current in snapshot["customers"]
        if current["id"] == geotab_db["customer_id"]
    )
    database = next(
        current
        for current in customer["databases"]
        if current["id"] == geotab_db["database_id"]
    )
    assert all(current["rule_id"] != "aReassign1" for current in database["rules"])


def test_create_rule_invalid_category_rejected(geotab_db, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    with pytest.raises(ValueError, match="categoria"):
        motor_catalog.create_geotab_rule(
            geotab_db["database_id"],
            GeotabRuleCreateRequest(rule_id="aRule3", category="otra"),
        )


@pytest.mark.parametrize(
    "description",
    [
        "Excesos de velocidad",
        "Giros bruscos",
        "Excesos de RPM",
        "Frenadas bruscas",
        "Baches o Resaltos fuertes",
        "Aceleraciones bruscas",
    ],
)
def test_safe_habit_description_enum(description):
    assert motor_catalog._normalize_safe_habit_description(description) == description


def test_safe_habit_description_rejects_missing_or_unknown():
    assert motor_catalog._normalize_safe_habit_description(None) is None
    with pytest.raises(ValueError, match="clasificacion"):
        motor_catalog._normalize_safe_habit_description("Evento general")
    with pytest.raises(ValueError, match="seleccionar"):
        motor_catalog.create_geotab_rule(
            1,
            GeotabRuleCreateRequest(
                rule_id="aSafeWithoutDescription",
                category="habito_seguro",
            ),
        )


def test_exceso_rpm_requires_motor(geotab_db, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    with pytest.raises(ValueError, match="RPM"):
        motor_catalog.create_geotab_rule(
            geotab_db["database_id"],
            GeotabRuleCreateRequest(
                rule_id="aRpm1",
                category="habito_seguro",
                description="Excesos de RPM",
            ),
        )


def test_rule_group_rejects_safe_habit_rules(geotab_db, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    safe_rule = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aSafe1",
            category="habito_seguro",
            description="Frenadas bruscas",
        ),
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


def test_exceso_rpm_band_derives_safe_habit_application(geotab_db, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO motor_catalog (technical_number, engine_name) VALUES ('TEC-X11', 'X11') RETURNING id;"
            )
            motor_id = int(cur.fetchone()["id"])
        conn.commit()

    rpm_rule = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aRpmX11",
            category="operacion",
            motor_id=motor_id,
            band="exceso_rpm",
        ),
    )
    motor_catalog.create_geotab_rule_group(
        geotab_db["database_id"],
        GeotabRuleGroupCreateRequest(motor_id=motor_id, rule_record_ids=[rpm_rule.id]),
    )
    assert motor_catalog._list_rule_groups_for_database(geotab_db["database_id"])[0].rules

    derived = next(
        rule
        for rule in motor_catalog._list_rules_for_database(geotab_db["database_id"])
        if rule.rule_id == "aRpmX11"
    )
    assert {
        (
            application.category,
            application.motor_id,
            application.event_type,
            application.band,
            application.description,
        )
        for application in derived.applications
    } == {
        ("operacion", motor_id, None, "exceso_rpm", None),
        ("habito_seguro", motor_id, "exceso_rpm", None, "Excesos de RPM"),
    }

    operation = next(app for app in derived.applications if app.category == "operacion")
    updated = motor_catalog.update_geotab_rule_application(
        operation.id,
        GeotabRuleApplicationUpdateRequest(band="rango_potencia"),
    )
    assert all(app.event_type != "exceso_rpm" for app in updated.applications)


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
    geotab_db, sibling_geotab_db, rule_motor_id, monkeypatch
):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )
    motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aShared1", category="operacion", motor_id=rule_motor_id
        ),
    )

    # La regla registrada bajo el primer cliente es visible desde la fila del otro.
    sibling_rules = motor_catalog._list_rules_for_database(sibling_geotab_db["database_id"])
    assert [rule.rule_id for rule in sibling_rules] == ["aShared1"]

    # Registrar de nuevo la misma regla desde la fila hermana no duplica la regla
    # fisica; devuelve el mismo registro y conserva la aplicacion.
    existing = motor_catalog.create_geotab_rule(
        sibling_geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aShared1", category="operacion", motor_id=rule_motor_id
        ),
    )
    assert existing.rule_id == "aShared1"
    assert len(existing.applications) == 1
    assert existing.applications[0].motor_id == rule_motor_id


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
    assert vehicle_row["marketing_model_name"] == "L9 370"
    assert vehicle_row["service_model_name"] == "L9 CM2450 L126B"
    # vocacional siempre presente y booleano (default false).
    assert vehicle_row["vocacional"] is False

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


async def test_snapshot_exposes_motor_type(client, vehicle, monkeypatch):
    """motor_type = engine_name de la familia de motor (ver REGLAS_POR_MOTOR).

    - vehiculo: engine_name del motor cuyo technical_number coincide.
    - regla 'operacion': engine_name de su motor (siempre obligatorio).
    - regla 'habito_seguro' global: motor_type NULL.
    """
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", lambda db_id, rule_id: _fake_inspection(rule_id)
    )

    # El motor cuyo technical_number coincide con el del vehiculo 'ABC123' (TEC-1).
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO motor_catalog (technical_number, engine_name) "
                "VALUES ('TEC-1', 'ISD') RETURNING id;"
            )
            motor_id = int(cur.fetchone()["id"])
        conn.commit()

    op_rule = motor_catalog.create_geotab_rule(
        vehicle["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aOp1", category="operacion", motor_id=motor_id
        ),
    )
    motor_catalog.create_geotab_rule(
        vehicle["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aSafe1",
            category="habito_seguro",
            description="Excesos de velocidad",
        ),
    )
    rpm_rule = motor_catalog.create_geotab_rule(
        vehicle["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aRpm1",
            category="operacion",
            motor_id=motor_id,
            band="exceso_rpm",
        ),
    )
    motor_catalog.create_geotab_rule_group(
        vehicle["database_id"],
        GeotabRuleGroupCreateRequest(
            motor_id=motor_id,
            rule_record_ids=[op_rule.id, rpm_rule.id],
        ),
    )

    response = await client.get("/api/v1/integration/snapshot", headers=headers)
    assert response.status_code == 200
    payload = response.json()

    vehicle_row = next(v for v in payload["vehicles"] if v["plate"] == "ABC123")
    assert vehicle_row["motor_type"] == "ISD"

    customer = next(c for c in payload["customers"] if c["name"] == "Cliente Portal")
    exported_rules = customer["databases"][0]["rules"]
    rules = {r["rule_id"]: r for r in exported_rules}
    assert rules["aOp1"]["motor_type"] == "ISD"
    assert rules["aSafe1"]["motor_type"] is None
    assert rules["aSafe1"]["description"] == "Excesos de velocidad"
    rpm_rows = [row for row in exported_rules if row["rule_id"] == "aRpm1"]
    assert {row["category"] for row in rpm_rows} == {"operacion", "habito_seguro"}
    assert {row["motor_type"] for row in rpm_rows} == {"ISD"}
    assert next(row for row in rpm_rows if row["category"] == "operacion")["band"] == "exceso_rpm"
    safe_rpm = next(row for row in rpm_rows if row["category"] == "habito_seguro")
    assert safe_rpm["event_type"] == "exceso_rpm"
    assert safe_rpm["description"] == "Excesos de RPM"


# ── Bandas de RPM explicitas ──────────────────────────────────────────


def _fake_inspection_named(name: str):
    def _resolve(db_id, rule_id):
        return GeotabRuleInspection(
            exists=True, rule_id=rule_id, name=name, status="Activa", headline=""
        )

    return _resolve


def test_create_rule_with_band(geotab_db, rule_motor_id, monkeypatch):
    monkeypatch.setattr(
        motor_catalog,
        "resolve_geotab_rule",
        _fake_inspection_named("Rango Economico Descenso"),
    )
    record = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aBand1",
            category="operacion",
            motor_id=rule_motor_id,
            band="rango_economico",
            is_descenso=True,
        ),
    )
    app = record.applications[0]
    assert app.band == "rango_economico"
    assert app.is_descenso is True

    # operacion sin banda -> None.
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", _fake_inspection_named("Regla rara")
    )
    no_band = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aBand2", category="operacion", motor_id=rule_motor_id
        ),
    )
    assert no_band.applications[0].band is None
    assert no_band.applications[0].is_descenso is False

    # habito_seguro nunca lleva banda aunque se envie.
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", _fake_inspection_named("Frenada")
    )
    habito = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aBand3",
            category="habito_seguro",
            description="Frenadas bruscas",
            band="rango_bajo",
        ),
    )
    assert habito.applications[0].band is None


def test_create_rule_band_suggestion_in_record(geotab_db, rule_motor_id, monkeypatch):
    monkeypatch.setattr(
        motor_catalog,
        "resolve_geotab_rule",
        _fake_inspection_named("Rango Potencia Ineficiente X11"),
    )
    record = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aSug1", category="operacion", motor_id=rule_motor_id
        ),
    )
    app = record.applications[0]
    # La banda real no se seteo (None), pero el sugeridor la propone.
    assert app.band is None
    assert app.suggested_band == "rango_potencia_ineficiente"


def test_rule_band_check_constraints(geotab_db, rule_motor_id, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", _fake_inspection_named("Rango Bajo")
    )
    record = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aChk1", category="operacion", motor_id=rule_motor_id
        ),
    )
    application_id = record.applications[0].id

    # ralenti + descenso -> rechazado por CHECK.
    with pytest.raises(psycopg.errors.CheckViolation):
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE geotab_rule_applications "
                    "SET band = 'ralenti', is_descenso = TRUE WHERE id = %s;",
                    (application_id,),
                )
            conn.commit()

    # is_descenso sin band -> rechazado por CHECK.
    with pytest.raises(psycopg.errors.CheckViolation):
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE geotab_rule_applications "
                    "SET band = NULL, is_descenso = TRUE WHERE id = %s;",
                    (application_id,),
                )
            conn.commit()


def test_update_rule_application_band(geotab_db, rule_motor_id, monkeypatch):
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", _fake_inspection_named("Regla rara")
    )
    record = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aUpd1", category="operacion", motor_id=rule_motor_id
        ),
    )
    application_id = record.applications[0].id
    assert record.applications[0].band is None

    updated = motor_catalog.update_geotab_rule_application(
        application_id,
        GeotabRuleApplicationUpdateRequest(band="rango_potencia", is_descenso=False),
    )
    app = next(a for a in updated.applications if a.id == application_id)
    assert app.band == "rango_potencia"

    # No se permite banda sobre aplicaciones habito_seguro.
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", _fake_inspection_named("Frenada")
    )
    safe = motor_catalog.create_geotab_rule(
        geotab_db["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aUpd2",
            category="habito_seguro",
            description="Frenadas bruscas",
        ),
    )
    safe_app_id = safe.applications[0].id
    with pytest.raises(ValueError, match="operacion"):
        motor_catalog.update_geotab_rule_application(
            safe_app_id,
            GeotabRuleApplicationUpdateRequest(band="rango_bajo"),
        )

    edited_safe = motor_catalog.update_geotab_rule_application(
        safe_app_id,
        GeotabRuleApplicationUpdateRequest(description="Giros bruscos"),
    )
    edited_app = next(a for a in edited_safe.applications if a.id == safe_app_id)
    assert edited_app.description == "Giros bruscos"
    assert edited_app.event_type is None
    assert edited_app.motor_id is None

    with pytest.raises(ValueError, match="RPM"):
        motor_catalog.update_geotab_rule_application(
            safe_app_id,
            GeotabRuleApplicationUpdateRequest(description="Excesos de RPM"),
        )



async def test_snapshot_includes_band_fields(
    client, vehicle, rule_motor_id, monkeypatch
):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    monkeypatch.setattr(
        motor_catalog,
        "resolve_geotab_rule",
        _fake_inspection_named("Rango Economico Descenso"),
    )
    motor_catalog.create_geotab_rule(
        vehicle["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aBandOp",
            category="operacion",
            motor_id=rule_motor_id,
            band="rango_economico",
            is_descenso=True,
        ),
    )
    monkeypatch.setattr(
        motor_catalog, "resolve_geotab_rule", _fake_inspection_named("Regla rara")
    )
    motor_catalog.create_geotab_rule(
        vehicle["database_id"],
        GeotabRuleCreateRequest(
            rule_id="aBandNone", category="operacion", motor_id=rule_motor_id
        ),
    )

    response = await client.get("/api/v1/integration/snapshot", headers=headers)
    assert response.status_code == 200
    customer = next(c for c in response.json()["customers"] if c["name"] == "Cliente Portal")
    rules = {r["rule_id"]: r for r in customer["databases"][0]["rules"]}

    assert rules["aBandOp"]["band"] == "rango_economico"
    assert rules["aBandOp"]["is_descenso"] is True
    # Serializa NULL sin romper el formato.
    assert rules["aBandNone"]["band"] is None
    assert rules["aBandNone"]["is_descenso"] is False


def test_backfill_operacion_bands(geotab_db, rule_motor_id):
    """Reproduce el backfill de la migracion 20260724_0001 sobre nombres reales."""
    seeds = [
        ("Rango Bajo X11", "rango_bajo", False),
        ("Rango Consumo X11", "rango_potencia_ineficiente", False),
        ("Potencia Eficiente", "rango_potencia", False),
        ("Rango Económico Descenso", "rango_economico", True),
        ("Regla sin palabra clave", None, False),
    ]
    app_ids: list[int] = []
    with _connect() as conn:
        with conn.cursor() as cur:
            for idx, (name, _band, _desc) in enumerate(seeds):
                cur.execute(
                    "INSERT INTO geotab_rules (database_id, name, rule_id, category) "
                    "VALUES (%s, %s, %s, 'operacion') RETURNING id;",
                    (geotab_db["database_id"], name, f"bk{idx}"),
                )
                rule_id = int(cur.fetchone()["id"])
                cur.execute(
                    "INSERT INTO geotab_rule_applications "
                    "(geotab_rule_id, category, motor_id) "
                    "VALUES (%s, 'operacion', %s) RETURNING id;",
                    (rule_id, rule_motor_id),
                )
                app_ids.append(int(cur.fetchone()["id"]))
        conn.commit()

    # Backfill identico al de la migracion.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT gra.id AS application_id, gr.name AS rule_name
                FROM geotab_rule_applications gra
                INNER JOIN geotab_rules gr ON gr.id = gra.geotab_rule_id
                WHERE gra.category = 'operacion' AND gra.band IS NULL
                  AND gra.id = ANY(%s);
                """,
                (app_ids,),
            )
            rows = cur.fetchall()
            for row in rows:
                band = suggest_band(row["rule_name"])
                if band is None:
                    continue
                cur.execute(
                    "UPDATE geotab_rule_applications SET band = %s, is_descenso = %s "
                    "WHERE id = %s;",
                    (band, suggest_is_descenso(row["rule_name"], band), row["application_id"]),
                )
        conn.commit()

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, band, is_descenso FROM geotab_rule_applications "
                "WHERE id = ANY(%s) ORDER BY id;",
                (app_ids,),
            )
            result = {row["id"]: row for row in cur.fetchall()}

    for app_id, (_name, expected_band, expected_desc) in zip(app_ids, seeds):
        assert result[app_id]["band"] == expected_band
        assert result[app_id]["is_descenso"] is expected_desc


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
    assert payload["vehicles"][0]["marketing_model_name"] == "L9 370"
    assert payload["vehicles"][0]["service_model_name"] == "L9 CM2450 L126B"


# ── Endpoint /integration/availability ────────────────────────────────


@pytest.fixture
def availability_data(motor_tables):
    """Cliente real + cliente sistema con placas y filas de disponibilidad."""
    availability_store._ensure_availability_table()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE monthly_vehicle_availability RESTART IDENTITY CASCADE;")

            cur.execute(
                "INSERT INTO customers (name) VALUES ('Cliente Avail') RETURNING id;"
            )
            normal_customer_id = int(cur.fetchone()["id"])
            cur.execute(
                "INSERT INTO customers (name) VALUES (%s) RETURNING id;",
                (availability_store._SYSTEM_CUSTOMER_NAME,),
            )
            system_customer_id = int(cur.fetchone()["id"])

            cur.execute(
                """
                INSERT INTO customer_databases
                    (customer_id, database_name, username, password, connection_type)
                VALUES (%s, 'db_avail', 'avail@navi.co', 'secret', 'geotab')
                RETURNING id;
                """,
                (normal_customer_id,),
            )
            normal_database_id = int(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO customer_databases
                    (customer_id, database_name, username, password, connection_type)
                VALUES (%s, 'db_system', 'system@navi.co', 'secret', 'geotab')
                RETURNING id;
                """,
                (system_customer_id,),
            )
            system_database_id = int(cur.fetchone()["id"])

            cur.execute(
                """
                INSERT INTO vehicle_motor_assignments
                    (plate, technical_number, customer_id, customer_database_id)
                VALUES
                    ('AVL001', 'TEC-A1', %s, %s),
                    ('AVL002', 'TEC-A2', %s, %s),
                    ('SYS001', 'TEC-S1', %s, %s);
                """,
                (
                    normal_customer_id,
                    normal_database_id,
                    normal_customer_id,
                    normal_database_id,
                    system_customer_id,
                    system_database_id,
                ),
            )

            cur.execute(
                """
                INSERT INTO monthly_vehicle_availability
                    (plate, period_month, calculation_status,
                     project_availability_pct, h_total, h_no_disp,
                     orders_considered, mttr_hours, orders_closed,
                     last_calculated_at, source)
                VALUES
                    ('AVL001', '2026-01', 'calculated',
                     98.5, 744.0, 11.16, 3, 3.72, 3,
                     '2026-01-15T10:00:00+00:00', 'cloudfleet'),
                    ('AVL002', '2026-02', 'no_orders',
                     100.0, 672.0, 0.0, 0, NULL, 0,
                     '2026-02-20T10:00:00+00:00', 'cloudfleet'),
                    ('SYS001', '2026-01', 'calculated',
                     95.0, 744.0, 37.2, 5, 7.44, 5,
                     '2026-01-10T10:00:00+00:00', 'cloudfleet');
                """
            )
        conn.commit()

    return {
        "normal_customer_id": normal_customer_id,
        "system_customer_id": system_customer_id,
        "normal_database_id": normal_database_id,
        "system_database_id": system_database_id,
    }


async def test_availability_shape_and_excludes_system(client, availability_data, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    response = await client.get(
        "/api/v1/integration/availability",
        headers=headers,
        params={"month_from": "2026-01", "month_to": "2026-02"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["month_from"] == "2026-01"
    assert payload["month_to"] == "2026-02"
    assert payload["since"] is None
    assert payload["limit"] == 500
    assert payload["offset"] == 0
    assert payload["total"] == 2
    assert len(payload["rows"]) == 2

    plates = {r["plate"] for r in payload["rows"]}
    assert plates == {"AVL001", "AVL002"}
    assert "SYS001" not in plates

    row = next(r for r in payload["rows"] if r["plate"] == "AVL001")
    assert row["period_month"] == "2026-01"
    assert row["calculation_status"] == "calculated"
    assert row["project_availability_pct"] == 98.5
    assert row["h_total"] == 744.0
    assert row["h_no_disp"] == 11.16
    assert row["orders_considered"] == 3
    assert row["mttr_hours"] == 3.72
    assert row["orders_closed"] == 3
    assert row["customer_id"] == availability_data["normal_customer_id"]
    assert row["customer_name"] == "Cliente Avail"
    assert row["last_calculated_at"]


async def test_availability_filter_by_month(client, availability_data, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    response = await client.get(
        "/api/v1/integration/availability",
        headers=headers,
        params={"month_from": "2026-02", "month_to": "2026-02"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 1
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["plate"] == "AVL002"
    assert payload["rows"][0]["period_month"] == "2026-02"


async def test_availability_incremental_since(client, availability_data, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    response = await client.get(
        "/api/v1/integration/availability",
        headers=headers,
        params={
            "month_from": "2026-01",
            "month_to": "2026-02",
            "since": "2026-01-16T00:00:00Z",
        },
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 1
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["plate"] == "AVL002"
    assert payload["rows"][0]["last_calculated_at"].startswith("2026-02-20T10:00:00")


async def test_availability_invalid_month_returns_422(client, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    response = await client.get(
        "/api/v1/integration/availability",
        headers=headers,
        params={"month_from": "2026-13", "month_to": "2026-02"},
    )
    assert response.status_code == 422

    response = await client.get(
        "/api/v1/integration/availability",
        headers=headers,
        params={"month_from": "2026/01", "month_to": "2026-02"},
    )
    assert response.status_code == 422


# ── Endpoint /integration/taller-ordenes ──────────────────────────────


def _fake_taller_orders_payload() -> dict:
    """Payload sintetico que simula la salida de get_active_orders."""
    orders = [
        {
            "order_number": "OT-001",
            "plate": "ABC123",
            "customer_id": 1,
            "customer_name": "Cliente Uno",
            "type": "preventive",
            "status": "opened",
            "status_indicator": "on_time",
            "time_status_text": "En tiempo",
            "days_elapsed": 2,
            "pending_closure_days": None,
            "maintenance_labels": [],
            "has_labels": False,
        },
        {
            "order_number": "OT-002",
            "plate": "DEF456",
            "customer_id": 1,
            "customer_name": "Cliente Uno",
            "type": "corrective",
            "status": "opened",
            "status_indicator": "overdue",
            "time_status_text": "Excedido",
            "days_elapsed": 10,
            "pending_closure_days": None,
            "maintenance_labels": [],
            "has_labels": False,
        },
        {
            "order_number": "OT-003",
            "plate": "GHI789",
            "customer_id": 2,
            "customer_name": "Cliente Dos",
            "type": "corrective",
            "status": "ontechnicalcompletion",
            "status_indicator": "pending_closure",
            "time_status_text": "Pendiente cierre",
            "days_elapsed": 5,
            "pending_closure_days": 10,
            "maintenance_labels": ["repuesto_especial"],
            "has_labels": True,
        },
    ]
    return {
        "generated_at": "2026-07-12T10:00:00",
        "summary": {
            "total_active": 3,
            "on_time": 1,
            "about_to_expire": 0,
            "overdue": 1,
            "pending_closure": 1,
            "pending_closure_7d": 1,
            "pending_closure_30d": 0,
            "con_etiquetas": 1,
        },
        "orders": orders,
    }


async def test_taller_ordenes_returns_all_without_customer_id(client, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}
    monkeypatch.setattr(
        integration_export, "get_active_orders", lambda force_refresh=False: _fake_taller_orders_payload()
    )

    response = await client.get("/api/v1/integration/taller-ordenes", headers=headers)
    assert response.status_code == 200
    payload = response.json()

    assert payload["generated_at"] == "2026-07-12T10:00:00"
    assert payload["customer_id"] is None
    assert len(payload["orders"]) == 3
    assert payload["summary"]["total_active"] == 3
    assert payload["summary"]["con_etiquetas"] == 1

    plates = {o["plate"] for o in payload["orders"]}
    assert plates == {"ABC123", "DEF456", "GHI789"}


async def test_taller_ordenes_filters_by_customer_id_and_recalculates_summary(
    client, monkeypatch
):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}
    monkeypatch.setattr(
        integration_export, "get_active_orders", lambda force_refresh=False: _fake_taller_orders_payload()
    )

    response = await client.get(
        "/api/v1/integration/taller-ordenes",
        headers=headers,
        params={"customer_id": 1},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["customer_id"] == 1
    assert len(payload["orders"]) == 2
    assert payload["summary"]["total_active"] == 2
    assert payload["summary"]["on_time"] == 1
    assert payload["summary"]["overdue"] == 1
    assert payload["summary"]["pending_closure"] == 0
    assert payload["summary"]["pending_closure_7d"] == 0
    assert payload["summary"]["con_etiquetas"] == 0

    response = await client.get(
        "/api/v1/integration/taller-ordenes",
        headers=headers,
        params={"customer_id": 2},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["customer_id"] == 2
    assert len(payload["orders"]) == 1
    assert payload["orders"][0]["order_number"] == "OT-003"
    assert payload["summary"]["total_active"] == 1
    assert payload["summary"]["pending_closure"] == 1
    assert payload["summary"]["pending_closure_7d"] == 1
    assert payload["summary"]["con_etiquetas"] == 1


async def test_taller_ordenes_returns_503_on_cloudfleet_error(client, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEYS", "clave-portal")
    headers = {"X-API-Key": "clave-portal"}

    def _raise_cloudfleet(*, force_refresh=False):
        raise integration_export.CloudFleetUnavailableError("CloudFleet caido")

    monkeypatch.setattr(integration_export, "get_active_orders", _raise_cloudfleet)

    response = await client.get("/api/v1/integration/taller-ordenes", headers=headers)
    assert response.status_code == 503
    assert "CloudFleet" in response.json()["detail"]
