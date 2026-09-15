"""
Re-cifra customer_database_credentials.password de una clave Fernet a otra.

Uso:
    python -m app.jobs.rotate_credential_key                  # dry-run
    python -m app.jobs.rotate_credential_key --apply

Claves:
    INTEGRATION_FERNET_KEY      clave vigente (origen)
    NEW_INTEGRATION_FERNET_KEY  clave destino

Nace como parte de compartir la clave con Portal Clientes: el snapshot de
integracion emite el token tal cual en vez de la contraseña en claro, asi que
ambas aplicaciones tienen que cifrar con la misma clave. Tambien sirve para una
rotacion futura.

Es idempotente: una fila que ya descifra con la clave destino se salta, de modo
que volver a correrlo tras un fallo parcial termina el trabajo sin dañar lo ya
migrado. Nunca escribe una contraseña en el log.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from cryptography.fernet import Fernet, InvalidToken
from psycopg.rows import dict_row

from app.core.crypto import _FERNET_TOKEN_PREFIX, _ENV_KEY_NAME
from app.core.db import db_conn

_NEW_ENV_KEY_NAME = "NEW_INTEGRATION_FERNET_KEY"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [rotate-credential-key] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _load_fernet(env_name: str) -> Fernet:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        logger.error("Abortando: la variable %s no esta definida.", env_name)
        sys.exit(1)
    try:
        return Fernet(raw.encode("ascii"))
    except Exception as exc:  # clave malformada
        logger.error("La clave %s no es valida para Fernet: %s", env_name, exc)
        sys.exit(1)


def _run(apply_changes: bool) -> None:
    source = _load_fernet(_ENV_KEY_NAME)
    target = _load_fernet(_NEW_ENV_KEY_NAME)

    rotated = already = plain = failed = 0

    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, password FROM customer_database_credentials ORDER BY id;"
            )
            rows = cur.fetchall()

        for row in rows:
            token = row["password"]
            if not token:
                continue

            if not str(token).startswith(_FERNET_TOKEN_PREFIX):
                # Texto plano legacy: lo cifra con la clave destino. No se puede
                # verificar contra un origen porque nunca hubo token.
                plain += 1
                if apply_changes:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE customer_database_credentials "
                            "SET password = %s, updated_at = NOW() WHERE id = %s;",
                            (target.encrypt(str(token).encode("utf-8")).decode("ascii"), row["id"]),
                        )
                continue

            raw = str(token).encode("ascii")

            # La clave destino primero: si descifra, la fila ya esta migrada y
            # volver a cifrarla con el mismo secreto seria trabajo inutil.
            try:
                target.decrypt(raw)
                already += 1
                continue
            except InvalidToken:
                pass

            try:
                plaintext = source.decrypt(raw)
            except InvalidToken:
                # No descifra con ninguna de las dos: no se toca. Reescribirla
                # destruiria el unico dato que queda de esa credencial.
                logger.error(
                    "id=%s no descifra ni con %s ni con %s; se deja intacta.",
                    row["id"],
                    _ENV_KEY_NAME,
                    _NEW_ENV_KEY_NAME,
                )
                failed += 1
                continue

            new_token = target.encrypt(plaintext)
            # Verificacion antes de escribir: un token que no da la vuelta
            # dejaria la credencial irrecuperable.
            if target.decrypt(new_token) != plaintext:
                logger.error("id=%s: el token nuevo no reproduce el secreto; se aborta.", row["id"])
                sys.exit(1)

            rotated += 1
            if apply_changes:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE customer_database_credentials "
                        "SET password = %s, updated_at = NOW() WHERE id = %s;",
                        (new_token.decode("ascii"), row["id"]),
                    )

        if not apply_changes:
            conn.rollback()

    logger.info(
        "%s: %d re-cifradas, %d ya en la clave destino, %d planas cifradas, %d sin descifrar.",
        "Aplicado" if apply_changes else "Dry-run (sin cambios)",
        rotated,
        already,
        plain,
        failed,
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="escribe los cambios (por defecto dry-run)"
    )
    _run(parser.parse_args().apply)
