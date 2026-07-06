from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import QuickServeConfig

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

FWUID_FALLBACK = (
    "YkVKdlZEd2t6eFplVFJNMGN2eVd5UTJEa1N5enhOU3R5"
    "QWl2VzNveFZTbGcxMy4tMjE0NzQ4MzY0OC45OTYxNDcy"
)
DEFAULT_QUICKSERVE_SESSION_TTL_SECONDS = 6 * 60 * 60
DEFAULT_QUICKSERVE_SESSION_LOCK_TTL_SECONDS = 60
DEFAULT_QUICKSERVE_SESSION_WAIT_SECONDS = 8.0
DEFAULT_QUICKSERVE_SESSION_WAIT_INTERVAL_SECONDS = 0.4
_REDIS_CLIENT: Redis | None = None


def _redis_client() -> Redis | None:
    global _REDIS_CLIENT

    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT

    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return None

    try:
        _REDIS_CLIENT = Redis.from_url(redis_url, decode_responses=True)
        _REDIS_CLIENT.ping()
    except RedisError:
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


def _session_cache_key(cfg: QuickServeConfig) -> str:
    return f"quickserve:session:{cfg.username}:{cfg.app_id}"


def _session_lock_key(cfg: QuickServeConfig) -> str:
    return f"{_session_cache_key(cfg)}:lock"


def _session_ttl_seconds() -> int:
    raw_value = os.getenv(
        "QUICKSERVE_SESSION_TTL_SECONDS", str(DEFAULT_QUICKSERVE_SESSION_TTL_SECONDS)
    )
    try:
        return max(int(raw_value), 60)
    except ValueError:
        return DEFAULT_QUICKSERVE_SESSION_TTL_SECONDS


def _session_lock_ttl_seconds() -> int:
    raw_value = os.getenv(
        "QUICKSERVE_SESSION_LOCK_TTL_SECONDS", str(DEFAULT_QUICKSERVE_SESSION_LOCK_TTL_SECONDS)
    )
    try:
        return max(int(raw_value), 15)
    except ValueError:
        return DEFAULT_QUICKSERVE_SESSION_LOCK_TTL_SECONDS


def _session_wait_seconds() -> float:
    raw_value = os.getenv(
        "QUICKSERVE_SESSION_WAIT_SECONDS", str(DEFAULT_QUICKSERVE_SESSION_WAIT_SECONDS)
    )
    try:
        return max(float(raw_value), 1.0)
    except ValueError:
        return DEFAULT_QUICKSERVE_SESSION_WAIT_SECONDS


def _session_wait_interval_seconds() -> float:
    raw_value = os.getenv(
        "QUICKSERVE_SESSION_WAIT_INTERVAL_SECONDS",
        str(DEFAULT_QUICKSERVE_SESSION_WAIT_INTERVAL_SECONDS),
    )
    try:
        return max(float(raw_value), 0.1)
    except ValueError:
        return DEFAULT_QUICKSERVE_SESSION_WAIT_INTERVAL_SECONDS


def _reuse_session_enabled() -> bool:
    raw_value = os.getenv("QUICKSERVE_REUSE_SESSION", "false").strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def _save_cached_session(session: requests.Session, cfg: QuickServeConfig) -> None:
    redis_client = _redis_client()
    if redis_client is None:
        return

    try:
        payload = {
            "cookies": requests.utils.dict_from_cookiejar(session.cookies),
            "saved_at": int(time.time()),
        }
        redis_client.setex(_session_cache_key(cfg), _session_ttl_seconds(), json.dumps(payload))
    except RedisError:
        return


def _load_cached_session(session: requests.Session, cfg: QuickServeConfig) -> bool:
    redis_client = _redis_client()
    if redis_client is None:
        return False

    try:
        payload = redis_client.get(_session_cache_key(cfg))
    except RedisError:
        return False

    if not payload:
        return False

    try:
        parsed = json.loads(payload)
        cookies = parsed.get("cookies") or {}
        session.cookies.update(requests.utils.cookiejar_from_dict(cookies))
    except Exception:
        return False
    return True


def _clear_cached_session(cfg: QuickServeConfig) -> None:
    redis_client = _redis_client()
    if redis_client is None:
        return
    try:
        redis_client.delete(_session_cache_key(cfg))
    except RedisError:
        return


