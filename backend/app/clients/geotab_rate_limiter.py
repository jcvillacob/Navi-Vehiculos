"""Limitador de tasa en proceso (token bucket) para llamadas a la API de Geotab.

Modelo
------
Geotab aplica una cuota de ~1000 llamadas por minuto por base de datos (y usuario)
y responde ``OverLimitException`` cuando se excede. Varios consumidores dentro del
mismo proceso comparten las mismas credenciales: el job mensual de rendimientos
(multi-hilo, varios workers por base), ``geotab_taller_sync`` (cada 30 min), el
snapshot de conexion (diario) y las consultas CPK on-demand. Este modulo ofrece un
token bucket por clave (normalmente la base de datos) compartido por todos ellos:

* Cada bucket tiene ``capacity`` tokens (rafaga maxima) y se rellena de forma
  continua a ``refill_per_minute / 60`` tokens por segundo, con tope en ``capacity``.
* ``acquire(key, cost)`` bloquea hasta tener ``cost`` tokens (un ``multi_call`` de
  N Gets debe cobrar N, porque Geotab cuenta cada llamada interna).
* ``penalize(key, seconds)`` se invoca cuando el servidor respondio
  ``OverLimitException``: vacia el bucket y bloquea la clave hasta ``now+seconds``.
* Thread-safe: un ``threading.Lock`` global protege el estado; las esperas se hacen
  FUERA del lock con el ``sleep`` inyectado y se re-verifican en bucle.

El reloj y el sleep son inyectables (``clock``/``sleep``) para poder testear sin
esperas reales.

Variables de entorno (solo aplican al singleton ``get_rate_limiter()``)
----------------------------------------------------------------------
* ``GEOTAB_RATE_LIMIT_PER_MINUTE``  tokens por minuto (default 900; margen bajo 1000).
* ``GEOTAB_RATE_LIMIT_BURST``       capacidad del bucket (default = per-minute).
* ``GEOTAB_RATE_LIMIT_PER_USER``    si es ``1``/``true``, ``limiter_key`` incluye el
  usuario (cuota por base+usuario). Por defecto solo la base, para que usuarios
  paralelos compartan la cuota de forma conservadora.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import threading
import time
from typing import Callable

_logger = logging.getLogger(__name__)

_DEFAULT_PER_MINUTE = 900
_SLOW_WAIT_WARN_SECONDS = 5.0
# Espera maxima por iteracion del bucle: evita dormir eternamente si otro hilo
# consume tokens o si llega una penalizacion mientras esperamos.
_MAX_SLICE_SECONDS = 5.0
# Espera minima por iteracion: evita spin cuando el deficit es de punto flotante
# (ej. 1e-13 s) y el reloj no alcanza a avanzar.
_MIN_SLICE_SECONDS = 0.001


class RateLimitTimeout(TimeoutError):
    """Se supero el ``timeout`` esperando tokens en ``acquire``."""


@dataclass
class _Bucket:
    tokens: float
    last_refill: float
    blocked_until: float = 0.0
    waits_total: float = 0.0
    acquired_total: int = 0
    penalties_total: int = 0
    waits_count: int = 0
    extra: dict = field(default_factory=dict)


class GeotabRateLimiter:
    def __init__(
        self,
        *,
        capacity: int = _DEFAULT_PER_MINUTE,
        refill_per_minute: int = _DEFAULT_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity debe ser > 0")
        if refill_per_minute <= 0:
            raise ValueError("refill_per_minute debe ser > 0")
        self.capacity = float(capacity)
        self.refill_per_minute = float(refill_per_minute)
        self._rate = self.refill_per_minute / 60.0  # tokens por segundo
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    # ------------------------------------------------------------------ interno
    def _get_bucket(self, key: str, now: float) -> _Bucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, last_refill=now)
            self._buckets[key] = bucket
        return bucket

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = now - bucket.last_refill
        if elapsed > 0:
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self._rate)
        bucket.last_refill = now

    def _try_take(self, key: str, cost: float) -> tuple[bool, float]:
        """Intenta consumir ``cost`` tokens. Devuelve (ok, segundos_a_esperar).

        Debe llamarse con el lock tomado.
        """
        now = self._clock()
        bucket = self._get_bucket(key, now)
        self._refill(bucket, now)
        if now < bucket.blocked_until:
            return False, bucket.blocked_until - now
        if bucket.tokens >= cost:
            bucket.tokens -= cost
            bucket.acquired_total += int(cost)
            return True, 0.0
        deficit = cost - bucket.tokens
        return False, deficit / self._rate

    @staticmethod
    def _check_cost(cost: int) -> float:
        if cost <= 0:
            raise ValueError("cost debe ser >= 1")
        return float(cost)

    # ------------------------------------------------------------------ API
    def acquire(self, key: str, cost: int = 1, *, timeout: float | None = None) -> float:
        """Bloquea hasta tener ``cost`` tokens para ``key``; devuelve segundos esperados.

        Lanza ``RateLimitTimeout`` si la espera acumulada superaria ``timeout``.
        """
        cost_f = self._check_cost(cost)
        if cost_f > self.capacity:
            raise ValueError(
                f"cost={cost} excede la capacidad del bucket ({int(self.capacity)})"
            )
        waited = 0.0
        while True:
            with self._lock:
                ok, wait_for = self._try_take(key, cost_f)
                if ok:
                    if waited > 0:
                        bucket = self._buckets[key]
                        bucket.waits_total += waited
                        bucket.waits_count += 1
                    break
            if timeout is not None and waited + wait_for > timeout:
                raise RateLimitTimeout(
                    f"geotab rate limiter: timeout ({timeout:.1f}s) esperando "
                    f"{cost} token(s) para '{key}' (esperado {waited:.1f}s, "
                    f"faltaban {wait_for:.1f}s)"
                )
            slice_ = min(max(wait_for, _MIN_SLICE_SECONDS), _MAX_SLICE_SECONDS)
            self._sleep(slice_)
            waited += slice_
        if waited > 0:
            level = logging.WARNING if waited > _SLOW_WAIT_WARN_SECONDS else logging.DEBUG
            _logger.log(
                level,
                "geotab rate limiter: esperados %.2fs por %d token(s) en '%s'",
                waited,
                cost,
                key,
            )
        return waited

    def try_acquire(self, key: str, cost: int = 1) -> bool:
        cost_f = self._check_cost(cost)
        with self._lock:
            ok, _ = self._try_take(key, cost_f)
        return ok

    def penalize(self, key: str, seconds: float = 60.0) -> None:
        """El servidor respondio OverLimitException: vacia el bucket y bloquea la clave."""
        with self._lock:
            now = self._clock()
            bucket = self._get_bucket(key, now)
            self._refill(bucket, now)
            bucket.tokens = 0.0
            bucket.blocked_until = max(bucket.blocked_until, now + max(0.0, seconds))
            bucket.penalties_total += 1
            blocked_for = bucket.blocked_until - now
        _logger.warning(
            "geotab rate limiter: OverLimit en '%s'; bucket vaciado y bloqueado %.0fs",
            key,
            blocked_for,
        )

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            now = self._clock()
            out: dict[str, dict] = {}
            for key, bucket in self._buckets.items():
                self._refill(bucket, now)
                out[key] = {
                    "tokens": round(bucket.tokens, 3),
                    "capacity": int(self.capacity),
                    "blocked_until": bucket.blocked_until,
                    "blocked_for": round(max(0.0, bucket.blocked_until - now), 3),
                    "waits_total": round(bucket.waits_total, 3),
                    "waits_count": bucket.waits_count,
                    "acquired_total": bucket.acquired_total,
                    "penalties_total": bucket.penalties_total,
                }
            return out


# ---------------------------------------------------------------------- config
def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _logger.warning("%s=%r invalido; usando %d", name, raw, default)
        return default
    if value <= 0:
        _logger.warning("%s=%d debe ser > 0; usando %d", name, value, default)
        return default
    return value


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def build_rate_limiter_from_env(**overrides) -> GeotabRateLimiter:
    per_minute = _env_int("GEOTAB_RATE_LIMIT_PER_MINUTE", _DEFAULT_PER_MINUTE)
    burst = _env_int("GEOTAB_RATE_LIMIT_BURST", per_minute)
    return GeotabRateLimiter(capacity=burst, refill_per_minute=per_minute, **overrides)


_singleton: GeotabRateLimiter | None = None
_singleton_lock = threading.Lock()


def get_rate_limiter() -> GeotabRateLimiter:
    """Singleton a nivel de modulo configurado desde variables de entorno."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = build_rate_limiter_from_env()
                _logger.debug(
                    "geotab rate limiter inicializado: capacity=%d, refill=%d/min",
                    _singleton.capacity,
                    _singleton.refill_per_minute,
                )
    return _singleton


def reset_rate_limiter() -> None:
    """Descarta el singleton (util en tests o al recargar configuracion)."""
    global _singleton
    with _singleton_lock:
        _singleton = None


def limiter_key(database: str, username: str | None = None) -> str:
    """Clave normalizada del bucket: base en minusculas; con usuario si
    GEOTAB_RATE_LIMIT_PER_USER esta activo."""
    db = (database or "").strip().lower()
    if _env_flag("GEOTAB_RATE_LIMIT_PER_USER") and username:
        return f"{db}|{username.strip().lower()}"
    return db
