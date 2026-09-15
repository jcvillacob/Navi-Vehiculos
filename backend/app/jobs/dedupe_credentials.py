"""
Limpieza: quita credenciales repetidas dentro del pool de una misma database Geotab.

Varias filas de ``customer_databases`` pueden apuntar al mismo ``database_name``
(una por cliente). ``get_geotab_config_for_database`` rota sobre todas ellas como
un unico pool, asi que el mismo usuario cargado en dos filas hermanas no aporta
un acceso adicional: solo hace que la LRU lo elija el doble de veces y agote
antes su cupo de sesiones concurrentes en MyGeotab.

El UNIQUE de la tabla es ``(customer_database_id, username)`` y no puede abarcar
las filas hermanas porque ``database_name`` vive en otra tabla, de modo que los
duplicados historicos hay que limpiarlos aqui.

Uso:
    python -m app.jobs.dedupe_credentials             # dry-run, no borra nada
    python -m app.jobs.dedupe_credentials --apply     # aplica el borrado

Criterio: dentro de cada grupo (database_name + username, sin distinguir
mayusculas) se conserva UNA fila y se borran las demas. Se conserva la de
``updated_at`` mas reciente, y a igualdad la de ``id`` menor.

Si las copias no guardan la misma contrasena en claro, el grupo se SALTA y se
reporta: puede tratarse de una rotacion de clave a medias y elegir por fecha
podria dejar activa la contrasena equivocada.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from typing import Any

from app.core.crypto import decrypt_secret
from app.core.db import db_conn
from psycopg.rows import dict_row

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [dedupe-credentials] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _mask(username: str) -> str:
    """Enmascara el usuario: los logs de este job pueden quedar archivados."""
    text = (username or "").strip()
    if len(text) <= 4:
        return "***"
    return f"{text[:4]}***"


def _fetch_pool_rows(conn) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                k.id,
                k.customer_database_id,
                k.username,
                k.password,
                k.is_active,
                k.last_used_at,
                k.updated_at,
                LOWER(cd.database_name) AS database_key,
                c.name AS owner_customer_name
            FROM customer_database_credentials k
            INNER JOIN customer_databases cd ON cd.id = k.customer_database_id
            LEFT JOIN customers c ON c.id = cd.customer_id
            WHERE cd.connection_type = 'geotab'
            ORDER BY k.id;
            """
        )
        return list(cur.fetchall())


def _group_duplicates(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["database_key"], (row["username"] or "").strip().lower())
        grouped[key].append(row)
    return {key: group for key, group in grouped.items() if len(group) > 1}


def _passwords_agree(group: list[dict[str, Any]]) -> bool:
    """True si todas las copias descifran a la misma contrasena en claro.

    Fernet no es determinista, asi que comparar el ciphertext no sirve: hay que
    descifrar. Una copia que no descifra invalida el grupo.
    """
    plaintexts = set()
    for row in group:
        plaintext = decrypt_secret(row["password"])
        if plaintext is None:
            return False
        plaintexts.add(plaintext)
    return len(plaintexts) == 1


def _pick_survivor(group: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        group,
        key=lambda row: (row["updated_at"], -row["id"]),
        reverse=True,
    )[0]


def _run(apply_changes: bool) -> int:
    deleted_total = 0
    skipped_groups = 0

    with db_conn(row_factory=dict_row) as conn:
        duplicates = _group_duplicates(_fetch_pool_rows(conn))

        if not duplicates:
            logger.info("No hay credenciales duplicadas en ningun pool Geotab.")
            return 0

        for (database_key, username_key), group in sorted(duplicates.items()):
            masked = _mask(username_key)
            if not _passwords_agree(group):
                skipped_groups += 1
                logger.warning(
                    "SALTADO database='%s' usuario='%s': las %d copias no comparten la "
                    "misma contrasena (o alguna no descifra). Revisar a mano.",
                    database_key,
                    masked,
                    len(group),
                )
                continue

            survivor = _pick_survivor(group)
            doomed = [row for row in group if row["id"] != survivor["id"]]
            logger.info(
                "database='%s' usuario='%s': conservar id=%s (cliente %s), borrar %s.",
                database_key,
                masked,
                survivor["id"],
                survivor["owner_customer_name"],
                ", ".join(f"id={row['id']} ({row['owner_customer_name']})" for row in doomed),
            )

            if apply_changes:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM customer_database_credentials WHERE id = ANY(%s);",
                        ([row["id"] for row in doomed],),
                    )
            deleted_total += len(doomed)

        if apply_changes:
            conn.commit()

    if apply_changes:
        logger.info(
            "Limpieza aplicada: %d filas duplicadas borradas, %d grupos saltados.",
            deleted_total,
            skipped_groups,
        )
    else:
        logger.info(
            "Dry-run: se borrarian %d filas duplicadas, %d grupos quedarian saltados. "
            "Repetir con --apply para ejecutarlo.",
            deleted_total,
            skipped_groups,
        )
    return deleted_total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica el borrado. Sin este flag el script solo reporta.",
    )
    args = parser.parse_args()
    _run(args.apply)


if __name__ == "__main__":
    main()
