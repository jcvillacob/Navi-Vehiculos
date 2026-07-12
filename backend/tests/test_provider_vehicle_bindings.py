from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

TEST_DATABASE_URL = os.environ.get("DATABASE_URL_TEST", "").strip()


def _admin_database_url(database_url: str) -> str:
    parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://", 1))
    return urlunparse(parsed._replace(path="/postgres"))


@pytest.fixture
def db_connection():
    raw_url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL_TEST", "").strip()
    parsed = urlparse(raw_url.replace("postgresql+psycopg://", "postgresql://", 1))
    db_url = urlunparse(parsed)
    conn = psycopg.connect(db_url, row_factory=dict_row)
    yield conn
    conn.close()


@pytest.fixture
def geotab_database(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (name)
            VALUES ('Test Geotab Customer')
            ON CONFLICT DO NOTHING
            RETURNING id;
            """,
        )
        row = cur.fetchone()
        customer_id = row["id"] if row else None

    if customer_id is None:
        with db_connection.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE name = 'Test Geotab Customer' LIMIT 1;")
            row = cur.fetchone()
            customer_id = row["id"]

    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_databases (customer_id, database_name, username, password, connection_type)
            VALUES (%s, 'test_geotab_db', 'test_user', 'test_pass', 'geotab')
            ON CONFLICT DO NOTHING
            RETURNING id;
            """,
            (customer_id,),
        )
        row = cur.fetchone()
        db_id = row["id"] if row else None

    if db_id is None:
        with db_connection.cursor() as cur:
            cur.execute(
                "SELECT id FROM customer_databases WHERE customer_id = %s AND connection_type = 'geotab' LIMIT 1;",
                (customer_id,),
            )
            row = cur.fetchone()
            db_id = row["id"]

    db_connection.commit()
    yield db_id

    with db_connection.cursor() as cur:
        cur.execute("DELETE FROM vehicle_provider_bindings WHERE customer_database_id = %s;", (db_id,))
        cur.execute("DELETE FROM customer_databases WHERE id = %s;", (db_id,))
        cur.execute("DELETE FROM customers WHERE id = %s;", (customer_id,))
    db_connection.commit()


@pytest.fixture
def frotcom_database(db_connection):
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (name)
            VALUES ('Test Frotcom Customer')
            ON CONFLICT DO NOTHING
            RETURNING id;
            """,
        )
        row = cur.fetchone()
        customer_id = row["id"] if row else None

    if customer_id is None:
        with db_connection.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE name = 'Test Frotcom Customer' LIMIT 1;")
            row = cur.fetchone()
            customer_id = row["id"]

    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO customer_databases (customer_id, database_name, username, password, connection_type, access_url)
            VALUES (%s, 'test_frotcom_db', 'frotcom_user', 'frotcom_pass', 'frotcom', 'https://frotcom.example.com')
            ON CONFLICT DO NOTHING
            RETURNING id;
            """,
            (customer_id,),
        )
        row = cur.fetchone()
        db_id = row["id"] if row else None

    if db_id is None:
        with db_connection.cursor() as cur:
            cur.execute(
                "SELECT id FROM customer_databases WHERE customer_id = %s AND connection_type = 'frotcom' LIMIT 1;",
                (customer_id,),
            )
            row = cur.fetchone()
            db_id = row["id"]

    db_connection.commit()
    yield db_id

    with db_connection.cursor() as cur:
        cur.execute("DELETE FROM vehicle_provider_bindings WHERE customer_database_id = %s;", (db_id,))
        cur.execute("DELETE FROM customer_databases WHERE id = %s;", (db_id,))
        cur.execute("DELETE FROM customers WHERE id = %s;", (customer_id,))
    db_connection.commit()


@pytest.fixture
def test_vehicle(db_connection):
    plate = "TEST001"
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO motor_catalog (technical_number, engine_name)
            VALUES ('TEC001', 'Test Motor')
            ON CONFLICT DO NOTHING;
            """,
        )
    with db_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO vehicle_motor_assignments (plate, technical_number, geotab_status)
            VALUES (%s, 'TEC001', 'unknown')
            ON CONFLICT (plate) DO UPDATE SET plate = EXCLUDED.plate
            RETURNING plate;
            """,
            (plate,),
        )
    db_connection.commit()
    yield plate
    with db_connection.cursor() as cur:
        cur.execute("DELETE FROM vehicle_provider_bindings WHERE plate = %s;", (plate,))
        cur.execute("DELETE FROM vehicle_motor_assignments WHERE plate = %s;", (plate,))
    db_connection.commit()


