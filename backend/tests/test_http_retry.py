from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
import requests

from app.clients.http_retry import retry_http


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self  # type: ignore[assignment]
            raise error


def _sequence(*items):
    """Devuelve un fn() que consume `items`: excepciones se lanzan, el resto se devuelve."""
    queue = list(items)
    calls: list[int] = []

    def fn():
        calls.append(len(calls))
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def _fake_sleep():
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)

    sleep.waits = waits  # type: ignore[attr-defined]
    return sleep


def test_connection_error_then_ok_retries_once():
    ok = _FakeResponse(200)
    fn = _sequence(requests.ConnectionError("boom"), ok)
    sleep = _fake_sleep()

    result = retry_http(fn, describe="t", sleep=sleep, base_wait=1.0, max_wait=5.0)

    assert result is ok
    assert len(fn.calls) == 2
    assert len(sleep.waits) == 1
    assert 0.0 <= sleep.waits[0] <= 1.0


def test_timeout_and_chunked_encoding_are_transient():
    ok = _FakeResponse(200)
    fn = _sequence(
        requests.Timeout("slow"),
        requests.exceptions.ChunkedEncodingError("cut"),
        ok,
    )
    sleep = _fake_sleep()

    assert retry_http(fn, describe="t", sleep=sleep) is ok
    assert len(fn.calls) == 3


def test_503_response_then_ok():
    ok = _FakeResponse(200)
    fn = _sequence(_FakeResponse(503), ok)
    sleep = _fake_sleep()

    assert retry_http(fn, describe="t", sleep=sleep) is ok
    assert len(fn.calls) == 2
    assert len(sleep.waits) == 1


def test_429_honors_retry_after_header():
    ok = _FakeResponse(200)
    fn = _sequence(_FakeResponse(429, headers={"Retry-After": "2"}), ok)
    sleep = _fake_sleep()

    # base_wait minusculo: sin Retry-After esperaria ~0.01 s; el header manda.
    retry_http(fn, describe="t", sleep=sleep, base_wait=0.01)

    assert sleep.waits == [2.0]
    assert sleep.waits[0] >= 2.0


def test_retry_after_is_capped():
    ok = _FakeResponse(200)
    fn = _sequence(_FakeResponse(503, headers={"Retry-After": "999"}), ok)
    sleep = _fake_sleep()

    retry_http(fn, describe="t", sleep=sleep, max_wait=1.0)

    assert sleep.waits == [3.0]  # max_wait * 3


def test_400_is_not_retried_and_is_returned():
    bad = _FakeResponse(400)
    fn = _sequence(bad)
    sleep = _fake_sleep()

    result = retry_http(fn, describe="t", sleep=sleep)

    assert result is bad
    assert len(fn.calls) == 1
    assert sleep.waits == []


def test_http_error_with_non_retryable_status_is_raised_immediately():
    error = requests.HTTPError("HTTP 404")
    error.response = _FakeResponse(404)  # type: ignore[assignment]
    fn = _sequence(error)
    sleep = _fake_sleep()

    with pytest.raises(requests.HTTPError):
        retry_http(fn, describe="t", sleep=sleep)
    assert len(fn.calls) == 1


def test_http_error_with_retryable_status_is_retried():
    error = requests.HTTPError("HTTP 502")
    error.response = _FakeResponse(502)  # type: ignore[assignment]
    ok = _FakeResponse(200)
    fn = _sequence(error, ok)
    sleep = _fake_sleep()

    assert retry_http(fn, describe="t", sleep=sleep) is ok
    assert len(fn.calls) == 2


def test_exhaustion_reraises_last_exception():
    fn = _sequence(
        requests.ConnectionError("1"),
        requests.ConnectionError("2"),
        requests.ConnectionError("3"),
    )
    sleep = _fake_sleep()

    with pytest.raises(requests.ConnectionError, match="3"):
        retry_http(fn, describe="t", sleep=sleep, max_attempts=3)
    assert len(fn.calls) == 3
    assert len(sleep.waits) == 2


def test_exhaustion_on_response_calls_raise_for_status():
    fn = _sequence(_FakeResponse(503), _FakeResponse(503), _FakeResponse(503))
    sleep = _fake_sleep()

    with pytest.raises(requests.HTTPError):
        retry_http(fn, describe="t", sleep=sleep, max_attempts=3)
    assert len(fn.calls) == 3


def test_backoff_without_jitter_is_exponential_and_capped():
    fn = _sequence(_FakeResponse(500), _FakeResponse(500), _FakeResponse(500), _FakeResponse(200))
    sleep = _fake_sleep()

    retry_http(fn, describe="t", sleep=sleep, max_attempts=4, base_wait=0.5, max_wait=1.5, jitter=False)

    assert sleep.waits == [0.5, 1.0, 1.5]


def test_logs_warning_per_retry():
    fn = _sequence(_FakeResponse(503), _FakeResponse(200))
    fake_logger = MagicMock(spec=logging.Logger)

    retry_http(fn, describe="Proveedor X GET /foo", sleep=_fake_sleep(), logger=fake_logger)

    assert fake_logger.warning.call_count == 1
    args = fake_logger.warning.call_args.args
    message = args[0] % args[1:]
    assert "Proveedor X GET /foo" in message
    assert "HTTP 503" in message
    assert "reintento 1/2" in message
