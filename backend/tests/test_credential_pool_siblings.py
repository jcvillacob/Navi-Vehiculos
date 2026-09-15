"""Las credenciales pertenecen a la database de Geotab, no al cliente.

Varios clientes pueden tener su propia fila en ``customer_databases`` apuntando
al mismo ``database_name``, y todos usan las mismas cuentas contra MyGeotab. Que
la tabla de credenciales cuelgue de ``customer_database_id`` es un detalle de
almacenamiento: el listado, la unicidad y las escrituras razonan sobre la
database fisica completa.
"""
from __future__ import annotations

import pytest

from app.schemas.vehicle import (
    CustomerDatabaseCredentialCreateRequest,
    CustomerDatabaseCredentialUpdateRequest,
)
from app.services.motor_catalog import (
    create_database_credential,
    delete_database_credential,
    list_database_credentials,
    update_database_credential,
)
from tests.test_geotab_taller import (
    _connect,
    _insert_customer,
    _insert_geotab_db,
    _truncate_all,
)


@pytest.fixture
def pool_db():
    _truncate_all()
    yield
    _truncate_all()


def _insert_credential(database_id: int, username: str, password: str = "gAAAAA-fake") -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO customer_database_credentials
                    (customer_database_id, username, password)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (database_id, username, password),
            )
            credential_id = int(cur.fetchone()["id"])
        conn.commit()
    return credential_id


