"""Grupos internos de vehiculos por cliente (categorias/subcategorias).

El cliente organiza su flota como quiera (ej. Bavaria: Regional -> CEDI ->
categorias). El arbol vive en customer_vehicle_groups (parent_id auto-ref) y
cada vehiculo apunta a lo sumo a UN nodo via
vehicle_motor_assignments.customer_group_id. La cadena de padres queda
implicita, asi que asignar a una subcategoria asigna tambien su categoria.
Todo se exporta a Portal Clientes en el snapshot de integracion.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row

from app.services.motor_catalog import _database_dsn, _ensure_motor_tables

_logger = logging.getLogger(__name__)

# Techo defensivo de profundidad del arbol. La UI trabaja con 2-3 niveles;
# esto solo evita cadenas absurdas o bucles ante datos corruptos.
_MAX_GROUP_DEPTH = 5


def _normalize_name(name: str | None) -> str:
    normalized = (name or "").strip()
    if not normalized:
        raise ValueError("El nombre del grupo es obligatorio.")
    if len(normalized) > 120:
        raise ValueError("El nombre del grupo no puede superar 120 caracteres.")
    return normalized


def _fetch_group(cur: psycopg.Cursor, group_id: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT id, customer_id, parent_id, name, is_active, created_at, updated_at
        FROM customer_vehicle_groups
        WHERE id = %s;
        """,
        (group_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError("El grupo no existe.")
    return row


def _ancestor_chain_ids(cur: psycopg.Cursor, group_id: int) -> list[int]:
    """Ids del nodo y todos sus ancestros (para validar ciclos y profundidad)."""
    cur.execute(
        """
        WITH RECURSIVE chain AS (
            SELECT id, parent_id, 1 AS depth
            FROM customer_vehicle_groups
            WHERE id = %s
            UNION ALL
            SELECT g.id, g.parent_id, chain.depth + 1
            FROM customer_vehicle_groups g
            INNER JOIN chain ON g.id = chain.parent_id
            WHERE chain.depth < %s
        )
        SELECT id FROM chain;
        """,
        (group_id, _MAX_GROUP_DEPTH + 1),
    )
    return [int(row["id"]) for row in cur.fetchall()]


def _touch_customer(cur: psycopg.Cursor, customer_id: int) -> None:
    """Marca al cliente como cambiado.

    El export incremental hacia Portal Clientes decide si incluye a un cliente
    mirando updated_at (suyo, de sus databases, credenciales o grupos). Un
    grupo BORRADO ya no existe para esa comparacion, asi que sin este toque la
    baja seria invisible hasta un full sync. Se hace en toda mutacion de grupos
    por consistencia.
    """
    cur.execute(
        "UPDATE customers SET updated_at = NOW() WHERE id = %s;", (customer_id,)
    )


def _group_payload(row: dict[str, Any], vehicle_count: int = 0) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "customer_id": int(row["customer_id"]),
        "parent_id": int(row["parent_id"]) if row.get("parent_id") is not None else None,
        "name": row["name"],
        "is_active": bool(row["is_active"]),
        "vehicle_count": vehicle_count,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_customer_groups(customer_id: int) -> list[dict[str, Any]]:
    """Arbol plano (parent_id) de grupos del cliente, con conteo de vehiculos
    asignados DIRECTAMENTE a cada nodo. Orden estable por nombre dentro de
    cada nivel; el armado del arbol es del consumidor."""
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE id = %s;", (customer_id,))
            if cur.fetchone() is None:
                raise ValueError("El cliente no existe.")
            cur.execute(
                """
                SELECT
                    g.id, g.customer_id, g.parent_id, g.name, g.is_active,
                    g.created_at, g.updated_at,
                    COALESCE(v.vehicle_count, 0) AS vehicle_count
                FROM customer_vehicle_groups g
                LEFT JOIN (
                    SELECT customer_group_id, COUNT(*) AS vehicle_count
                    FROM vehicle_motor_assignments
                    WHERE customer_group_id IS NOT NULL
                    GROUP BY customer_group_id
                ) v ON v.customer_group_id = g.id
                WHERE g.customer_id = %s
                ORDER BY COALESCE(g.parent_id, 0) ASC, LOWER(g.name) ASC;
                """,
                (customer_id,),
            )
            rows = cur.fetchall()
    return [_group_payload(row, int(row.get("vehicle_count") or 0)) for row in rows]


def create_customer_group(
    customer_id: int, name: str, parent_id: int | None = None
) -> dict[str, Any]:
    normalized_name = _normalize_name(name)

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM customers WHERE id = %s;", (customer_id,))
            if cur.fetchone() is None:
                raise ValueError("El cliente no existe.")

            if parent_id is not None:
                parent = _fetch_group(cur, parent_id)
                if int(parent["customer_id"]) != int(customer_id):
                    raise ValueError("El grupo padre pertenece a otro cliente.")
                if len(_ancestor_chain_ids(cur, parent_id)) >= _MAX_GROUP_DEPTH:
                    raise ValueError(
                        f"Profundidad maxima de grupos alcanzada ({_MAX_GROUP_DEPTH} niveles)."
                    )

            try:
                cur.execute(
                    """
                    INSERT INTO customer_vehicle_groups (customer_id, parent_id, name)
                    VALUES (%s, %s, %s)
                    RETURNING id, customer_id, parent_id, name, is_active,
                              created_at, updated_at;
                    """,
                    (customer_id, parent_id, normalized_name),
                )
            except pg_errors.UniqueViolation as exc:
                raise ValueError(
                    "Ya existe un grupo con ese nombre en ese nivel."
                ) from exc
            row = cur.fetchone()
            _touch_customer(cur, customer_id)
        conn.commit()
    return _group_payload(row)


def update_customer_group(
    group_id: int,
    *,
    name: str | None = None,
    is_active: bool | None = None,
    parent_id: int | None = None,
    move_parent: bool = False,
) -> dict[str, Any]:
    """Renombra, activa/desactiva o mueve un grupo.

    move_parent=True aplica parent_id (incluso None = subir a la raiz);
    False lo ignora, porque None tambien es un valor valido de destino.
    """
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            group = _fetch_group(cur, group_id)

            new_name = group["name"] if name is None else _normalize_name(name)
            new_active = bool(group["is_active"]) if is_active is None else bool(is_active)
            new_parent = group["parent_id"]
            if move_parent:
                if parent_id is not None:
                    if int(parent_id) == int(group_id):
                        raise ValueError("Un grupo no puede ser su propio padre.")
                    parent = _fetch_group(cur, parent_id)
                    if int(parent["customer_id"]) != int(group["customer_id"]):
                        raise ValueError("El grupo padre pertenece a otro cliente.")
                    if int(group_id) in _ancestor_chain_ids(cur, parent_id):
                        raise ValueError(
                            "No se puede mover un grupo dentro de uno de sus descendientes."
                        )
                    if len(_ancestor_chain_ids(cur, parent_id)) >= _MAX_GROUP_DEPTH:
                        raise ValueError(
                            f"Profundidad maxima de grupos alcanzada ({_MAX_GROUP_DEPTH} niveles)."
                        )
                new_parent = parent_id

            try:
                cur.execute(
                    """
                    UPDATE customer_vehicle_groups
                    SET name = %s, is_active = %s, parent_id = %s, updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, customer_id, parent_id, name, is_active,
                              created_at, updated_at;
                    """,
                    (new_name, new_active, new_parent, group_id),
                )
            except pg_errors.UniqueViolation as exc:
                raise ValueError(
                    "Ya existe un grupo con ese nombre en ese nivel."
                ) from exc
            row = cur.fetchone()

            cur.execute(
                """
                SELECT COUNT(*) AS vehicle_count
                FROM vehicle_motor_assignments
                WHERE customer_group_id = %s;
                """,
                (group_id,),
            )
            count_row = cur.fetchone()
            _touch_customer(cur, int(group["customer_id"]))
        conn.commit()
    return _group_payload(row, int(count_row["vehicle_count"]))


def delete_customer_group(group_id: int) -> None:
    """Borra un grupo vacio: sin subgrupos y sin vehiculos asignados.

    Para retirar un grupo con historia sin tocar nada, usar is_active=False;
    Portal Clientes lo desactiva en el siguiente full sync en vez de borrarlo.
    """
    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            group = _fetch_group(cur, group_id)
            cur.execute(
                "SELECT 1 FROM customer_vehicle_groups WHERE parent_id = %s LIMIT 1;",
                (group_id,),
            )
            if cur.fetchone() is not None:
                raise ValueError(
                    "El grupo tiene subgrupos; muevalos o borrelos primero."
                )
            cur.execute(
                """
                SELECT COUNT(*) AS vehicle_count
                FROM vehicle_motor_assignments
                WHERE customer_group_id = %s;
                """,
                (group_id,),
            )
            count = int(cur.fetchone()["vehicle_count"])
            if count > 0:
                raise ValueError(
                    f"El grupo tiene {count} vehiculo(s) asignado(s); reasignelos primero."
                )
            cur.execute(
                "DELETE FROM customer_vehicle_groups WHERE id = %s;", (group_id,)
            )
            _touch_customer(cur, int(group["customer_id"]))
        conn.commit()


def set_vehicle_group(plate: str, group_id: int | None) -> dict[str, Any]:
    """Asigna (o limpia, con None) el grupo interno de un vehiculo.

    El grupo debe pertenecer al cliente del vehiculo y estar activo; un
    vehiculo sin cliente no puede tener grupo.
    """
    normalized_plate = plate.strip().upper()
    if not normalized_plate:
        raise ValueError("La placa es obligatoria.")

    with psycopg.connect(_database_dsn(), row_factory=dict_row) as conn:
        _ensure_motor_tables(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT plate, customer_id
                FROM vehicle_motor_assignments
                WHERE plate = %s;
                """,
                (normalized_plate,),
            )
            vehicle = cur.fetchone()
            if vehicle is None:
                raise ValueError("El vehiculo no existe.")

            group_name = None
            group_path = None
            if group_id is not None:
                group = _fetch_group(cur, group_id)
                if vehicle["customer_id"] is None:
                    raise ValueError(
                        "El vehiculo no tiene cliente asignado; asigne el cliente primero."
                    )
                if int(group["customer_id"]) != int(vehicle["customer_id"]):
                    raise ValueError("El grupo pertenece a otro cliente.")
                if not group["is_active"]:
                    raise ValueError("El grupo esta inactivo.")
                group_name = group["name"]
                cur.execute(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT id, parent_id, name, 1 AS depth
                        FROM customer_vehicle_groups
                        WHERE id = %s
                        UNION ALL
                        SELECT g.id, g.parent_id, g.name, chain.depth + 1
                        FROM customer_vehicle_groups g
                        INNER JOIN chain ON g.id = chain.parent_id
                        WHERE chain.depth < %s
                    )
                    SELECT STRING_AGG(name, ' / ' ORDER BY depth DESC) AS group_path
                    FROM chain;
                    """,
                    (group_id, _MAX_GROUP_DEPTH + 1),
                )
                path_row = cur.fetchone()
                group_path = path_row["group_path"] if path_row else group_name

            cur.execute(
                """
                UPDATE vehicle_motor_assignments
                SET customer_group_id = %s, updated_at = NOW()
                WHERE plate = %s;
                """,
                (group_id, normalized_plate),
            )
        conn.commit()

    return {
        "plate": normalized_plate,
        "customer_group_id": group_id,
        "group_name": group_name,
        "group_path": group_path,
    }
