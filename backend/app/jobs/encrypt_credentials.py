"""
Backfill: cifra los passwords en texto plano de customer_database_credentials.

Uso:
    python -m app.jobs.encrypt_credentials

Requiere INTEGRATION_FERNET_KEY; si no esta definida el script aborta.
NO cifra filas que ya parezcan tokens Fernet.
"""
from __future__ import annotations

import logging
import os
import sys

from app.core.crypto import _ENV_KEY_NAME, encrypt_secret, is_encrypted
from app.core.db import db_conn
from psycopg.rows import dict_row

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [encrypt-credentials] %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _run() -> None:
    if not os.getenv(_ENV_KEY_NAME, "").strip():
        logger.error(
            "Abortando: la variable %s no esta definida. "
            "Generala con: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"",
            _ENV_KEY_NAME,
        )
        sys.exit(1)

    encrypted_count = 0
    skipped_count = 0

    with db_conn(row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, password
                FROM customer_database_credentials
                ORDER BY id;
                """
            )
            rows = cur.fetchall()

        for row in rows:
            raw_password = row["password"]
            if not raw_password or is_encrypted(raw_password):
                skipped_count += 1
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE customer_database_credentials
                    SET password = %s, updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (encrypt_secret(raw_password), row["id"]),
                )
            encrypted_count += 1

    logger.info(
        "Backfill completado: %d credenciales cifradas, %d saltadas (ya cifradas o vacias).",
        encrypted_count,
        skipped_count,
    )


if __name__ == "__main__":
    _run()
