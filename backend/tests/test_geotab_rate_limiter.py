"""Tests del token bucket de Geotab (sin esperas reales: reloj y sleep falsos)."""

from __future__ import annotations

import logging
import threading

import pytest

from app.clients import geotab_rate_limiter as mod
from app.clients.geotab_rate_limiter import (
    GeotabRateLimiter,
    RateLimitTimeout,
    get_rate_limiter,
    limiter_key,
    reset_rate_limiter,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


def make(capacity=10, per_minute=60, start=1000.0):
    """capacity=10 y 60/min => 1 token por segundo; facil de razonar."""
    clock = FakeClock(start)
    limiter = GeotabRateLimiter(
        capacity=capacity, refill_per_minute=per_minute, clock=clock, sleep=clock.sleep
    )
    return limiter, clock


# --------------------------------------------------------------------- basicos
def test_burst_then_wait_computed():
    limiter, clock = make()
    for _ in range(10):
        assert limiter.acquire("db") == 0.0
    assert clock.sleeps == []
    # bucket vacio: el siguiente token tarda 1 s a 1 tok/s
    waited = limiter.acquire("db")
    assert waited == pytest.approx(1.0)
    assert clock.sleeps == [pytest.approx(1.0)]
    snap = limiter.snapshot()["db"]
    assert snap["acquired_total"] == 11
    assert snap["waits_total"] == pytest.approx(1.0)
    assert snap["waits_count"] == 1
    assert snap["tokens"] == pytest.approx(0.0)


def test_cost_greater_than_one():
    limiter, clock = make()
    assert limiter.acquire("db", cost=7) == 0.0
    assert limiter.snapshot()["db"]["tokens"] == pytest.approx(3.0)
    # faltan 4 tokens para cost=7 => 4 s
    assert limiter.acquire("db", cost=7) == pytest.approx(4.0)
    assert limiter.snapshot()["db"]["acquired_total"] == 14
    with pytest.raises(ValueError):
        limiter.acquire("db", cost=11)  # excede capacidad: nunca se satisfaria
    with pytest.raises(ValueError):
        limiter.acquire("db", cost=0)


def test_refill_over_time_capped_at_capacity():
    limiter, clock = make()
    for _ in range(10):
        limiter.acquire("db")
    clock.advance(3.5)
    assert limiter.snapshot()["db"]["tokens"] == pytest.approx(3.5)
    clock.advance(600)
    assert limiter.snapshot()["db"]["tokens"] == pytest.approx(10.0)
    # tras el rellenado completo vuelve a haber rafaga sin espera
    for _ in range(10):
        assert limiter.acquire("db") == 0.0


def test_per_key_isolation():
    limiter, clock = make()
    for _ in range(10):
        limiter.acquire("db_a")
    assert limiter.try_acquire("db_a") is False
    assert limiter.try_acquire("db_b") is True
    assert limiter.acquire("db_b") == 0.0
    snap = limiter.snapshot()
    assert snap["db_a"]["acquired_total"] == 10
    assert snap["db_b"]["acquired_total"] == 2
    assert snap["db_b"]["tokens"] == pytest.approx(8.0)


def test_try_acquire_false_when_empty():
    limiter, clock = make(capacity=2)
    assert limiter.try_acquire("db", cost=2) is True
    assert limiter.try_acquire("db") is False
    assert clock.sleeps == []  # try_acquire nunca duerme
    clock.advance(1.0)
    assert limiter.try_acquire("db") is True


# --------------------------------------------------------------------- penalize
def test_penalize_blocks_then_releases():
    limiter, clock = make()
    limiter.acquire("db")  # tokens=9
    limiter.penalize("db", seconds=60)
    snap = limiter.snapshot()["db"]
    assert snap["tokens"] == pytest.approx(0.0)
    assert snap["blocked_for"] == pytest.approx(60.0)
    assert snap["penalties_total"] == 1
    assert limiter.try_acquire("db") is False
    # aun con tiempo transcurrido (tokens rellenados) sigue bloqueado hasta blocked_until
    clock.advance(30)
    assert limiter.try_acquire("db") is False
    # acquire duerme hasta blocked_until (30 s restantes); ya hay tokens rellenados
    waited = limiter.acquire("db")
    assert waited == pytest.approx(30.0)
    assert limiter.snapshot()["db"]["blocked_for"] == 0.0
    # otra clave no se ve afectada
    assert limiter.try_acquire("other") is True


def test_penalize_does_not_shorten_existing_block():
    limiter, clock = make()
    limiter.penalize("db", seconds=60)
    limiter.penalize("db", seconds=10)
    assert limiter.snapshot()["db"]["blocked_for"] == pytest.approx(60.0)
    assert limiter.snapshot()["db"]["penalties_total"] == 2


# --------------------------------------------------------------------- timeout
def test_timeout_raises_without_sleeping_past_it():
    limiter, clock = make()
    for _ in range(10):
        limiter.acquire("db")
    with pytest.raises(RateLimitTimeout):
        limiter.acquire("db", cost=5, timeout=2.0)  # necesitaria 5 s
    assert clock.sleeps == []  # no duerme si ya sabe que no alcanza
    # con timeout suficiente si espera
    assert limiter.acquire("db", cost=5, timeout=5.0) == pytest.approx(5.0)


def test_timeout_none_waits_in_slices_and_logs_warning(caplog):
    limiter, clock = make()
    for _ in range(10):
        limiter.acquire("db")
    with caplog.at_level(logging.DEBUG, logger="app.clients.geotab_rate_limiter"):
        waited = limiter.acquire("db", cost=8)  # 8 s > 5 s => WARNING
    assert waited == pytest.approx(8.0)
    assert len(clock.sleeps) >= 2  # dividido en slices de max 5 s
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("esperados 8.00s" in r.getMessage() for r in warnings)


def test_short_wait_logs_debug_only(caplog):
    limiter, clock = make()
    for _ in range(10):
        limiter.acquire("db")
    with caplog.at_level(logging.DEBUG, logger="app.clients.geotab_rate_limiter"):
        limiter.acquire("db")
    assert all(r.levelno == logging.DEBUG for r in caplog.records)
    assert caplog.records


# --------------------------------------------------------------------- threads
def test_thread_safety_no_over_consumption():
    """10 hilos x 100 acquires con capacidad exacta: sin sleep y sin sobreconsumo."""
    frozen = FakeClock()

    def no_sleep(_seconds):  # pragma: no cover - fallaria el test si se llama
        raise AssertionError("no deberia dormir: capacidad exacta")

    limiter = GeotabRateLimiter(
        capacity=1000, refill_per_minute=60, clock=frozen, sleep=no_sleep
    )
    errors: list[BaseException] = []

    def worker():
        try:
            for _ in range(100):
                limiter.acquire("db")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    snap = limiter.snapshot()["db"]
    assert snap["acquired_total"] == 1000
    assert snap["tokens"] == pytest.approx(0.0)
    assert limiter.try_acquire("db") is False


def test_thread_safety_with_waits_on_shared_fake_clock():
    """Capacidad pequena: los hilos deben esperar (sleep falso avanza reloj compartido)."""
    clock = FakeClock()
    # 50 de rafaga, 6000/min = 100 tok/s
    limiter = GeotabRateLimiter(
        capacity=50, refill_per_minute=6000, clock=clock, sleep=clock.sleep
    )
    errors: list[BaseException] = []

    def worker():
        try:
            for _ in range(100):
                limiter.acquire("db", timeout=120)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    snap = limiter.snapshot()["db"]
    assert snap["acquired_total"] == 1000
    assert snap["waits_count"] >= 1
    # el reloj falso tuvo que avanzar al menos (1000-50)/100 s para rellenar
    assert clock.now - 1000.0 >= 9.5 * 0.95


# --------------------------------------------------------------------- env
@pytest.fixture
def fresh_singleton():
    reset_rate_limiter()
    yield
    reset_rate_limiter()


def test_env_defaults(monkeypatch, fresh_singleton):
    monkeypatch.delenv("GEOTAB_RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.delenv("GEOTAB_RATE_LIMIT_BURST", raising=False)
    limiter = get_rate_limiter()
    assert limiter.capacity == 900
    assert limiter.refill_per_minute == 900
    assert get_rate_limiter() is limiter  # singleton


def test_env_config_parsing(monkeypatch, fresh_singleton):
    monkeypatch.setenv("GEOTAB_RATE_LIMIT_PER_MINUTE", "600")
    monkeypatch.delenv("GEOTAB_RATE_LIMIT_BURST", raising=False)
    limiter = get_rate_limiter()
    assert limiter.refill_per_minute == 600
    assert limiter.capacity == 600  # burst default = per-minute

    reset_rate_limiter()
    monkeypatch.setenv("GEOTAB_RATE_LIMIT_BURST", "120")
    limiter = get_rate_limiter()
    assert limiter.refill_per_minute == 600
    assert limiter.capacity == 120


def test_env_invalid_values_fall_back(monkeypatch, fresh_singleton, caplog):
    monkeypatch.setenv("GEOTAB_RATE_LIMIT_PER_MINUTE", "abc")
    monkeypatch.setenv("GEOTAB_RATE_LIMIT_BURST", "-5")
    with caplog.at_level(logging.WARNING, logger="app.clients.geotab_rate_limiter"):
        limiter = get_rate_limiter()
    assert limiter.refill_per_minute == 900
    assert limiter.capacity == 900
    assert len([r for r in caplog.records if "invalido" in r.getMessage()]) == 1
    assert len([r for r in caplog.records if "debe ser > 0" in r.getMessage()]) == 1


def test_limiter_key_normalization(monkeypatch):
    monkeypatch.delenv("GEOTAB_RATE_LIMIT_PER_USER", raising=False)
    assert limiter_key(" Navitrans ") == "navitrans"
    assert limiter_key("Navitrans", "User@x.com") == "navitrans"
    monkeypatch.setenv("GEOTAB_RATE_LIMIT_PER_USER", "1")
    assert limiter_key("Navitrans", "User@x.com") == "navitrans|user@x.com"
    assert limiter_key("Navitrans", None) == "navitrans"
    monkeypatch.setenv("GEOTAB_RATE_LIMIT_PER_USER", "0")
    assert limiter_key("Navitrans", "u") == "navitrans"


def test_constructor_validation():
    with pytest.raises(ValueError):
        GeotabRateLimiter(capacity=0)
    with pytest.raises(ValueError):
        GeotabRateLimiter(refill_per_minute=0)
    assert mod._DEFAULT_PER_MINUTE == 900