def _acquire_login_lock(cfg: QuickServeConfig) -> bool:
    redis_client = _redis_client()
    if redis_client is None:
        return True

    try:
        acquired = redis_client.set(
            _session_lock_key(cfg),
            str(time.time()),
            nx=True,
            ex=_session_lock_ttl_seconds(),
        )
    except RedisError:
        return True
    return bool(acquired)


def _release_login_lock(cfg: QuickServeConfig) -> None:
    redis_client = _redis_client()
    if redis_client is None:
        return
    try:
        redis_client.delete(_session_lock_key(cfg))
    except RedisError:
        return


def _wait_for_cached_session(session: requests.Session, cfg: QuickServeConfig) -> bool:
    deadline = time.time() + _session_wait_seconds()
    interval = _session_wait_interval_seconds()

    while time.time() < deadline:
        if _load_cached_session(session, cfg):
            return True
        time.sleep(interval)
    return False


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _get_fwuid(session: requests.Session, cfg: QuickServeConfig) -> str:
    resp = session.get(
        f"{cfg.sf_base}/clw/s/login/",
        headers=HEADERS,
        params={"ec": "302", "inst": "Uz", "startURL": f"/clw/IAM_Authorize?appid={cfg.app_id}"},
        allow_redirects=True,
        timeout=45,
    )
    match = re.search(r'"fwuid"\s*:\s*"([^"]+)"', resp.text)
    return match.group(1) if match else FWUID_FALLBACK


def _login_salesforce(session: requests.Session, fwuid: str, cfg: QuickServeConfig) -> str:
    aura_message = {
        "actions": [
            {
                "id": "104;a",
                "descriptor": "aura://ApexActionController/ACTION$execute",
                "callingDescriptor": "UNKNOWN",
                "params": {
                    "namespace": "",
                    "classname": "IAM_VisualforceToLightning",
                    "method": "getDoLogin",
                    "params": {
                        "fedID": cfg.username,
                        "password": cfg.password,
                        "startURL": f"/clw/IAM_Authorize?appid={cfg.app_id}",
                        "resourceURL": None,
                        "appID": None,
                        "lang": "en_US",
                    },
                    "cacheable": False,
                    "isContinuation": False,
                },
            }
        ]
    }
    aura_context = {
        "mode": "PROD",
        "fwuid": fwuid,
        "app": "siteforce:loginApp2",
        "loaded": {"APPLICATION@markup://siteforce:loginApp2": "1452_SL_71DWyCkD4oCnm3sgzzg"},
        "dn": [],
        "globals": {},
        "uad": True,
    }
    resp = session.post(
        f"{cfg.sf_base}/clw/s/sfsites/aura?r=8&aura.ApexAction.execute=1",
        data={
            "message": json.dumps(aura_message),
            "aura.context": json.dumps(aura_context),
            "aura.pageURI": (
                f"/clw/s/login/?ec=302&inst=Uz&startURL=%2Fclw%2FIAM_Authorize%3Fappid%3D{cfg.app_id}"
            ),
            "aura.token": "null",
        },
        headers={
            **HEADERS,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": cfg.sf_base,
            "Referer": f"{cfg.sf_base}/clw/s/login/",
            "x-sfdc-lds-endpoints": "ApexActionController.execute:IAM_VisualforceToLightning.getDoLogin",
        },
        timeout=45,
    )
    result = resp.json()
    action = result["actions"][0]
    if action.get("state") != "SUCCESS":
        raise RuntimeError(f"Login QuickServe fallido: {action.get('error', '')}")
    return action["returnValue"]["returnValue"]


