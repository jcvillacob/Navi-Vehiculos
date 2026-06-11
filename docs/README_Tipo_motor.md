# geotab_vin_motor

## Objetivo
Este módulo tiene un único objetivo:

1. Recibir una placa.
2. Buscar el VIN en Geotab.
3. Buscar el número de motor (ESN) en `dbo.T_DIM_VEHICULO_CONFIABILIDAD`.
4. Consultar QuickServe con ese ESN.
5. Devolver `Technical Engine Configuration #`.

## Cómo lo hace
El flujo está modularizado en 4 capas:

1. `config.py`:
- Carga credenciales/configuración (hardcodeadas por defecto, con override por `.env`).

2. `geotab_client.py`:
- Autentica en Geotab.
- Busca dispositivo por placa.
- Extrae VIN.

3. `sql_client.py`:
- Conecta a SQL Server.
- Busca `Número de motor` usando VIN.

4. `quickserve_client.py`:
- Ejecuta autenticación SSO de Salesforce/QuickServe.
- Hace `set_esn`.
- Obtiene y parsea dataplate.
- Extrae `Technical Engine Configuration #`.

El script de entrada es `buscar_tec_config_desde_placa.py`.

## Uso
Desde la raíz del proyecto:

```bash
python geotab_vin_motor/buscar_tec_config_desde_placa.py TLK240
```

También soporta:

```bash
python -m geotab_vin_motor.buscar_tec_config_desde_placa TLK240
```

## Librerías necesarias
Instala (en tu `venv`) estas librerías:

```bash
pip install mygeotab pymssql requests beautifulsoup4 python-dotenv lxml
```

Notas:
- Si `lxml` no está disponible, el parser cae automáticamente a `html.parser`.
- En Windows, `pymssql` puede requerir wheels compatibles con tu versión de Python.

## Variables de entorno (opcionales)
El código ya trae defaults hardcodeados. Si quieres sobreescribirlos:

```env
GEOTAB_USERNAME=
GEOTAB_PASSWORD=
GEOTAB_DATABASE=

INVENTORY_DB_SERVER=
INVENTORY_DB_USER=
INVENTORY_DB_PASSWORD=
INVENTORY_DB_NAME=
INVENTORY_DB_PORT=

QUICKSERVE_USERNAME=
QUICKSERVE_PASSWORD=
QUICKSERVE_APP_ID=
QUICKSERVE_SF_BASE=
QUICKSERVE_BASE_URL=
```

---

## Código completo: `buscar_tec_config_desde_placa.py`

