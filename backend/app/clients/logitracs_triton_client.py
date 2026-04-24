from __future__ import annotations

import base64
import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


def _normalize_triton_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if normalized.endswith("/api/Usuarios/Login"):
        return normalized[: -len("/api/Usuarios/Login")]
    if normalized.endswith("/Login"):
        return normalized[: -len("/Login")]
    return normalized


def _origin_from_url(value: str) -> str | None:
    parsed = urlsplit(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _build_field_index(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {_normalize_key(str(k)): v for k, v in row.items()}


def _first_value(row: dict[str, Any] | None, *candidates: str) -> Any:
    index = _build_field_index(row)
    for candidate in candidates:
        key = _normalize_key(candidate)
        if key in index:
            return index[key]
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_plate(row: dict[str, Any] | None) -> str | None:
    value = _first_value(row, "Placa", "placa", "plate")
    if value is None:
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def extract_odometer_end(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "Odometro final", "odometro final"))


def extract_kilometraje_vo(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "Kilometraje Vo", "kilometraje vo"))


def extract_kms_period(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "Kilometraje", "kilometraje"))


def extract_fuel_liters(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "Combustible", "combustible"))


def extract_engine_hours(row: dict[str, Any] | None) -> float | None:
    return _to_float(_first_value(row, "Tiempo Encendido(h)", "tiempo encendido(h)"))


@dataclass(frozen=True)
class LogitracsTritonConfig:
    username: str
    password: str
    password_web: str
    codigo_empresa: str
    triton_base_url: str = "https://triton.logitracs.com/Logitracs.Triton"
    logivim_base_url: str = "https://triton.logitracs.com/LogiVIMwebTriton/public"


class LogitracsTritonAuthError(RuntimeError):
    """Credenciales LogiTracs Triton invalidas o rechazadas por el servidor."""


