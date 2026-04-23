from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.clients.frotcom_client import (
    FrotcomAuthError,
    FrotcomConfig,
    _AUTH_FAILURE_CACHE,
    _TOKEN_CACHE,
    clear_auth_failure,
    get_access_token,
)
from app.clients.artimo_client import ArtimoAuthError, ArtimoClient, ArtimoConfig
from app.services.performance_providers import (
    ArtimoMonthlyPerformanceProvider,
    FrotcomMonthlyPerformanceProvider,
)
from app.services.performance_types import BindingSnapshot, PerformanceTarget


@pytest.fixture(autouse=True)
def _clear_frotcom_caches():
    _TOKEN_CACHE.clear()
    _AUTH_FAILURE_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()
    _AUTH_FAILURE_CACHE.clear()


def _make_target(
    provider_key: str = "frotcom",
    customer_database_id: int = 1,
    plate: str = "TEST001",
    **overrides,
) -> PerformanceTarget:
    defaults = dict(
        provider_key=provider_key,
        customer_id=1,
        customer_database_id=customer_database_id,
        client_name="Test",
        database_name="test_db",
        plate=plate,
        technical_number="TEC001",
        engine_name="Test Motor",
        username="user",
        password="pass",
        provider_config={
            "customer_id": "cust",
            "group_name": "grp",
            "api_base_url": "https://api.artimo.test",
            "auth_base_url": "https://auth.artimo.test",
        },
    )
    defaults.update(overrides)
    return PerformanceTarget(**defaults)


class TestFrotcomAuthFailureCache:
    def test_401_raises_frotcom_auth_error(self):
        config = FrotcomConfig(username="bad", password="bad")
        fake_response = MagicMock(status_code=401, text="unauthorized")
        with patch(
            "app.clients.frotcom_client.requests.post",
            return_value=fake_response,
        ):
            with pytest.raises(FrotcomAuthError):
                get_access_token(config)

    def test_auth_failure_is_cached_and_does_not_retry_api(self):
        config = FrotcomConfig(username="bad", password="bad")
        fake_response = MagicMock(status_code=401, text="unauthorized")
        with patch(
            "app.clients.frotcom_client.requests.post",
            return_value=fake_response,
        ) as mock_post:
            with pytest.raises(FrotcomAuthError):
                get_access_token(config)
            for _ in range(9):
                with pytest.raises(FrotcomAuthError):
                    get_access_token(config)
            assert mock_post.call_count == 1, (
                "Auth failure must be cached; only one network call expected"
            )

    def test_clear_auth_failure_allows_retry(self):
        config = FrotcomConfig(username="bad", password="bad")
        fake_fail = MagicMock(status_code=401, text="unauthorized")
        fake_ok = MagicMock(status_code=201)
        fake_ok.json.return_value = {"token": "abc"}

        with patch(
            "app.clients.frotcom_client.requests.post",
            side_effect=[fake_fail, fake_ok],
        ) as mock_post:
            with pytest.raises(FrotcomAuthError):
                get_access_token(config)
            clear_auth_failure(config)
            token = get_access_token(config)
            assert token == "abc"
            assert mock_post.call_count == 2


class TestFrotcomAdapterAuthHandling:
    def test_auth_error_surfaces_clean_message_to_all_targets(self):
        provider = FrotcomMonthlyPerformanceProvider()
        target_a = _make_target(customer_database_id=10, plate="AAA111")
        target_b = _make_target(customer_database_id=10, plate="BBB222")
        bindings = {}

        with patch(
            "app.services.performance_providers.list_frotcom_vehicles",
            side_effect=FrotcomAuthError(
                "Credenciales Frotcom invalidas. Revisa usuario y contrasena de la database."
            ),
        ) as mock_list:
            result = provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target_a, target_b],
                previous_records={},
                bindings=bindings,
            )

        assert mock_list.call_count == 1, (
            "Auth error must short-circuit further list_frotcom_vehicles calls in the same batch"
        )
        assert len(result.records) == 2
        for record in result.records:
            assert record.calculation_status == "error"
            assert any("Credenciales Frotcom invalidas" in w for w in record.warnings)
        for upd in result.binding_updates:
            assert upd.binding_status == "error"
            assert "Credenciales Frotcom invalidas" in (upd.last_error or "")

    def test_auth_error_on_manual_binding_short_circuits_same_batch(self):
        provider = FrotcomMonthlyPerformanceProvider()
        target_a = _make_target(customer_database_id=10, plate="AAA111")
        target_b = _make_target(customer_database_id=10, plate="BBB222")
        bindings = {
            ("frotcom", 10, "AAA111"): BindingSnapshot("VID_A", "resolved", is_manual=True),
            ("frotcom", 10, "BBB222"): BindingSnapshot("VID_B", "resolved", is_manual=True),
        }

        calls = []

        def fake_calculate(*, target, config, **_kwargs):
            calls.append(target.plate)
            raise FrotcomAuthError(
                "Credenciales Frotcom invalidas. Revisa usuario y contrasena de la database."
            )

        with patch(
            "app.services.performance_providers._calculate_frotcom_vehicle_record",
            side_effect=fake_calculate,
        ):
            result = provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target_a, target_b],
                previous_records={},
                bindings=bindings,
            )

        assert calls == ["AAA111"], (
            "After first auth error, subsequent manual-binding targets should short-circuit"
        )
        assert len(result.records) == 2
        for record in result.records:
            assert record.calculation_status == "error"
            assert any("Credenciales Frotcom invalidas" in w for w in record.warnings)


class TestArtimoAuthHandling:
    def test_login_401_raises_artimo_auth_error(self):
        config = ArtimoConfig(
            username="bad",
            password="bad",
            customer_id="cust",
            group_name="grp",
        )
        client = ArtimoClient(config)
        fake_response = MagicMock(status_code=401)
        with patch.object(client.session, "post", return_value=fake_response):
            with pytest.raises(ArtimoAuthError):
                client.login()

    def test_adapter_surfaces_clean_message_on_auth_error(self):
        provider = ArtimoMonthlyPerformanceProvider()
        target_a = _make_target(provider_key="artimo", customer_database_id=10, plate="AAA111")
        target_b = _make_target(provider_key="artimo", customer_database_id=10, plate="BBB222")

        mock_artimo = MagicMock()
        mock_artimo.get_month_range.return_value = ("2026-01-01", "2026-01-31")
        mock_artimo.get_report.side_effect = ArtimoAuthError(
            "Credenciales Artimo invalidas. Revisa usuario y contrasena de la database."
        )

        with patch(
            "app.services.performance_providers.ArtimoClient", return_value=mock_artimo
        ):
            result = provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target_a, target_b],
                previous_records={},
                bindings={},
            )

        assert len(result.records) == 2
        for record in result.records:
            assert record.calculation_status == "error"
            assert any("Credenciales Artimo invalidas" in w for w in record.warnings)
        for upd in result.binding_updates:
            assert upd.binding_status == "error"
            assert "Credenciales Artimo invalidas" in (upd.last_error or "")