```python
"""
Script principal (unico objetivo de la carpeta):
Placa -> VIN (Geotab) -> Numero de motor (SQL) -> QuickServe -> Technical Engine Configuration #
"""

import sys

from dotenv import load_dotenv

try:
    from geotab_vin_motor.config import (
        load_geotab_config,
        load_quickserve_config,
        load_sql_config,
    )
    from geotab_vin_motor.geotab_client import build_client, extract_vin, find_device_by_plate
    from geotab_vin_motor.quickserve_client import (
        extract_technical_engine_configuration,
        get_engine_dataplate,
    )
    from geotab_vin_motor.sql_client import find_engine_number_by_vin, open_connection
except ModuleNotFoundError:
    # Permite ejecutar como:
    # python geotab_vin_motor/buscar_tec_config_desde_placa.py <PLACA>
    from config import load_geotab_config, load_quickserve_config, load_sql_config
    from geotab_client import build_client, extract_vin, find_device_by_plate
    from quickserve_client import extract_technical_engine_configuration, get_engine_dataplate
    from sql_client import find_engine_number_by_vin, open_connection


def run(plate: str):
    geotab_cfg = load_geotab_config()
    sql_cfg = load_sql_config()
    quickserve_cfg = load_quickserve_config()

    geotab_client = build_client(geotab_cfg)
    device = find_device_by_plate(geotab_client, plate)
    if not device:
        print("No se encontro vehiculo en Geotab para esa placa.")
        return 0

    vin = extract_vin(device)
    if not vin:
        print("El vehiculo existe en Geotab, pero no trae VIN.")
        return 0

    conn = None
    try:
        conn = open_connection(sql_cfg)
        row = find_engine_number_by_vin(conn, vin)
    finally:
        if conn:
            conn.close()

    if not row:
        print("No se encontro el VIN en dbo.T_DIM_VEHICULO_CONFIABILIDAD.")
        return 0

    esn = row.get("numero_motor")
    if not esn:
        print("Se encontro VIN en SQL, pero sin Numero de motor.")
        return 0

    print(f"Placa: {plate}")
    print(f"VIN: {vin}")
    print(f"Numero de motor (ESN): {esn}")
    print("Consultando QuickServe...")

    dataplate = get_engine_dataplate(str(esn).strip(), quickserve_cfg)
    if not dataplate:
        print("No se pudo obtener dataplate desde QuickServe.")
        return 0

    tech = extract_technical_engine_configuration(dataplate)
    print("\nResultado final:")
    print(f"Technical Engine Configuration #: {tech}")
    return 0


def main():
    load_dotenv()
    if len(sys.argv) < 2:
        print("Uso: python geotab_vin_motor/buscar_tec_config_desde_placa.py <PLACA>")
        sys.exit(1)

    placa = sys.argv[1].strip().upper()
    try:
        sys.exit(run(placa))
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

## Código completo: `config.py`

```python
"""Carga y validacion de configuraciones desde variables de entorno."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GeotabConfig:
    username: str
    password: str
    database: str


@dataclass(frozen=True)
class SqlConfig:
    server: str
    user: str
    password: str
    database: str
    port: int


@dataclass(frozen=True)
class QuickServeConfig:
    username: str
    password: str
    app_id: str
    sf_base: str
    base_url: str


def load_geotab_config() -> GeotabConfig:
    username = os.getenv("GEOTAB_USERNAME")
    password = os.getenv("GEOTAB_PASSWORD")
    database = os.getenv("GEOTAB_DATABASE")
    return GeotabConfig(username=username, password=password, database=database)


def load_sql_config() -> SqlConfig:
    return SqlConfig(
        server=os.getenv("INVENTORY_DB_SERVER"),
        user=os.getenv("INVENTORY_DB_USER"),
        password=os.getenv("INVENTORY_DB_PASSWORD"),
        database=os.getenv("INVENTORY_DB_NAME"),
        port=int(os.getenv("INVENTORY_DB_PORT", "1433")),
    )


def load_quickserve_config() -> QuickServeConfig:
    username = os.getenv("QUICKSERVE_USERNAME")
    password = os.getenv("QUICKSERVE_PASSWORD")
    return QuickServeConfig(
        username=username,
        password=password,
        app_id=os.getenv("QUICKSERVE_APP_ID"),
        sf_base=os.getenv("QUICKSERVE_SF_BASE"),
        base_url=os.getenv("QUICKSERVE_BASE_URL"),
    )
```

## Código completo: `geotab_client.py`

```python
"""Consultas a Geotab para obtener VIN por placa."""

import mygeotab

try:
    from geotab_vin_motor.config import GeotabConfig
except ModuleNotFoundError:  # Ejecucion directa del script principal
    from config import GeotabConfig


def build_client(cfg: GeotabConfig):
    client = mygeotab.API(
        username=cfg.username,
        password=cfg.password,
        database=cfg.database,
    )
    client.authenticate()
    return client


def find_device_by_plate(client, plate: str):
    devices = client.call("Get", typeName="Device", search={"licensePlate": plate})
    if not devices:
        return None
    return devices[0]


def extract_vin(device: dict):
    # El VIN puede variar segun tenant/campos expuestos por Geotab.
    for key in ("vehicleIdentificationNumber", "vin", "VIN"):
        value = device.get(key)
        if value:
            return str(value).strip()
    return None
```

## Código completo: `sql_client.py`

```python
"""Consultas a SQL Server para obtener el numero de motor desde VIN."""

import pymssql

try:
    from geotab_vin_motor.config import SqlConfig
except ModuleNotFoundError:  # Ejecucion directa del script principal
    from config import SqlConfig


def open_connection(cfg: SqlConfig):
    return pymssql.connect(
        server=cfg.server,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        port=cfg.port,
        as_dict=True,
        timeout=30,
        login_timeout=30,
    )


def find_engine_number_by_vin(conn, vin: str):
    query = """
    SELECT TOP 1
        VIN,
        [Número de motor] AS numero_motor
    FROM dbo.T_DIM_VEHICULO_CONFIABILIDAD
    WHERE VIN = %s
    """
    with conn.cursor(as_dict=True) as cursor:
        cursor.execute(query, (vin,))
        return cursor.fetchone()
```

## Código completo: `quickserve_client.py`

```python
"""Cliente QuickServe: autenticacion SSO y lectura de dataplate por ESN."""

import json
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    from geotab_vin_motor.config import QuickServeConfig
except ModuleNotFoundError:  # Ejecucion directa del script principal
    from config import QuickServeConfig

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
    # Fallback para entornos que no tienen lxml instalado.
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
        "actions": [{
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
        }]
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
        raise Exception(f"Login QuickServe fallido: {action.get('error', '')}")
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
        raise Exception("No se encontro redirect IAM_Authorize en frontdoor.jsp")
    path = match.group(1)
    return path if path.startswith("http") else cfg.sf_base + path


def _complete_saml_sso(session: requests.Session, iam_url: str, cfg: QuickServeConfig) -> bool:
    nav_headers = {**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"}
    resp = session.get(iam_url, headers=nav_headers, allow_redirects=True, timeout=45)
    soup = _make_soup(resp.text)
    if not soup.find("input", {"id": "com.salesforce.visualforce.ViewState"}):
        raise Exception("ViewState no encontrado en IAM_Authorize")

    viewstate_fields = {}
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
    dynamic_params = {}
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


def _parse_dataplate(html: str) -> dict:
    soup = _make_soup(html)
    data = {}

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


def _get_dataplate(session: requests.Session, cfg: QuickServeConfig) -> dict:
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


def get_engine_dataplate(esn: str, cfg: QuickServeConfig) -> dict:
    session = requests.Session()
    session.cookies.set("GDPR_USER_CONSENT", cfg.username)
    if not _authenticate_quickserve(session, cfg):
        return {}
    time.sleep(0.3)
    if not _set_esn(session, cfg, esn):
        return {}
    time.sleep(0.3)
    return _get_dataplate(session, cfg)


def extract_technical_engine_configuration(dataplate: dict):
    if "Technical Engine Configuration #" in dataplate:
        return dataplate["Technical Engine Configuration #"]
    for key, value in dataplate.items():
        if "technical engine configuration" in key.lower():
            return value
    return None
```

## Código completo: `__init__.py`

```python
"""Utilidades para resolver: Placa -> VIN -> ESN -> Technical Engine Configuration #."""
```