class LogitracsTritonClient:
    def __init__(self, config: LogitracsTritonConfig):
        self.config = config
        self.triton_base_url = _normalize_triton_base_url(config.triton_base_url) or "https://triton.logitracs.com/Logitracs.Triton"
        self.logivim_base_url = str(config.logivim_base_url or "").strip().rstrip("/") or "https://triton.logitracs.com/LogiVIMwebTriton/public"
        self.triton_origin = _origin_from_url(self.triton_base_url) or "https://triton.logitracs.com"
        self.logivim_origin = _origin_from_url(self.logivim_base_url) or self.triton_origin
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Accept-Language": "es-ES,es;q=0.9",
            }
        )
        self._logged_in = False
        self.jwt: str | None = None
        self.email_usuario: str | None = None

    def _get_xsrf(self) -> str | None:
        token = self.session.cookies.get("XSRF-TOKEN")
        if token:
            return unquote(token)
        return None

    def _inject_xsrf(self) -> None:
        token = self._get_xsrf()
        if token:
            self.session.headers["X-XSRF-TOKEN"] = token

    @staticmethod
    def _decode_jwt_payload(jwt: str) -> dict[str, Any]:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))

    @staticmethod
    def _looks_like_login(html: str) -> bool:
        return 'name="password"' in html or 'type="password"' in html

    @staticmethod
    def _csrf_from_html(html: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        inp = soup.find("input", {"name": "_token"})
        if inp and inp.get("value"):
            return inp["value"]
        meta = soup.find("meta", {"name": "csrf-token"})
        if meta and meta.get("content"):
            return meta["content"]
        return None

    def login(self) -> None:
        triton_base = self.triton_base_url
        logivim_base = self.logivim_base_url

        # Paso 1: GET Login page for cookies
        r1 = self.session.get(f"{triton_base}/Login")
        r1.raise_for_status()
        self._inject_xsrf()

        # Paso 2: POST API Login
        self.session.headers.update({
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": self.triton_origin,
            "Referer": f"{triton_base}/Login",
        })
        r2 = self.session.post(
            f"{triton_base}/api/Usuarios/Login",
            json={
                "username": self.config.username,
                "password": self.config.password,
                "codigoEmpresa": self.config.codigo_empresa,
            },
            timeout=30,
        )
        if r2.status_code in (401, 403):
            raise LogitracsTritonAuthError(
                "Credenciales LogiTracs Triton invalidas. Revisa usuario, contrasena y codigoEmpresa."
            )
        r2.raise_for_status()
        self.jwt = r2.json()["token"]
        payload = self._decode_jwt_payload(self.jwt)
        self.email_usuario = payload["EmailUsuario"]

        # Paso 3: SSO to LogiVIM
        self.session.headers.pop("Content-Type", None)
        self.session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{triton_base}/LogiVim",
            "Sec-Fetch-Dest": "iframe",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
        })
        sso_url = f"{logivim_base}/loginLogitracs/usuario/{quote(self.email_usuario, safe='@')}"
        resp_sso = self.session.get(sso_url, allow_redirects=True)
        resp_sso.raise_for_status()
        self._inject_xsrf()

        self.session.headers["Referer"] = resp_sso.url
        ver_info_url = f"{logivim_base}/ver-informacion-especifica"
        r_vi = self.session.get(ver_info_url, allow_redirects=True)
        r_vi.raise_for_status()
        self._inject_xsrf()

        # Paso 4: Laravel login
        if self._looks_like_login(resp_sso.text):
            login_html = resp_sso.text
            login_page_url = resp_sso.url
        else:
            r_login = self.session.get(f"{logivim_base}/login", allow_redirects=True)
            r_login.raise_for_status()
            login_html = r_login.text
            login_page_url = r_login.url
            self._inject_xsrf()

        token_login = self._csrf_from_html(login_html)
        if not token_login:
            raise RuntimeError("LogiVIM login sin _token (layout inesperado).")

        password_web = self.config.password_web or self.config.password
        r_do_login = self.session.post(
            f"{logivim_base}/login",
            data={
                "_token": token_login,
                "email": self.email_usuario,
                "password": password_web,
            },
            headers={
                "Origin": self.logivim_origin,
                "Referer": login_page_url,
            },
            allow_redirects=True,
        )
        self._inject_xsrf()

        if self._looks_like_login(r_do_login.text):
            raise LogitracsTritonAuthError(
                "Login web LogiVIM fallo. Verifica password_web."
            )

        self._logged_in = True

    def get_fleet_operational_report(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        if not self._logged_in:
            self.login()

        logivim_base = self.logivim_base_url
        informe_url = f"{logivim_base}/informeOperacionalFlota"

        # GET to retrieve _token
        r_informe = self.session.get(informe_url, allow_redirects=True)
        r_informe.raise_for_status()
        self._inject_xsrf()

        if self._looks_like_login(r_informe.text):
            raise LogitracsTritonAuthError("Sesion LogiVIM expirada.")

        token_informe = self._csrf_from_html(r_informe.text)
        if not token_informe:
            raise RuntimeError("No se encontro _token en la pagina del informe.")

        payload = {
            "_token": token_informe,
            "fechaInicioFiltro": start_date,
            "fechaFinFiltro": end_date,
            "idCobertura": "0",
            "lgOperacion": "0",
            "lgFrente": "0",
            "id": "",
        }
        common_headers = {
            "Origin": self.logivim_origin,
            "Referer": informe_url,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        resp = self.session.post(
            informe_url,
            data=payload,
            headers=common_headers,
            timeout=60,
        )

        needs_multipart = (
            resp.status_code in (400, 415, 419)
            or "TokenMismatch" in resp.text
            or self._looks_like_login(resp.text)
        )

        if needs_multipart:
            try:
                from requests_toolbelt.multipart.encoder import MultipartEncoder
            except ImportError:
                raise RuntimeError(
                    f"Informe fallo HTTP {resp.status_code} y no hay requests_toolbelt para fallback."
                )
            m = MultipartEncoder(fields=payload)
            resp = self.session.post(
                informe_url,
                data=m,
                headers={**common_headers, "Content-Type": m.content_type},
                timeout=60,
            )

        if resp.status_code != 200 or self._looks_like_login(resp.text):
            raise RuntimeError("POST del informe operacional fallo.")

        # Parse
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", {"id": "order-listingFleet"}) or soup.find("table")
        if table is None:
            raise RuntimeError("Tabla del informe no encontrada.")

        normalize = lambda s: re.sub(r"\s+", " ", s).strip()
        headers = [normalize(th.get_text(" ", strip=True)) for th in table.find("thead").find_all("th")]

        rows: list[dict[str, Any]] = []
        for tr in table.find("tbody").find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            cells += [None] * (len(headers) - len(cells))
            rows.append(dict(zip(headers, cells[:len(headers)])))

        return rows