def _follow_frontdoor(session: requests.Session, frontdoor_url: str, cfg: QuickServeConfig) -> str:
    resp = session.get(
        frontdoor_url,
        headers={**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"},
        allow_redirects=True,
        timeout=45,
    )
    match = re.search(
        r"window\.location(?:\.replace|\.href)?\s*(?:=|\()\s*['\"]([^'\"]+)['\"]",
        resp.text,
    )
    if not match:
        raise RuntimeError("No se encontro redirect IAM_Authorize en frontdoor.jsp")
    path = match.group(1)
    return path if path.startswith("http") else cfg.sf_base + path


def _complete_saml_sso(session: requests.Session, iam_url: str, cfg: QuickServeConfig) -> bool:
    nav_headers = {**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"}
    resp = session.get(iam_url, headers=nav_headers, allow_redirects=True, timeout=45)
    soup = _make_soup(resp.text)
    if not soup.find("input", {"id": "com.salesforce.visualforce.ViewState"}):
        raise RuntimeError("ViewState no encontrado en IAM_Authorize")

    viewstate_fields: dict[str, str] = {}
    for field_id in [
        "com.salesforce.visualforce.ViewState",
        "com.salesforce.visualforce.ViewStateVersion",
        "com.salesforce.visualforce.ViewStateMAC",
        "com.salesforce.visualforce.ViewStateCSRF",
    ]:
        inp = soup.find("input", {"id": field_id})
        if inp:
            viewstate_fields[field_id] = inp.get("value", "")

    jsfcljs_match = re.search(
        r"jsfcljs\(document\.forms\['([^']+)'\]\s*,\s*'([^']+)'",
        resp.text,
    )
    form_name = "j_id0:j_id2"
    dynamic_params: dict[str, str] = {}
    if jsfcljs_match:
        form_name = jsfcljs_match.group(1)
        pvp_parts = jsfcljs_match.group(2).split(",")
        for i in range(0, len(pvp_parts) - 1, 2):
            dynamic_params[pvp_parts[i]] = pvp_parts[i + 1]

    post_data = {form_name: form_name}
    post_data.update(dynamic_params)
    post_data.update(viewstate_fields)

    resp = session.post(
        f"{cfg.sf_base}/clw/IAM_Authorize",
        data=post_data,
        headers={
            **nav_headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": cfg.sf_base,
            "Referer": iam_url,
        },
        allow_redirects=True,
        timeout=45,
    )

    for _ in range(10):
        if "quickserve.cummins.com" in resp.url:
            return True

        soup = _make_soup(resp.text)
        saml_input = soup.find("input", {"name": "SAMLResponse"})
        if saml_input:
            form = saml_input.find_parent("form")
            if form:
                action = form.get("action", "")
                data = {i["name"]: i.get("value", "") for i in form.find_all("input", {"name": True})}
                resp = session.post(
                    action,
                    data=data,
                    headers={
                        **nav_headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": resp.url,
                    },
                    allow_redirects=True,
                    timeout=45,
                )
                continue

        js_match = re.search(
            r"window\.location(?:\.replace|\.href)?\s*(?:=|\()\s*['\"]([^'\"]+)['\"]",
            resp.text,
        )
        if js_match:
            next_url = js_match.group(1)
            if not next_url.startswith("http"):
                next_url = urljoin(resp.url, next_url)
            if next_url != resp.url:
                resp = session.get(next_url, headers=nav_headers, allow_redirects=True, timeout=45)
                continue
        break

    return "quickserve.cummins.com" in resp.url


def _authenticate_quickserve(session: requests.Session, cfg: QuickServeConfig) -> bool:
    fwuid = _get_fwuid(session, cfg)
    frontdoor_url = _login_salesforce(session, fwuid, cfg)
    iam_url = _follow_frontdoor(session, frontdoor_url, cfg)
    return _complete_saml_sso(session, iam_url, cfg)


def _build_session(cfg: QuickServeConfig) -> requests.Session:
    session = requests.Session()
    session.cookies.set("GDPR_USER_CONSENT", cfg.username)
    return session


def _set_esn(session: requests.Session, cfg: QuickServeConfig, esn: str) -> bool:
    resp = session.get(
        f"{cfg.base_url}/qs3/portal/includes/ajax/set_esn.json",
        params={"esn": esn, "nocache": int(time.time() * 1000)},
        headers={
            **HEADERS,
            "Accept": "application/json, text/javascript, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{cfg.base_url}/qs3/portal/service/index.html",
        },
        timeout=45,
    )
    try:
        resp.json()
        return True
    except Exception:
        return False


_DATAPLATE_HEADER_HINTS = (
    "model name",
    "modelo",
    "shop order",
    "pedido",
    "build plant",
    "planta",
    "build date",
    "fecha",
    "warranty",
    "garantía",
    "garantia",
    "ecm",
    "fuel pump",
    "bomba de combustible",
    "engine configuration",
    "configuración de motor",
    "configuracion de motor",
    "cpl",
)

_DATAPLATE_KNOWN_HEADER_LABELS = {
    "marketing model name": "Marketing Model Name",
    "nombre de modelo de marketing": "Marketing Model Name",
    "nombre del modelo de marketing": "Marketing Model Name",
    "service model name": "Service Model Name",
    "nombre de modelo de servicio": "Service Model Name",
    "nombre del modelo de servicio": "Service Model Name",
    "epa model name": "EPA Model Name",
    "nombre de modelo epa": "EPA Model Name",
    "nombre del modelo epa": "EPA Model Name",
    "shop order": "Pedido a tienda",
    "pedido a tienda": "Pedido a tienda",
    "build plant": "Planta de construcción",
    "planta de construcción": "Planta de construcción",
    "planta de construccion": "Planta de construcción",
    "build date": "Fecha de construcción",
    "fecha de construcción": "Fecha de construcción",
    "fecha de construccion": "Fecha de construcción",
    "warranty start date": "Fecha de inicio de la garantía",
    "fecha de inicio de la garantía": "Fecha de inicio de la garantía",
    "fecha de inicio de la garantia": "Fecha de inicio de la garantía",
    "ecm code": "Código de ECM",
    "código de ecm": "Código de ECM",
    "codigo de ecm": "Código de ECM",
    "fuel pump part #": "N.º de pieza de bomba de combustible",
    "n.º de pieza de bomba de combustible": "N.º de pieza de bomba de combustible",
    "n.o de pieza de bomba de combustible": "N.º de pieza de bomba de combustible",
    "fuel pump calibration": "Calibración de bomba de combustible",
    "calibración de bomba de combustible": "Calibración de bomba de combustible",
    "calibracion de bomba de combustible": "Calibración de bomba de combustible",
    "marketing engine configuration #": "Marketing Engine Configuration #",
    "technical engine configuration #": "Technical Engine Configuration #",
    "cpl #": "N.º CPL",
    "n.º cpl": "N.º CPL",
    "n.o cpl": "N.º CPL",
}


def _looks_like_dataplate_header_row(values: list[str]) -> bool:
    meaningful_values = [value.strip().lower() for value in values if value.strip()]
    if len(meaningful_values) < 2:
        return False
    return any(
        any(hint in value for hint in _DATAPLATE_HEADER_HINTS)
        for value in meaningful_values
    )


def _normalize_dataplate_label(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    normalized = normalized.replace("n.o", "no.").replace("n.º", "no.")
    return normalized


def _canonical_dataplate_header(value: str) -> str | None:
    normalized = _normalize_dataplate_label(value)
    if normalized in _DATAPLATE_KNOWN_HEADER_LABELS:
        return _DATAPLATE_KNOWN_HEADER_LABELS[normalized]
    if "cpl" in normalized and "#" in normalized:
        return "N.º CPL"
    return None


def _row_values(row) -> list[str]:
    return [
        cell.get_text(" ", strip=True)
        for cell in row.find_all(["th", "td"])
    ]


def _first_meaningful_row_values(rows, start_index: int) -> list[str]:
    for row in rows[start_index:]:
        values = [value for value in _row_values(row) if value.strip()]
        if values:
            return values
    return []


def _looks_like_model_value_row(values: list[str]) -> bool:
    if len(values) < 2:
        return False
    first = values[0].strip()
    second = values[1].strip()
    return bool(
        first
        and second
        and not _canonical_dataplate_header(first)
        and re.search(r"\bCM\d{3,5}\b", second, flags=re.IGNORECASE)
    )


def _parse_dataplate(html: str) -> dict[str, str]:
    soup = _make_soup(html)
    data: dict[str, str] = {}

    title_cell = soup.find("td", string=lambda t: t and "VIN:" in t)
    if title_cell:
        data["VIN"] = title_cell.get_text(strip=True).split("VIN:")[-1].strip()

    rows = soup.find_all("tr")
    i = 0
    while i < len(rows):
        row = rows[i]
        cell_values = _row_values(row)
        canonical_headers = [
            _canonical_dataplate_header(value) for value in cell_values if value.strip()
        ]
        canonical_headers = [header for header in canonical_headers if header]
        if (
            len(canonical_headers) >= 2
            or (canonical_headers and _looks_like_dataplate_header_row(cell_values))
        ):
            next_values = _first_meaningful_row_values(rows, i + 1)
            for key, val in zip(canonical_headers, next_values):
                if key and key.strip() and "VIN:" not in key:
                    data[key] = val
            i += 2
            continue

        if _looks_like_model_value_row(cell_values):
            for key, val in zip(
                ("Marketing Model Name", "Service Model Name", "EPA Model Name"),
                cell_values,
            ):
                data[key] = val
            i += 1
            continue

        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            for j in range(0, len(cells) - 1, 2):
                key = cells[j].get_text(strip=True)
                val = cells[j + 1].get_text(strip=True)
                if key and key.strip() and "VIN:" not in key:
                    data[key] = val
        i += 1
    return data


def extract_marketing_model_name(dataplate: dict[str, str]) -> str | None:
    if dataplate.get("Marketing Model Name"):
        return dataplate["Marketing Model Name"]
    for key, value in dataplate.items():
        if "marketing model name" in key.lower() and value:
            return value
    return None


def extract_service_model_name(dataplate: dict[str, str]) -> str | None:
    if dataplate.get("Service Model Name"):
        return dataplate["Service Model Name"]
    for key, value in dataplate.items():
        if "service model name" in key.lower() and value:
            return value
    return None


def _get_dataplate(session: requests.Session, cfg: QuickServeConfig) -> dict[str, str]:
    resp = session.get(
        f"{cfg.base_url}/qs3/portal/parts/get_engine_history.html",
        params={"header": "false", "hv": "15", "showDataplate": "true"},
        headers={
            **HEADERS,
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{cfg.base_url}/qs3/portal/service/index.html?message=esnupdated",
        },
        timeout=45,
    )
    if "mylogin.cummins.com" in resp.url:
        return {}
    return _parse_dataplate(resp.text)


def _fetch_dataplate_with_session(
    session: requests.Session, cfg: QuickServeConfig, esn: str
) -> dict[str, str]:
    time.sleep(0.3)
    if not _set_esn(session, cfg, esn):
        return {}
    time.sleep(0.3)
    return _get_dataplate(session, cfg)


def _login_and_cache_session(session: requests.Session, cfg: QuickServeConfig) -> bool:
    if not _authenticate_quickserve(session, cfg):
        return False
    _save_cached_session(session, cfg)
    return True


def get_engine_dataplate(esn: str, cfg: QuickServeConfig) -> dict[str, str]:
    if not _reuse_session_enabled():
        session = _build_session(cfg)
        if not _authenticate_quickserve(session, cfg):
            return {}
        return _fetch_dataplate_with_session(session, cfg, esn)

    session = _build_session(cfg)

    if _load_cached_session(session, cfg):
        dataplate = _fetch_dataplate_with_session(session, cfg, esn)
        if dataplate:
            _save_cached_session(session, cfg)
            return dataplate
        _clear_cached_session(cfg)

    has_lock = _acquire_login_lock(cfg)
    if not has_lock:
        waiting_session = _build_session(cfg)
        if _wait_for_cached_session(waiting_session, cfg):
            dataplate = _fetch_dataplate_with_session(waiting_session, cfg, esn)
            if dataplate:
                _save_cached_session(waiting_session, cfg)
                return dataplate
            _clear_cached_session(cfg)
        has_lock = _acquire_login_lock(cfg)

    try:
        fresh_session = _build_session(cfg)
        if not _login_and_cache_session(fresh_session, cfg):
            return {}
        dataplate = _fetch_dataplate_with_session(fresh_session, cfg, esn)
        if dataplate:
            _save_cached_session(fresh_session, cfg)
        return dataplate
    finally:
        if has_lock:
            _release_login_lock(cfg)


def extract_technical_engine_configuration(dataplate: dict[str, str]) -> str | None:
    if "Technical Engine Configuration #" in dataplate:
        return dataplate["Technical Engine Configuration #"]
    for key, value in dataplate.items():
        if "technical engine configuration" in key.lower():
            return value
    return None


def extract_cpl(dataplate: dict[str, str]) -> str | None:
    for exact_key in ("N.º CPL", "N.o CPL", "No. CPL", "CPL"):
        if dataplate.get(exact_key):
            return dataplate[exact_key]

    for key, value in dataplate.items():
        normalized_key = key.lower()
        if "cpl" in normalized_key and value:
            return value
    return None


def get_technical_config_from_esn(esn: str, cfg: QuickServeConfig) -> str | None:
    dataplate = get_engine_dataplate(esn, cfg)
    if not dataplate:
        return None
    return extract_technical_engine_configuration(dataplate)
