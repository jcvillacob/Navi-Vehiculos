"""
Cifrado simetrico (Fernet) para secretos en reposo y para el transporte.

Son DOS claves con papeles distintos, y no se deben fusionar:

- INTEGRATION_FERNET_KEY cifra en reposo. Es exclusiva de esta aplicacion y
  rotarla no afecta a nadie mas.
- SNAPSHOT_TRANSPORT_FERNET_KEY cifra la contrasena que sale en el snapshot de
  integracion, para que no viaje en claro por un CDN que termina TLS. Es la
  clave de reposo de Portal Clientes, que recibe el token y lo almacena tal
  cual sin descifrarlo. Rotarla es cambiar esta variable: no toca los datos en
  reposo de esta aplicacion.

- La clave de reposo viene de INTEGRATION_FERNET_KEY (generar con
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
- Si la variable NO esta definida, el modulo opera en modo passthrough
  (no cifra, no descifra) para no romper entornos existentes; loggea un
  warning una sola vez.
- decrypt_secret() tolera valores legacy en texto plano: si el valor no
  parece un token Fernet (prefijo 'gAAAA'), se devuelve tal cual.
"""
from __future__ import annotations

import logging
import os
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

_logger = logging.getLogger(__name__)

_ENV_KEY_NAME: Final[str] = "INTEGRATION_FERNET_KEY"
_TRANSPORT_ENV_KEY_NAME: Final[str] = "SNAPSHOT_TRANSPORT_FERNET_KEY"
_FERNET_TOKEN_PREFIX: Final[str] = "gAAAA"

_fernet: Fernet | None | object = object()  # sentinel para lazy-load
_transport_fernet: Fernet | None | object = object()
_warning_logged: bool = False


def _reset_for_tests() -> None:
    """Limpia el singleton para que los tests puedan cambiar la env var."""
    global _fernet, _transport_fernet, _warning_logged
    _fernet = object()
    _transport_fernet = object()
    _warning_logged = False


def _get_fernet() -> Fernet | None:
    """Singleton lazy que devuelve la instancia Fernet o None si no hay clave."""
    global _fernet, _warning_logged

    if isinstance(_fernet, Fernet) or _fernet is None:
        return _fernet  # type: ignore[return-value]

    raw_key = os.getenv(_ENV_KEY_NAME, "").strip()
    if not raw_key:
        _fernet = None
        if not _warning_logged:
            _logger.warning(
                "%s no esta definida; el modulo crypto opera en modo passthrough "
                "(secretos en texto plano).",
                _ENV_KEY_NAME,
            )
            _warning_logged = True
        return None

    try:
        _fernet = Fernet(raw_key.encode("ascii"))
    except Exception as exc:
        _logger.error(
            "La clave %s no es valida para Fernet: %s. "
            "El modulo operara en modo passthrough.",
            _ENV_KEY_NAME,
            exc,
        )
        _fernet = None

    return _fernet


def is_encrypted(value: str | None) -> bool:
    """Indica si un valor parece ya estar cifrado con Fernet."""
    if not value:
        return False
    return str(value).startswith(_FERNET_TOKEN_PREFIX)


def encrypt_secret(value: str | None) -> str | None:
    """Cifra un secreto con Fernet; si no hay clave devuelve el valor original."""
    if value is None or value == "":
        return value

    fernet = _get_fernet()
    if fernet is None:
        return value

    token = fernet.encrypt(value.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(value: str | None) -> str | None:
    """Descifra un token Fernet; tolera valores legacy en texto plano."""
    if value is None or value == "":
        return value

    text = str(value)
    if not text.startswith(_FERNET_TOKEN_PREFIX):
        # Valor legacy en texto plano: se devuelve tal cual para compatibilidad.
        return text

    fernet = _get_fernet()
    if fernet is None:
        # Hay un token pero no tenemos clave para descifrarlo: no exponerlo.
        _logger.error(
            "Se encontro un token Fernet en %s pero %s no esta definida; "
            "no es posible descifrar el valor.",
            "customer_database_credentials.password",
            _ENV_KEY_NAME,
        )
        return None

    try:
        plain = fernet.decrypt(text.encode("ascii"))
        return plain.decode("utf-8")
    except InvalidToken:
        # NO se devuelve el token crudo. Un valor con prefijo Fernet que no
        # descifra significa clave equivocada o dato corrompido, y entregarlo
        # tal cual lo hace pasar por la contrasena en claro: el snapshot de
        # integracion lo publicaria como `password` y Portal Clientes lo
        # cifraria por segunda vez, dejando al ETL sin poder autenticar contra
        # geotab sin que nada lo delate. None es ruidoso y fail-closed.
        _logger.error(
            "Token Fernet invalido para %s (clave incorrecta o dato corrompido); "
            "no es posible descifrar el valor.",
            "customer_database_credentials.password",
        )
        return None
    except Exception as exc:
        _logger.error(
            "Error inesperado al descifrar %s: %s; no se expone el valor crudo.",
            "customer_database_credentials.password",
            exc,
        )
        return None


def _get_transport_fernet() -> Fernet | None:
    """Singleton lazy de la clave de transporte, o None si no esta definida."""
    global _transport_fernet

    if isinstance(_transport_fernet, Fernet) or _transport_fernet is None:
        return _transport_fernet  # type: ignore[return-value]

    raw_key = os.getenv(_TRANSPORT_ENV_KEY_NAME, "").strip()
    if not raw_key:
        _transport_fernet = None
        return None

    try:
        _transport_fernet = Fernet(raw_key.encode("ascii"))
    except Exception as exc:
        _logger.error("La clave %s no es valida para Fernet: %s", _TRANSPORT_ENV_KEY_NAME, exc)
        _transport_fernet = None

    return _transport_fernet  # type: ignore[return-value]


def encrypt_for_transport(plaintext: str | None) -> str | None:
    """Cifra un secreto con la clave de TRANSPORTE, para publicarlo en el snapshot.

    Devuelve None si la clave no esta configurada. Es fail-closed a proposito:
    no existe un modo passthrough como el del reposo, porque ahi el efecto
    seria publicar la contrasena en claro hacia afuera. Sin clave, el snapshot
    sale sin `password_enc` y el consumidor conserva lo que ya tenia.
    """
    if plaintext is None or plaintext == "":
        return None

    fernet = _get_transport_fernet()
    if fernet is None:
        _logger.error(
            "%s no esta definida: el snapshot de integracion sale sin `password_enc`. "
            "Debe valer la clave Fernet de reposo de Portal Clientes.",
            _TRANSPORT_ENV_KEY_NAME,
        )
        return None

    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