@pytest.fixture
async def authenticated_editor(client, editor_user):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "editor", "password": "EditorPass1!"},
    )
    assert resp.status_code == 200
    # La autenticacion es por cookie (el client de httpx la persiste solo);
    # devolvemos el valor por compatibilidad con _auth_headers.
    token = resp.cookies.get("access_token")
    assert token
    return token


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestProviderVehicleBindings:
    @pytest.mark.asyncio
    async def test_assign_vehicle_database_with_provider_vehicle_id_inserts_manual_binding(
        self, client, geotab_database, test_vehicle, authenticated_editor
    ):
        resp = await client.put(
            f"/api/v1/vehicle/{test_vehicle}/database",
            json={"customer_database_id": geotab_database, "provider_vehicle_id": "b1234"},
            headers=_auth_headers(authenticated_editor),
        )
        assert resp.status_code == 200, resp.text

        raw_url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL_TEST", "").strip()
        parsed = urlparse(raw_url.replace("postgresql+psycopg://", "postgresql://", 1))
        db_url = urlunparse(parsed)
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT provider_vehicle_id, is_manual, binding_status
                    FROM vehicle_provider_bindings
                    WHERE plate = %s AND customer_database_id = %s;
                    """,
                    (test_vehicle, geotab_database),
                )
                row = cur.fetchone()

        assert row is not None, "Binding was not created"
        assert row["provider_vehicle_id"] == "b1234"
        assert row["is_manual"] is True
        assert row["binding_status"] == "resolved"

    @pytest.mark.asyncio
    async def test_assign_vehicle_database_with_empty_string_clears_manual_binding(
        self, client, frotcom_database, test_vehicle, authenticated_editor
    ):
        raw_url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL_TEST", "").strip()
        parsed = urlparse(raw_url.replace("postgresql+psycopg://", "postgresql://", 1))
        db_url = urlunparse(parsed)
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vehicle_provider_bindings
                    (plate, customer_database_id, provider, provider_vehicle_id, binding_status, is_manual, updated_at)
                    VALUES (%s, %s, 'frotcom', 'M1', 'resolved', TRUE, NOW())
                    ON CONFLICT (plate, customer_database_id, provider)
                    DO UPDATE SET provider_vehicle_id = EXCLUDED.provider_vehicle_id, is_manual = TRUE;
                    """,
                    (test_vehicle, frotcom_database),
                )
            conn.commit()

        resp = await client.put(
            f"/api/v1/vehicle/{test_vehicle}/database",
            json={"customer_database_id": frotcom_database, "provider_vehicle_id": ""},
            headers=_auth_headers(authenticated_editor),
        )
        assert resp.status_code == 200, resp.text

        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT provider_vehicle_id, is_manual
                    FROM vehicle_provider_bindings
                    WHERE plate = %s AND customer_database_id = %s AND is_manual = TRUE;
                    """,
                    (test_vehicle, frotcom_database),
                )
                row = cur.fetchone()

        assert row is None, "Manual binding should have been deleted"

    @pytest.mark.asyncio
    async def test_assign_vehicle_database_with_null_preserves_binding(
        self, client, geotab_database, test_vehicle, authenticated_editor
    ):
        raw_url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL_TEST", "").strip()
        parsed = urlparse(raw_url.replace("postgresql+psycopg://", "postgresql://", 1))
        db_url = urlunparse(parsed)
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vehicle_provider_bindings
                    (plate, customer_database_id, provider, provider_vehicle_id, binding_status, is_manual, updated_at)
                    VALUES (%s, %s, 'geotab', 'M2', 'resolved', TRUE, NOW())
                    ON CONFLICT (plate, customer_database_id, provider)
                    DO UPDATE SET provider_vehicle_id = EXCLUDED.provider_vehicle_id, is_manual = TRUE;
                    """,
                    (test_vehicle, geotab_database),
                )
            conn.commit()

        resp = await client.put(
            f"/api/v1/vehicle/{test_vehicle}/database",
            json={"customer_database_id": geotab_database},
            headers=_auth_headers(authenticated_editor),
        )
        assert resp.status_code == 200, resp.text

        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT provider_vehicle_id, is_manual
                    FROM vehicle_provider_bindings
                    WHERE plate = %s AND customer_database_id = %s AND is_manual = TRUE;
                    """,
                    (test_vehicle, geotab_database),
                )
                row = cur.fetchone()

        assert row is not None, "Manual binding should have been preserved"
        assert row["provider_vehicle_id"] == "M2"
        assert row["is_manual"] is True

    @pytest.mark.asyncio
    async def test_list_vehicle_assignments_surfaces_manual_binding(
        self, client, geotab_database, test_vehicle, authenticated_editor
    ):
        raw_url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL_TEST", "").strip()
        parsed = urlparse(raw_url.replace("postgresql+psycopg://", "postgresql://", 1))
        db_url = urlunparse(parsed)
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vehicle_provider_bindings
                    (plate, customer_database_id, provider, provider_vehicle_id, binding_status, is_manual, updated_at)
                    VALUES (%s, %s, 'geotab', 'b9999', 'resolved', TRUE, NOW())
                    ON CONFLICT (plate, customer_database_id, provider)
                    DO UPDATE SET provider_vehicle_id = EXCLUDED.provider_vehicle_id, is_manual = TRUE;
                    """,
                    (test_vehicle, geotab_database),
                )
                cur.execute(
                    """
                    UPDATE vehicle_motor_assignments
                    SET customer_database_id = %s
                    WHERE plate = %s;
                    """,
                    (geotab_database, test_vehicle),
                )
            conn.commit()

        resp = await client.get(
            f"/api/v1/vehicle/{test_vehicle}",
            headers=_auth_headers(authenticated_editor),
        )
        assert resp.status_code == 200, resp.text
        record = resp.json()
        assert record["provider_vehicle_id"] == "b9999"
        assert record["is_provider_vehicle_id_manual"] is True

    @pytest.mark.asyncio
    async def test_assign_vehicle_database_without_database_preserves_existing_behavior(
        self, client, test_vehicle, authenticated_editor
    ):
        resp = await client.put(
            f"/api/v1/vehicle/{test_vehicle}/database",
            json={"customer_database_id": None},
            headers=_auth_headers(authenticated_editor),
        )
        assert resp.status_code == 200, resp.text