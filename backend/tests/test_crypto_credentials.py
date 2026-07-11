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
    _reset_for_tests,
    decrypt_secret,
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


def test_decrypt_token_with_wrong_key_returns_raw(
    monkeypatch, fernet_key, other_fernet_key
):
    monkeypatch.setenv(_ENV_KEY_NAME, fernet_key)
    token = encrypt_secret("sensitive")

    monkeypatch.setenv(_ENV_KEY_NAME, other_fernet_key)
    _reset_for_tests()

    with _capture_crypto_logs(logging.WARNING) as records:
        result = decrypt_secret(token)

    assert result == token
    assert any(
        "Token Fernet invalido" in record.getMessage()
        for record in records
        if record.levelno == logging.WARNING
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
