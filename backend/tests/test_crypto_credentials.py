"""Tests puros para el cifrado Fernet de credenciales de integracion.

No tocan base de datos; verifican passthrough, roundtrip y compatibilidad
legacy.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.crypto import (
    _ENV_KEY_NAME,
    _TRANSPORT_ENV_KEY_NAME,
    _reset_for_tests,
    decrypt_secret,
    encrypt_for_transport,
    encrypt_secret,
    is_encrypted,
)


@pytest.fixture(autouse=True)
def _reset_crypto_singleton():
    """Limpia el singleton de Fernet antes y despues de cada test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def other_fernet_key() -> str:
    return Fernet.generate_key().decode()


@contextmanager
def _capture_crypto_logs(
    level: int = logging.DEBUG,
) -> Generator[list[logging.LogRecord], None, None]:
    """Captura los logs del logger app.core.crypto de forma aislada."""
    logger = logging.getLogger("app.core.crypto")
    handler = logging.Handler()
    handler.setLevel(level)
    records: list[logging.LogRecord] = []

    def _emit(record: logging.LogRecord) -> None:
        records.append(record)

    handler.emit = _emit  # type: ignore[method-assign]
    logger.addHandler(handler)
    original_level = logger.level
    original_disabled = logger.disabled
    logger.setLevel(min(level, original_level or logging.DEBUG))
    logger.disabled = False
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)
        logger.disabled = original_disabled


def test_encrypt_decrypt_without_key_is_passthrough(monkeypatch):
    monkeypatch.delenv(_ENV_KEY_NAME, raising=False)

    with _capture_crypto_logs(logging.WARNING) as records:
        plain = "plain-password"
        assert encrypt_secret(plain) is plain
        assert decrypt_secret(plain) is plain

        assert encrypt_secret(None) is None
        assert encrypt_secret("") == ""
        assert decrypt_secret(None) is None
        assert decrypt_secret("") == ""

    assert any(
        "no esta definida" in record.getMessage()
        for record in records
        if record.levelno == logging.WARNING
    )


def test_encrypt_decrypt_roundtrip_with_key(monkeypatch, fernet_key):
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)

    plain = "super-secret-password"
    token = encrypt_secret(plain)
    assert token is not None
    assert token != plain
    assert is_encrypted(token)

    decrypted = decrypt_secret(token)
    assert decrypted == plain


def test_decrypt_legacy_plain_with_key(monkeypatch, fernet_key):
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)

    legacy = "old-plain-password"
    assert decrypt_secret(legacy) == legacy
    assert not is_encrypted(legacy)


def test_decrypt_token_with_wrong_key_returns_none(
    monkeypatch, fernet_key, other_fernet_key
):
    """Clave equivocada da None, NO el token crudo.

    Devolverlo lo hacia pasar por la contrasena en claro: el snapshot de
    integracion lo habria publicado como `password` y el consumidor lo habria
    cifrado por segunda vez, dejando al ETL sin poder autenticar contra geotab
    sin que nada lo delatara.
    """
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)
    token = encrypt_secret("sensitive")

    monkeypatch.setenv(_ENV_KEY_NAME, other_fernet_key)
    _reset_for_tests()

    with _capture_crypto_logs(logging.ERROR) as records:
        result = decrypt_secret(token)

    assert result is None
    assert result != token
    assert any(
        "Token Fernet invalido" in record.getMessage()
        for record in records
        if record.levelno == logging.ERROR
    )


def test_decrypt_token_without_key_returns_none(monkeypatch, fernet_key):
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)
    token = encrypt_secret("sensitive")

    monkeypatch.delenv(_ENV_KEY_NAME, raising=False)
    _reset_for_tests()

    with _capture_crypto_logs(logging.ERROR) as records:
        result = decrypt_secret(token)

    assert result is None
    assert any(
        "no esta definida" in record.getMessage()
        for record in records
        if record.levelno == logging.ERROR
    )


def test_is_encrypted_helper():
    assert is_encrypted("gAAAAA123") is True
    assert is_encrypted("plain text") is False
    assert is_encrypted("") is False
    assert is_encrypted(None) is False


def test_encrypt_normalizes_none_and_empty():
    assert encrypt_secret(None) is None
    assert encrypt_secret("") == ""


# ---------------------------------------------------------------------------
# Clave de transporte: la que cifra la contrasena que sale en el snapshot
# ---------------------------------------------------------------------------
def test_transporte_cifra_con_su_propia_clave_no_con_la_de_reposo(
    monkeypatch, fernet_key, other_fernet_key
):
    """El token publicado lo abre el consumidor, no esta aplicacion.

    Es lo que mantiene los dos reposos independientes: rotar la clave del
    consumidor no obliga a re-cifrar esta base.
    """
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)
    monkeypatch.setenv(_TRANSPORT_ENV_KEY_NAME, other_fernet_key)
    _reset_for_tests()

    token = encrypt_for_transport("sensitive")

    assert token is not None
    assert Fernet(other_fernet_key.encode()).decrypt(token.encode()) == b"sensitive"
    with pytest.raises(Exception):
        Fernet(fernet_key.encode()).decrypt(token.encode())


def test_transporte_sin_clave_devuelve_none_nunca_el_claro(monkeypatch, fernet_key):
    """Fail-closed: sin clave de transporte no se publica nada.

    No hay passthrough como en el reposo, porque aqui su efecto seria publicar
    la contrasena en claro hacia afuera.
    """
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)
    monkeypatch.delenv(_TRANSPORT_ENV_KEY_NAME, raising=False)
    _reset_for_tests()

    with _capture_crypto_logs(logging.ERROR) as records:
        result = encrypt_for_transport("sensitive")

    assert result is None
    assert any(
        _TRANSPORT_ENV_KEY_NAME in record.getMessage()
        for record in records
        if record.levelno == logging.ERROR
    )


def test_transporte_no_es_determinista_pero_siempre_abre(monkeypatch, fernet_key, other_fernet_key):
    """Dos llamadas dan tokens distintos y ambos descifran al mismo secreto.

    Fernet lleva IV y timestamp propios, asi que comparar tokens entre syncs no
    sirve para detectar si la contrasena cambio.
    """
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)
    monkeypatch.setenv(_TRANSPORT_ENV_KEY_NAME, other_fernet_key)
    _reset_for_tests()

    a, b = encrypt_for_transport("sensitive"), encrypt_for_transport("sensitive")
    f = Fernet(other_fernet_key.encode())

    assert a != b
    assert f.decrypt(a.encode()) == f.decrypt(b.encode()) == b"sensitive"


def test_transporte_ignora_vacios(monkeypatch, fernet_key, other_fernet_key):
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)
    monkeypatch.setenv(_TRANSPORT_ENV_KEY_NAME, other_fernet_key)
    _reset_for_tests()

    assert encrypt_for_transport(None) is None
    assert encrypt_for_transport("") is None
