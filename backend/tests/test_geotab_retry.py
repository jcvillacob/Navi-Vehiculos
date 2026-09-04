"""Politica de reintentos del cliente Geotab (sin DB, sin red).

Cubre `_classify_error`, `_call_with_retry` (via `_api_call_with_retry` y
`multi_call_with_retry`) y el fallback condicional de `get_month_data_bundle`.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from mygeotab.exceptions import MyGeotabException, TimeoutException

from app.clients import geotab_client
from app.clients.geotab_client import (
    _api_call_with_retry,
    _classify_error,
    get_month_data_bundle,
    multi_call_with_retry,
)


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------


def geotab_error(name: str, message: str = "boom") -> MyGeotabException:
    return MyGeotabException({"errors": [{"name": name, "message": message}]})


def http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} error", response=response)


class FakeAuthenticationError(Exception):
    """Nombre contiene 'Authentication' -> clasificada como auth."""


class ScriptedAPI:
    """`call`/`multi_call` consumen una secuencia de excepciones y luego devuelven `result`."""

    def __init__(self, script: list[BaseException], result: Any = None):
        self.script = list(script)
        self.result = result
        self.attempts = 0
        self.authenticate_calls = 0
        self.credentials = type("Creds", (), {"username": "user", "database": "test_db"})()

    def _next(self):
        self.attempts += 1
        if self.script:
            raise self.script.pop(0)
        return self.result

    def call(self, method, **kwargs):
        return self._next()

    def multi_call(self, calls):
        return self._next()

    def authenticate(self):
        self.authenticate_calls += 1


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(geotab_client._time, "sleep", lambda seconds: recorded.append(seconds))
    return recorded


# ------------------------------------------------------------------------------
# _classify_error
# ------------------------------------------------------------------------------


class TestClassifyError:
    @pytest.mark.parametrize(
        "exc",
        [
            geotab_error("OverLimitException", "API calls quota exceeded"),
            http_error(429),
        ],
    )
    def test_rate_limit(self, exc):
        assert _classify_error(exc) == "rate_limit"

    @pytest.mark.parametrize(
        "exc",
        [
            geotab_error("DbUnavailableException"),
            geotab_error("ServerException"),
            geotab_error("TimeoutException"),
            TimeoutException("my.geotab.com"),
            http_error(500),
            http_error(502),
            http_error(503),
            http_error(504),
            requests.exceptions.ConnectionError("conn"),
            requests.exceptions.Timeout("slow"),
            requests.exceptions.ChunkedEncodingError("chunk"),
            RuntimeError("HTTPSConnectionPool: Max retries exceeded"),
        ],
    )
    def test_transient(self, exc):
        assert _classify_error(exc) == "transient"

    @pytest.mark.parametrize(
        "exc",
        [
            FakeAuthenticationError("nope"),
            geotab_error("InvalidUserException", "Incorrect MyGeotab login credentials"),
            RuntimeError("session has expired"),
        ],
    )
    def test_auth(self, exc):
        assert _classify_error(exc) == "auth"

    @pytest.mark.parametrize(
        "exc",
        [
            http_error(400),
            http_error(403),
            geotab_error("ArgumentException", "Invalid typeName"),
            geotab_error("MissingMethodException"),
            ValueError("bad"),
        ],
    )
    def test_fatal(self, exc):
        assert _classify_error(exc) == "fatal"


# ------------------------------------------------------------------------------
# _call_with_retry via _api_call_with_retry / multi_call_with_retry
# ------------------------------------------------------------------------------


class TestRetryPolicy:
    def test_over_limit_retried_with_wait_at_least_60s(self, sleeps):
        api = ScriptedAPI([geotab_error("OverLimitException")], result=[{"id": 1}])

        out = _api_call_with_retry(api, "Get", typeName="Device")

        assert out == [{"id": 1}]
        assert api.attempts == 2
        assert len(sleeps) == 1
        assert sleeps[0] >= 60
        assert sleeps[0] <= 60 + geotab_client.RATE_LIMIT_JITTER_MAX_SECONDS

    def test_rate_limit_wait_honours_configured_seconds(self, sleeps, monkeypatch):
        monkeypatch.setattr(geotab_client, "RATE_LIMIT_WAIT_SECONDS", 90)
        api = ScriptedAPI([geotab_error("OverLimitException")], result=[])

        _api_call_with_retry(api, "Get", typeName="Device")

        assert sleeps and sleeps[0] >= 90

    def test_db_unavailable_retried_with_exponential_backoff(self, sleeps):
        api = ScriptedAPI(
            [geotab_error("DbUnavailableException"), geotab_error("DbUnavailableException")],
            result=[{"id": 1}],
        )

        out = _api_call_with_retry(api, "Get", typeName="Device")

        assert out == [{"id": 1}]
        assert api.attempts == 3
        assert len(sleeps) == 2
        # full jitter: 0 <= wait <= base * factor**n, con tope
        assert 0 <= sleeps[0] <= 2.0
        assert 0 <= sleeps[1] <= 4.0

    def test_backoff_is_capped(self, sleeps, monkeypatch):
        monkeypatch.setattr(geotab_client, "NET_MAX_RETRIES", 10)
        monkeypatch.setattr(geotab_client.random, "uniform", lambda _lo, hi: hi)
        api = ScriptedAPI([geotab_error("ServerException")] * 6, result=[])

        _api_call_with_retry(api, "Get", typeName="Device")

        assert sleeps == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]

    def test_http_503_retried(self, sleeps):
        api = ScriptedAPI([http_error(503)], result=[{"id": 1}])

        out = _api_call_with_retry(api, "Get", typeName="Device")

        assert out == [{"id": 1}]
        assert api.attempts == 2
        assert len(sleeps) == 1

    def test_http_400_raised_immediately(self, sleeps):
        api = ScriptedAPI([http_error(400)], result=[{"id": 1}])

        with pytest.raises(requests.exceptions.HTTPError):
            _api_call_with_retry(api, "Get", typeName="Device")

        assert api.attempts == 1
        assert sleeps == []
        assert api.authenticate_calls == 0

    def test_auth_error_reauthenticates_once_then_succeeds(self, sleeps):
        api = ScriptedAPI([FakeAuthenticationError("session has expired")], result=[{"id": 7}])

        out = _api_call_with_retry(api, "Get", typeName="Device")

        assert out == [{"id": 7}]
        assert api.authenticate_calls == 1
        assert api.attempts == 2
        assert sleeps == []

    def test_second_auth_error_propagates(self, sleeps):
        api = ScriptedAPI(
            [FakeAuthenticationError("expired"), FakeAuthenticationError("still expired")],
            result=[],
        )

        with pytest.raises(FakeAuthenticationError, match="still expired"):
            _api_call_with_retry(api, "Get", typeName="Device")

        assert api.authenticate_calls == 1
        assert api.attempts == 2

    def test_transient_exhaustion_raises_last_exception(self, sleeps):
        script = [geotab_error("ServerException", f"fail {i}") for i in range(geotab_client.NET_MAX_RETRIES)]
        api = ScriptedAPI(script, result=[])

        with pytest.raises(MyGeotabException) as excinfo:
            _api_call_with_retry(api, "Get", typeName="Device")

        assert excinfo.value.message == f"fail {geotab_client.NET_MAX_RETRIES - 1}"
        assert api.attempts == geotab_client.NET_MAX_RETRIES
        assert len(sleeps) == geotab_client.NET_MAX_RETRIES - 1

    def test_rate_limit_exhaustion_raises_last_exception(self, sleeps):
        script = [geotab_error("OverLimitException", f"quota {i}") for i in range(geotab_client.RATE_LIMIT_MAX_ATTEMPTS)]
        api = ScriptedAPI(script, result=[])

        with pytest.raises(MyGeotabException) as excinfo:
            multi_call_with_retry(api, [("Get", {"typeName": "Device"})])

        assert excinfo.value.message == f"quota {geotab_client.RATE_LIMIT_MAX_ATTEMPTS - 1}"
        assert api.attempts == geotab_client.RATE_LIMIT_MAX_ATTEMPTS
        assert len(sleeps) == geotab_client.RATE_LIMIT_MAX_ATTEMPTS - 1
        assert all(w >= 60 for w in sleeps)

    def test_multi_call_uses_same_policy_and_normalizes_none(self, sleeps):
        api = ScriptedAPI([http_error(502), geotab_error("OverLimitException")], result=[None, [{"id": 2}]])

        out = multi_call_with_retry(api, [("Get", {"typeName": "A"}), ("Get", {"typeName": "B"})])

        assert out == [[], [{"id": 2}]]
        assert api.attempts == 3
        assert len(sleeps) == 2
        assert sleeps[0] <= 2.0
        assert sleeps[1] >= 60

    def test_retry_logs_warning_with_database(self, sleeps, monkeypatch):
        messages: list[str] = []
        monkeypatch.setattr(
            geotab_client._logger, "warning", lambda msg, *args, **_kwargs: messages.append(msg % args)
        )
        api = ScriptedAPI([geotab_error("DbUnavailableException")], result=[])

        _api_call_with_retry(api, "Get", typeName="Device")

        assert len(messages) == 1
        assert "db=test_db" in messages[0]
        assert "transient" in messages[0]
        assert "intento 1/" in messages[0]
        assert "MyGeotabException" in messages[0]

    def test_legacy_net_retry_waits_override(self, sleeps, monkeypatch):
        monkeypatch.setattr(geotab_client, "NET_RETRY_WAITS", (0.5, 1.5))
        api = ScriptedAPI([http_error(500)] * 3, result=[])

        _api_call_with_retry(api, "Get", typeName="Device")

        assert sleeps == [0.5, 1.5, 1.5]


# ------------------------------------------------------------------------------
# get_month_data_bundle: fallback solo ante errores fatales
# ------------------------------------------------------------------------------


_DIAGS = {"odometer": "DiagnosticOdometerId", "engine_hours": "DiagnosticEngineHoursId"}


class BundleAPI(ScriptedAPI):
    """multi_call sigue el script; call individual siempre responde."""

    def __init__(self, script):
        super().__init__(script, result=None)
        self.individual_calls = 0

    def call(self, method, **kwargs):
        self.individual_calls += 1
        if kwargs.get("typeName") == "Trip":
            return [{"distance": 1.0}]
        return [{"dateTime": "2026-06-01T00:00:00Z", "data": 1.0}]


class TestGetMonthDataBundleFallback:
    def test_no_fallback_on_rate_limit_exhaustion(self, sleeps):
        api = BundleAPI([geotab_error("OverLimitException")] * geotab_client.RATE_LIMIT_MAX_ATTEMPTS)

        with pytest.raises(MyGeotabException):
            get_month_data_bundle(api, "dev1", "2026-06-01T05:00:00.000Z", "2026-07-01T05:00:00.000Z", status_diagnostics=_DIAGS)

        assert api.attempts == geotab_client.RATE_LIMIT_MAX_ATTEMPTS
        assert api.individual_calls == 0

    def test_no_fallback_on_transient_exhaustion(self, sleeps):
        api = BundleAPI([http_error(503)] * geotab_client.NET_MAX_RETRIES)

        with pytest.raises(requests.exceptions.HTTPError):
            get_month_data_bundle(api, "dev1", "2026-06-01T05:00:00.000Z", "2026-07-01T05:00:00.000Z", status_diagnostics=_DIAGS)

        assert api.individual_calls == 0

    def test_fallback_on_fatal_error(self, sleeps):
        api = BundleAPI([geotab_error("ArgumentException", "Invalid search")])

        bundle = get_month_data_bundle(
            api, "dev1", "2026-06-01T05:00:00.000Z", "2026-07-01T05:00:00.000Z", status_diagnostics=_DIAGS
        )

        assert api.attempts == 1
        assert api.individual_calls == 3  # 2 StatusData + 1 Trip
        assert set(bundle) == {"odometer", "engine_hours", "trips"}
        assert bundle["trips"] == [{"distance": 1.0}]
        assert sleeps == []
