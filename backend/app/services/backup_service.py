"""Backups locales de PostgreSQL en formato custom de pg_dump."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

_BACKUP_PREFIX = "navi_vehiculos_"
_BACKUP_PATTERN = re.compile(
    rf"^{re.escape(_BACKUP_PREFIX)}(?P<timestamp>\d{{8}}_\d{{6}})_(?P<trigger>daily|manual)\.dump$"
)
_BACKUP_LOCK = threading.Lock()


class BackupError(RuntimeError):
    """Error controlado al generar o leer un respaldo."""


def _backup_dir() -> Path:
    return Path(os.getenv("BACKUP_DIR", "/backups")).expanduser()


def _retention_days() -> int:
    try:
        return max(1, int(os.getenv("BACKUP_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


def _backup_timeout_seconds() -> int:
    try:
        return max(60, int(os.getenv("BACKUP_TIMEOUT_SECONDS", "1800")))
    except ValueError:
        return 1800


def _database_connection() -> tuple[list[str], str]:
    raw_url = os.getenv("DATABASE_URL", "").strip()
    if not raw_url:
        raise BackupError("DATABASE_URL no esta configurada.")

    normalized_url = raw_url.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(normalized_url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise BackupError("DATABASE_URL no es una URL PostgreSQL valida.")

    host = parsed.hostname
    database = unquote(parsed.path.lstrip("/"))
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if not host or not database or not username:
        raise BackupError("DATABASE_URL debe incluir host, usuario y base de datos.")

    command = ["pg_dump", "--format=custom", "--no-owner", "--no-privileges"]
    command.extend(["--host", host, "--username", username, "--dbname", database])
    if parsed.port:
        command.extend(["--port", str(parsed.port)])

    return command, password


def _record(path: Path) -> dict[str, Any]:
    match = _BACKUP_PATTERN.match(path.name)
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return {
        "filename": path.name,
        "size_bytes": stat.st_size,
        "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "sha256": digest.hexdigest(),
        "trigger": match.group("trigger") if match else "unknown",
    }


def list_backups() -> list[dict[str, Any]]:
    directory = _backup_dir()
    if not directory.exists():
        return []

    files = [
        path
        for path in directory.iterdir()
        if path.is_file() and _BACKUP_PATTERN.match(path.name)
    ]
    return [_record(path) for path in sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)]


def _prune_old_backups(directory: Path) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_retention_days())
    removed = 0
    for path in directory.iterdir():
        if not path.is_file() or not _BACKUP_PATTERN.match(path.name):
            continue
        created_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if created_at < cutoff:
            path.unlink()
            removed += 1
    return removed


def create_postgres_backup(trigger: str = "manual") -> dict[str, Any]:
    """Genera un backup atomico y devuelve sus metadatos."""
    if trigger not in {"daily", "manual"}:
        raise BackupError(f"Trigger de backup no soportado: {trigger}")

    with _BACKUP_LOCK:
        directory = _backup_dir()
        directory.mkdir(parents=True, exist_ok=True)
        command, password = _database_connection()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{_BACKUP_PREFIX}{timestamp}_{trigger}.dump"
        target = directory / filename
        temporary = directory / f".{filename}.part"

        environment = os.environ.copy()
        environment["PGPASSWORD"] = password
        try:
            result = subprocess.run(
                [*command, "--file", str(temporary)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=_backup_timeout_seconds(),
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "pg_dump fallo").strip()
                raise BackupError(detail[-1000:])
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise BackupError("pg_dump termino sin generar un archivo valido.")

            temporary.replace(target)
            removed = _prune_old_backups(directory)
            record = _record(target)
            logger.info(
                "Backup PostgreSQL creado: %s (%d bytes, trigger=%s, purgados=%d)",
                record["filename"],
                record["size_bytes"],
                trigger,
                removed,
            )
            return record
        except subprocess.TimeoutExpired as exc:
            raise BackupError("pg_dump excedio el tiempo maximo configurado.") from exc
        finally:
            temporary.unlink(missing_ok=True)
