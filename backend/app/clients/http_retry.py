"""Reintentos HTTP con backoff exponencial y jitter para los clientes GPS.

`retry_http` es una función pura: recibe un callable que hace UNA petición y
lo reintenta ante errores transitorios (conexión, timeout, 429/5xx). El
`sleep` es inyectable para que los tests no esperen de verdad.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Iterable

import requests

DEFAULT_RETRY_STATUSES: tuple[int, ...] = (429, 500, 502, 503, 504)
_TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

_module_logger = logging.getLogger(__name__)


def _status_of(response: Any) -> int | None:
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _retry_after_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:  # pragma: no cover - headers exóticos
        return None
    if raw in (None, ""):
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return max(0.0, value)


def retry_http(
    fn: Callable[[], Any],
    *,
    describe: str,
    max_attempts: int = 3,
    base_wait: float = 1.0,
    max_wait: float = 20.0,
    retry_statuses: Iterable[int] = DEFAULT_RETRY_STATUSES,
    sleep: Callable[[float], None] = time.sleep,
    logger: logging.Logger | None = None,
    jitter: bool = True,
    retry_after_max: float | None = None,
) -> Any:
    """Ejecuta `fn()` reintentando ante fallos transitorios.

    - Reintenta si `fn` lanza ConnectionError / Timeout / ChunkedEncodingError.
    - Reintenta si `fn` devuelve una Response (o algo con `status_code`) cuyo
      status está en `retry_statuses`, o si lanza HTTPError con esa Response.
    - Honra `Retry-After` (segundos) en 429/503, topado en `retry_after_max`
      (por defecto `max_wait * 3`).
    - Backoff exponencial con full jitter: uniform(0, min(max_wait, base*2**n)).
      Con `jitter=False` espera exactamente min(max_wait, base*2**n).
    - Al agotar intentos re-lanza la última excepción, o llama
      `raise_for_status()` sobre la última respuesta (y la devuelve si no lanza).
    """
    log = logger or _module_logger
    statuses = set(int(s) for s in retry_statuses)
    attempts = max(1, int(max_attempts))
    after_cap = retry_after_max if retry_after_max is not None else max_wait * 3

    last_exc: BaseException | None = None
    last_response: Any = None

    for attempt in range(attempts):
        last_exc = None
        last_response = None
        reason = ""
        status: int | None = None
        try:
            result = fn()
        except _TRANSIENT_EXCEPTIONS as exc:
            last_exc = exc
            reason = f"{type(exc).__name__}: {exc}"
        except requests.HTTPError as exc:
            status = _status_of(getattr(exc, "response", None))
            if status is None or status not in statuses:
                raise
            last_exc = exc
            last_response = exc.response
            reason = f"HTTP {status}"
        else:
            status = _status_of(result)
            if status is None or status not in statuses:
                return result
            last_response = result
            reason = f"HTTP {status}"

        is_last = attempt >= attempts - 1
        if is_last:
            break

        wait = min(max_wait, base_wait * (2**attempt))
        if jitter:
            wait = random.uniform(0.0, wait)
        if status in (429, 503) and last_response is not None:
            retry_after = _retry_after_seconds(last_response)
            if retry_after is not None:
                wait = min(retry_after, after_cap)

        log.warning(
            "%s: reintento %d/%d tras %s; esperando %.2fs",
            describe,
            attempt + 1,
            attempts - 1,
            reason,
            wait,
        )
        sleep(wait)

    if last_exc is not None:
        raise last_exc
    if last_response is not None:
        raise_for_status = getattr(last_response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        return last_response
    raise RuntimeError(f"{describe}: retry_http terminó sin resultado")  # pragma: no cover
