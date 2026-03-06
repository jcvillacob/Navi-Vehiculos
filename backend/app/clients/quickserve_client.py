from __future__ import annotations

import json
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

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
        headers = [th.get_text(strip=True) for th in row.find_all("th")]
        if headers:
            values_row = rows[i + 1] if i + 1 < len(rows) else None
            if values_row:
                values = [td.get_text(strip=True) for td in values_row.find_all("td")]
                for h, v in zip(headers, values):
                    if h and h.strip():
                        data[h] = v
                i += 2
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


def get_engine_dataplate(esn: str, cfg: QuickServeConfig) -> dict[str, str]:
    session = requests.Session()
    session.cookies.set("GDPR_USER_CONSENT", cfg.username)
    if not _authenticate_quickserve(session, cfg):
        return {}
    time.sleep(0.3)
    if not _set_esn(session, cfg, esn):
        return {}
    time.sleep(0.3)
    return _get_dataplate(session, cfg)


def extract_technical_engine_configuration(dataplate: dict[str, str]) -> str | None:
    if "Technical Engine Configuration #" in dataplate:
        return dataplate["Technical Engine Configuration #"]
    for key, value in dataplate.items():
        if "technical engine configuration" in key.lower():
            return value
    return None


def get_technical_config_from_esn(esn: str, cfg: QuickServeConfig) -> str | None:
    dataplate = get_engine_dataplate(esn, cfg)
    if not dataplate:
        return None
    return extract_technical_engine_configuration(dataplate)