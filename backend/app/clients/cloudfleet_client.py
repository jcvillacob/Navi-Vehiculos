"""
Cliente HTTP para la API publica de CloudFleet (https://fleet.cloudfleet.com/api/v1).

Solo se exponen los endpoints que necesita el calculo de disponibilidad:
  - GET /vehicles
  - GET /work-orders/

Autenticacion: header `Authorization: Bearer {api_key}`. La API key es global
(una sola por instalacion) y se carga desde env vars en app/core/config.py.

Manejo de errores:
  - 401/403  -> CloudFleetAuthError (no se reintenta)
  - 404      -> respuesta vacia valida (la API responde 404 cuando un rango
                de work-orders no tiene filas)
  - 429      -> dormir rate_limit_delay y reintentar (cuenta como retry)
  - 5xx/red  -> backoff exponencial 2^n s, hasta max_retries
                Si se agota la cuota -> CloudFleetUnavailableError
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Iterable

import requests

from app.core.config import CloudFleetConfig

_logger = logging.getLogger(__name__)


class CloudFleetAuthError(RuntimeError):
    """API key invalida o sin permisos. NO debe reintentarse."""


class CloudFleetUnavailableError(RuntimeError):
    """La API de CloudFleet no responde despues de agotar reintentos."""


def _headers(config: CloudFleetConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }


def _sleep_request_delay(config: CloudFleetConfig, *, first_call: bool) -> None:
    if first_call or config.request_delay <= 0:
        return
    time.sleep(config.request_delay)


def _request_with_retry(
    config: CloudFleetConfig,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    first_call: bool = False,
) -> requests.Response | None:
    """
    Ejecuta GET con reintentos. Devuelve None si la API respondio 404
    (lista vacia valida segun la doc). Levanta CloudFleetAuthError o
    CloudFleetUnavailableError en los casos terminales.
    """
    attempt = 0
    backoff = 1.0
    last_error: Exception | None = None
    _sleep_request_delay(config, first_call=first_call)

    while attempt <= config.max_retries:
        try:
            response = requests.get(
                url,
                headers=_headers(config),
                params=params,
                timeout=config.timeout,
            )
        except requests.exceptions.RequestException as exc:
            last_error = exc
            _logger.warning(
                "CloudFleet GET %s fallo (intento %d/%d): %s",
                url,
                attempt + 1,
                config.max_retries + 1,
                exc,
            )
            attempt += 1
            if attempt > config.max_retries:
                break
            time.sleep(backoff)
            backoff *= 2
            continue

        status = response.status_code
        if status in (401, 403):
            raise CloudFleetAuthError(
                f"CloudFleet rechazo la API key (HTTP {status}). "
                "Revisa CLOUDFLEET_API_KEY en el .env."
            )
        if status == 404:
            # Para /work-orders es una respuesta valida segun la doc; para
            # /vehicles suele significar que CLOUDFLEET_API_URL apunta a un
            # host equivocado. Loggeamos warning en ambos casos para no
            # esconder el problema.
            _logger.warning(
                "CloudFleet GET %s respondio HTTP 404 (sin datos o URL erronea). "
                "URL base esperada: https://fleet.cloudfleet.com/api/v1",
                url,
            )
            return None
        if status == 429:
            _logger.warning(
                "CloudFleet rate-limit (HTTP 429) en %s. Esperando %.1fs.",
                url,
                config.rate_limit_delay,
            )
            time.sleep(config.rate_limit_delay)
            attempt += 1
            continue
        if 500 <= status < 600:
            last_error = RuntimeError(
                f"CloudFleet HTTP {status}: {response.text[:200]}"
            )
            _logger.warning(
                "CloudFleet GET %s respondio HTTP %d (intento %d/%d).",
                url,
                status,
                attempt + 1,
                config.max_retries + 1,
            )
            attempt += 1
            if attempt > config.max_retries:
                break
            time.sleep(backoff)
            backoff *= 2
            continue
        if 200 <= status < 300:
            return response
        # Cualquier otro status (4xx) lo tratamos como error duro no reintentable.
        raise CloudFleetUnavailableError(
            f"CloudFleet GET {url} respondio HTTP {status}: {response.text[:200]}"
        )

    raise CloudFleetUnavailableError(
        f"CloudFleet no respondio despues de {config.max_retries + 1} intentos: {last_error}"
    )


def _paginate(
    config: CloudFleetConfig,
    initial_url: str,
    *,
    initial_params: dict[str, Any] | None = None,
) -> Iterable[Any]:
    """
    Itera todas las paginas siguiendo el header `X-NextPage`.
    Si la primera pagina responde 404, no produce nada.
    """
    url: str | None = initial_url
    params: dict[str, Any] | None = initial_params
    first_call = True
    while url is not None:
        response = _request_with_retry(config, url, params=params, first_call=first_call)
        first_call = False
        if response is None:
            return
        payload = response.json() if response.content else []
        if isinstance(payload, list):
            yield from payload
        elif isinstance(payload, dict):
            # Algunas respuestas vienen envueltas; intentamos extraer 'data' o similar.
            for key in ("data", "items", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    yield from value
                    break
        next_url = response.headers.get("X-NextPage")
        url = next_url.strip() if isinstance(next_url, str) and next_url.strip() else None
        # Las paginas siguientes ya traen los filtros embebidos en X-NextPage.
        params = None


def list_vehicles(config: CloudFleetConfig) -> list[dict[str, Any]]:
    """Devuelve el inventario completo de vehiculos. Pagina por X-NextPage."""
    url = f"{config.base_url}/vehicles"
    return [item for item in _paginate(config, url) if isinstance(item, dict)]


def list_work_orders(
    config: CloudFleetConfig,
    *,
    updated_at_from: datetime,
    updated_at_to: datetime,
) -> list[dict[str, Any]]:
    """
    Devuelve work-orders cuyo `updatedAt` cae en el rango pedido.
    La doc aclara que HTTP 404 en este endpoint significa "sin datos en el
    rango" — _paginate ya lo trata como lista vacia.
    """
    url = f"{config.base_url}/work-orders/"
    params = {
        "updatedAtFrom": updated_at_from.isoformat(),
        "updatedAtTo": updated_at_to.isoformat(),
    }
    return [item for item in _paginate(config, url, initial_params=params) if isinstance(item, dict)]
