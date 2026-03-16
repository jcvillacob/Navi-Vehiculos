from __future__ import annotations

import io
import logging
import os
from typing import Any, BinaryIO
from uuid import uuid4

from minio import Minio
from minio.error import S3Error

_logger = logging.getLogger(__name__)

DEFAULT_MINIO_ENDPOINT = "minio:9000"
DEFAULT_MINIO_BUCKET = "motor-attachments"
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_READ_CHUNK_SIZE = 1024 * 1024

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def _get_client() -> Minio:
    return Minio(
        endpoint=os.getenv("MINIO_ENDPOINT", DEFAULT_MINIO_ENDPOINT),
        access_key=os.getenv("MINIO_ROOT_USER", "minioadmin"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "minioadmin"),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )


def _get_bucket() -> str:
    return os.getenv("MINIO_BUCKET", DEFAULT_MINIO_BUCKET)


def _max_file_bytes() -> int:
    raw = os.getenv("MOTOR_ATTACHMENTS_MAX_BYTES", str(DEFAULT_MAX_FILE_BYTES))
    try:
        return max(int(raw), 1)
    except ValueError as exc:
        raise RuntimeError("MOTOR_ATTACHMENTS_MAX_BYTES debe ser un entero.") from exc


def ensure_bucket() -> None:
    client = _get_client()
    bucket = _get_bucket()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        _logger.info("Bucket '%s' creado en MinIO.", bucket)


def _normalize_suffix(filename: str) -> str:
    from pathlib import Path as _Path

    suffix = _Path(filename or "").suffix.lower()
    if not suffix:
        return ""
    return suffix[:16] if len(suffix) > 16 else suffix


def upload_file(
    *,
    filename: str,
    content_type: str | None,
    fileobj: BinaryIO,
) -> tuple[str, str, str, int]:
    """Upload a file to MinIO.

    Returns (original_filename, object_name, content_type, file_size).
    """
    normalized_filename = (filename or "").strip()
    normalized_content_type = (content_type or "").strip().lower()

    if not normalized_filename:
        raise ValueError("Debes seleccionar un archivo.")
    if normalized_content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("Solo se permiten imagenes JPG/PNG/WEBP o archivos PDF.")

    max_bytes = _max_file_bytes()
    data = bytearray()
    while True:
        chunk = fileobj.read(FILE_READ_CHUNK_SIZE)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise ValueError(
                f"El archivo supera el maximo permitido de {max_bytes // (1024 * 1024)} MB."
            )

    file_size = len(data)
    object_name = f"{uuid4().hex}{_normalize_suffix(normalized_filename)}"

    client = _get_client()
    bucket = _get_bucket()
    ensure_bucket()

    client.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=io.BytesIO(data),
        length=file_size,
        content_type=normalized_content_type,
    )

    return normalized_filename, object_name, normalized_content_type, file_size


def download_file(object_name: str) -> tuple[BinaryIO, int]:
    """Download a file from MinIO. Returns (stream, file_size)."""
    client = _get_client()
    bucket = _get_bucket()
    try:
        response = client.get_object(bucket, object_name)
        data = response.read()
        response.close()
        response.release_conn()
        return io.BytesIO(data), len(data)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise FileNotFoundError("El archivo del adjunto no se encontro en el almacenamiento.") from exc
        raise


def delete_file(object_name: str) -> None:
    """Delete a file from MinIO. Silently ignores missing objects."""
    client = _get_client()
    bucket = _get_bucket()
    try:
        client.remove_object(bucket, object_name)
    except S3Error:
        _logger.warning("No se pudo eliminar el objeto '%s' del bucket '%s'.", object_name, bucket)


def file_exists(object_name: str) -> bool:
    """Check if a file exists in MinIO."""
    client = _get_client()
    bucket = _get_bucket()
    try:
        client.stat_object(bucket, object_name)
        return True
    except S3Error:
        return False