def _all_rows(username: str) -> list[dict]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, customer_database_id, username, password, is_active, label
                FROM customer_database_credentials
                WHERE LOWER(username) = LOWER(%s)
                ORDER BY id;
                """,
                (username,),
            )
            return list(cur.fetchall())


def test_list_returns_every_credential_of_the_physical_database(pool_db):
    """Abrir cualquier cliente muestra las credenciales de la database."""
    rayogas = _insert_customer("Rayogas")
    incubadora = _insert_customer("Incubadora Santander")
    rayogas_db = _insert_geotab_db(rayogas, "navitrans")
    incubadora_db = _insert_geotab_db(incubadora, "navitrans")

    _insert_credential(rayogas_db, "uno@navitrans.com.co")
    _insert_credential(incubadora_db, "dos@navitrans.com.co")

    desde_rayogas = list_database_credentials(rayogas_db)
    desde_incubadora = list_database_credentials(incubadora_db)

    assert {record.username for record in desde_rayogas} == {
        "uno@navitrans.com.co",
        "dos@navitrans.com.co",
    }
    # Las dos vistas son la misma lista: la credencial es de la database.
    assert {record.username for record in desde_incubadora} == {
        "uno@navitrans.com.co",
        "dos@navitrans.com.co",
    }


def test_list_collapses_the_same_user_stored_under_several_customers(pool_db):
    """Un usuario repetido en varios clientes es UNA credencial, no varias."""
    vigia = _insert_customer("Vigia")
    viacargo = _insert_customer("Viacargo")
    vigia_db = _insert_geotab_db(vigia, "navitrans")
    viacargo_db = _insert_geotab_db(viacargo, "navitrans")

    _insert_credential(vigia_db, "compartida@navitrans.com.co")
    _insert_credential(viacargo_db, "compartida@navitrans.com.co")

    records = list_database_credentials(vigia_db)

    assert [record.username for record in records] == ["compartida@navitrans.com.co"]
    # La entrada apunta a la copia del cliente abierto.
    assert records[0].customer_database_id == vigia_db
    # Pero las dos filas siguen existiendo hasta que se corra la limpieza.
    assert len(_all_rows("compartida@navitrans.com.co")) == 2


def test_list_does_not_mix_databases_with_different_names(pool_db):
    """Solo se agrupa por database_name; otra database no entra."""
    alion = _insert_customer("Alion")
    rivercol = _insert_customer("Rivercol")
    alion_db = _insert_geotab_db(alion, "alion")
    rivercol_db = _insert_geotab_db(rivercol, "rivercol")

    _insert_credential(alion_db, "alion@navitrans.com.co")
    _insert_credential(rivercol_db, "rivercol@navitrans.com.co")

    records = list_database_credentials(alion_db)

    assert [record.username for record in records] == ["alion@navitrans.com.co"]


def test_create_rejects_username_already_in_the_database(pool_db):
    """El mismo usuario dos veces sesga la rotacion LRU hacia esa cuenta."""
    vigia = _insert_customer("Vigia")
    viacargo = _insert_customer("Viacargo")
    vigia_db = _insert_geotab_db(vigia, "navitrans")
    viacargo_db = _insert_geotab_db(viacargo, "navitrans")

    _insert_credential(viacargo_db, "compartida@navitrans.com.co")

    with pytest.raises(ValueError) as excinfo:
        create_database_credential(
            vigia_db,
            CustomerDatabaseCredentialCreateRequest(
                username="compartida@navitrans.com.co",
                password="secreta",
            ),
        )

    assert "ya es una credencial de esta database" in str(excinfo.value)


def test_update_rejects_rename_that_collides_within_the_database(pool_db):
    vigia = _insert_customer("Vigia")
    viacargo = _insert_customer("Viacargo")
    vigia_db = _insert_geotab_db(vigia, "navitrans")
    viacargo_db = _insert_geotab_db(viacargo, "navitrans")

    credential_id = _insert_credential(vigia_db, "propia@navitrans.com.co")
    _insert_credential(viacargo_db, "compartida@navitrans.com.co")

    with pytest.raises(ValueError) as excinfo:
        update_database_credential(
            credential_id,
            CustomerDatabaseCredentialUpdateRequest(username="compartida@navitrans.com.co"),
        )

    assert "ya es una credencial" in str(excinfo.value)


def test_update_propagates_to_every_copy_of_the_credential(pool_db):
    """Editar desde un cliente no puede dejar la clave vieja viva en otro.

    Mientras existan copias historicas del mismo usuario, la rotacion puede
    tomar cualquiera de ellas. Si el cambio de contrasena tocara solo la fila
    abierta, la mitad de los intentos seguiria autenticando con la anterior.
    """
    from app.core.crypto import decrypt_secret

    vigia = _insert_customer("Vigia")
    viacargo = _insert_customer("Viacargo")
    vigia_db = _insert_geotab_db(vigia, "navitrans")
    viacargo_db = _insert_geotab_db(viacargo, "navitrans")

    credential_id = _insert_credential(vigia_db, "compartida@navitrans.com.co")
    _insert_credential(viacargo_db, "compartida@navitrans.com.co")

    update_database_credential(
        credential_id,
        CustomerDatabaseCredentialUpdateRequest(password="clave-nueva", label="rotada"),
    )

    rows = _all_rows("compartida@navitrans.com.co")
    assert len(rows) == 2
    assert {decrypt_secret(row["password"]) for row in rows} == {"clave-nueva"}
    assert {row["label"] for row in rows} == {"rotada"}


def test_delete_removes_every_copy_of_the_credential(pool_db):
    """Borrar una credencial la saca de la rotacion, no solo de un cliente."""
    vigia = _insert_customer("Vigia")
    viacargo = _insert_customer("Viacargo")
    vigia_db = _insert_geotab_db(vigia, "navitrans")
    viacargo_db = _insert_geotab_db(viacargo, "navitrans")

    credential_id = _insert_credential(vigia_db, "compartida@navitrans.com.co")
    _insert_credential(viacargo_db, "compartida@navitrans.com.co")
    # Otra credencial distinta, para que la guarda de "ultima activa" no aplique.
    _insert_credential(viacargo_db, "otra@navitrans.com.co")

    delete_database_credential(credential_id)

    assert _all_rows("compartida@navitrans.com.co") == []
    assert [record.username for record in list_database_credentials(vigia_db)] == [
        "otra@navitrans.com.co"
    ]


def test_cannot_delete_the_last_credential_even_with_duplicate_copies(pool_db):
    """Dos copias del mismo usuario no son dos accesos: se borran juntas."""
    vigia = _insert_customer("Vigia")
    viacargo = _insert_customer("Viacargo")
    vigia_db = _insert_geotab_db(vigia, "navitrans")
    viacargo_db = _insert_geotab_db(viacargo, "navitrans")

    credential_id = _insert_credential(vigia_db, "unica@navitrans.com.co")
    _insert_credential(viacargo_db, "unica@navitrans.com.co")

    with pytest.raises(ValueError) as excinfo:
        delete_database_credential(credential_id)

    assert "ultima credencial activa" in str(excinfo.value)
    assert len(_all_rows("unica@navitrans.com.co")) == 2


def test_sync_primary_credential_does_not_duplicate_across_customers(pool_db):
    """Crear la fila de otro cliente con el mismo usuario no engorda el pool.

    ``_sync_primary_credential`` corre al guardar una ``customer_databases``.
    Antes insertaba la credencial legacy en cada fila, y una database compartida
    por trece clientes terminaba con trece copias del mismo usuario.
    """
    from app.core.crypto import decrypt_secret
    from app.services.motor_catalog import _sync_primary_credential

    primero = _insert_customer("Primero")
    segundo = _insert_customer("Segundo")
    primero_db = _insert_geotab_db(primero, "navitrans")
    segundo_db = _insert_geotab_db(segundo, "navitrans")

    with _connect() as conn:
        _sync_primary_credential(conn, primero_db, "admin@navitrans.com.co", "clave-vieja")
        _sync_primary_credential(conn, segundo_db, "admin@navitrans.com.co", "clave-nueva")
        conn.commit()

    rows = _all_rows("admin@navitrans.com.co")

    assert len(rows) == 1
    assert rows[0]["customer_database_id"] == primero_db
    # Guardar desde el segundo cliente rota la clave en vez de perderse.
    assert decrypt_secret(rows[0]["password"]) == "clave-nueva"
