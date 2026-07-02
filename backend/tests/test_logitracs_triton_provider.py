from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

from app.clients.logitracs_triton_client import LogitracsTritonAuthError
from app.clients.logitracs_triton_client import LogitracsTritonClient, LogitracsTritonConfig
from app.services.performance_providers import LogitracsTritonMonthlyPerformanceProvider
from app.services.performance_types import BindingSnapshot, PerformanceTarget
from app.schemas.vehicle import MonthlyPerformanceRecord


def _make_target(
    provider_key: str = "logitracs_triton",
    customer_database_id: int = 1,
    plate: str = "AAA111",
    **overrides,
) -> PerformanceTarget:
    defaults = dict(
        provider_key=provider_key,
        customer_id=1,
        customer_database_id=customer_database_id,
        client_name="Cliente Test",
        database_name="db_test",
        plate=plate,
        technical_number="TEC001",
        engine_name="Motor Test",
        username="user",
        password="pass",
        provider_config={
            "codigo_empresa": "GRUPOK",
            "triton_login_url": "https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login",
            "logivim_base_url": "https://triton.logitracs.com/LogiVIMwebTriton/public",
        },
    )
    defaults.update(overrides)
    return PerformanceTarget(**defaults)


def _jwt_with_email(email: str) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"EmailUsuario": email}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.signature"


def _fake_response(*, status_code: int = 200, text: str = "", url: str = "https://example.test", json_body=None):
    response = MagicMock(status_code=status_code, text=text, url=url)
    response.raise_for_status.return_value = None
    response.json.return_value = json_body or {}
    return response


class TestLogitracsClient:
    def test_login_accepts_full_login_endpoint_in_config(self):
        client = LogitracsTritonClient(
            LogitracsTritonConfig(
                username="user",
                password="pass",
                password_web="webpass",
                codigo_empresa="GRUPOK",
                triton_base_url="https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login",
                logivim_base_url="https://triton.logitracs.com/LogiVIMwebTriton/public",
            )
        )

        login_page = _fake_response(url="https://triton.logitracs.com/Logitracs.Triton/Login")
        api_login = _fake_response(
            url="https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login",
            json_body={"token": _jwt_with_email("test@example.com")},
        )
        sso_page = _fake_response(
            url="https://triton.logitracs.com/LogiVIMwebTriton/public/login",
            text='<form><input name="_token" value="csrf123"><input type="password"></form>',
        )
        ver_info = _fake_response(
            url="https://triton.logitracs.com/LogiVIMwebTriton/public/ver-informacion-especifica",
        )
        logged_in = _fake_response(
            url="https://triton.logitracs.com/LogiVIMwebTriton/public/home",
            text="<html>ok</html>",
        )

        with patch.object(client.session, "get", side_effect=[login_page, sso_page, ver_info]) as mock_get, patch.object(
            client.session,
            "post",
            side_effect=[api_login, logged_in],
        ) as mock_post:
            client.login()

        assert mock_get.call_args_list[0].args[0] == "https://triton.logitracs.com/Logitracs.Triton/Login"
        assert mock_post.call_args_list[0].args[0] == "https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login"


class TestLogitracsProvider:
    def test_auth_error_short_circuits_batch_with_error_rows(self):
        provider = LogitracsTritonMonthlyPerformanceProvider()
        target_a = _make_target(customer_database_id=10, plate="AAA111")
        target_b = _make_target(customer_database_id=10, plate="BBB222")

        with patch(
            "app.services.performance_providers.LogitracsTritonClient.get_fleet_operational_report",
            side_effect=LogitracsTritonAuthError(
                "Credenciales LogiTracs Triton invalidas. Revisa usuario, contraseña y codigoEmpresa."
            ),
        ) as mock_get_report:
            result = provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target_a, target_b],
                previous_records={},
                bindings={},
            )

        assert mock_get_report.call_count == 1
        assert len(result.records) == 2
        assert len(result.binding_updates) == 2
        for record in result.records:
            assert record.calculation_status == "error"
            assert any("Credenciales LogiTracs Triton invalidas" in warning for warning in record.warnings)
        for update in result.binding_updates:
            assert update.binding_status == "error"
            assert "Credenciales LogiTracs Triton invalidas" in (update.last_error or "")

    def test_manual_binding_is_used_as_provider_vehicle_id(self):
        provider = LogitracsTritonMonthlyPerformanceProvider()
        target = _make_target(customer_database_id=10, plate="aaa111")
        bindings = {
            ("logitracs_triton", 10, "aaa111"): BindingSnapshot("MANUAL-42", "resolved", is_manual=True)
        }

        current_rows = [
            {
                "Placa": "AAA111",
                "Odometro final": "1500",
                "Kilometraje": "200",
                "Tiempo Encendido(h)": "10",
                "Combustible": "100",
            }
        ]
        previous_rows = [{"Placa": "AAA111", "Odometro final": "1300"}]

        with patch(
            "app.services.performance_providers.LogitracsTritonClient.get_fleet_operational_report",
            side_effect=[current_rows, previous_rows],
        ):
            result = provider.calculate_database_rows(
                month="2026-01",
                year=2026,
                month_number=1,
                previous_month="2025-12",
                targets=[target],
                previous_records={},
                bindings=bindings,
            )

        assert len(result.records) == 1
        assert result.records[0].provider_vehicle_id == "MANUAL-42"
        assert result.records[0].calculation_status == "calculated"
        assert len(result.binding_updates) == 1
        assert result.binding_updates[0].provider_vehicle_id == "MANUAL-42"

    def test_open_month_estimates_odometer_when_report_returns_zero(self):
        provider = LogitracsTritonMonthlyPerformanceProvider()
        target = _make_target(customer_database_id=10, plate="NNZ434")

        previous_record = MonthlyPerformanceRecord(
            customer_id=target.customer_id,
            customer_database_id=target.customer_database_id,
            client_name=target.client_name,
            database_name=target.database_name,
            source_provider=target.provider_key,
            plate=target.plate,
            provider_vehicle_id=target.plate,
            technical_number=target.technical_number,
            engine_name=target.engine_name,
            period_month="2026-03",
            odo_end=93520.0,
            calculation_status="calculated",
            warnings=[],
        )

        current_rows = [
            {
                "Placa": "NNZ434",
                "Odometro final": "0",
                "Kilometraje": "664",
                "Tiempo Encendido(h)": "193",
                "Combustible": "93.52",
            }
        ]
        previous_rows = [{"Placa": "NNZ434", "Odometro final": "93520"}]

        with patch(
            "app.services.performance_providers.LogitracsTritonClient.get_fleet_operational_report",
            side_effect=[current_rows, previous_rows],
        ):
            result = provider.calculate_database_rows(
                month="2026-04",
                year=2026,
                month_number=4,
                previous_month="2026-03",
                targets=[target],
                previous_records={(10, "NNZ434"): previous_record},
                bindings={},
            )

        assert len(result.records) == 1
        record = result.records[0]
        assert record.odo_start == 93520.0
        assert record.odo_end == 94184.0
        assert record.kms_ecm == 664.0
        assert record.calculation_status == "calculated"
        assert any("Odometro final reportado en 0 por LogiTracs" in warning for warning in record.warnings)
