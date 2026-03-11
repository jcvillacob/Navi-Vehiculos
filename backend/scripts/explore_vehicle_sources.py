from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTAINER_APP_DIR = Path("/app")

for candidate in (ROOT / "backend", ROOT, CONTAINER_APP_DIR):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.clients.geotab_client import (
    get_device_from_plate,
    get_device_from_vin,
    get_vin_from_plate as get_geotab_vin,
)
from app.clients.quickserve_client import get_engine_dataplate
from app.clients.sql_client import (
    get_vehicle_by_plate,
    get_vehicle_by_plate_full,
    get_vehicle_by_vin,
    get_vehicle_by_vin_full,
)
from app.core.config import load_geotab_config, load_quickserve_config, load_sql_config

VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def is_vin(value: str) -> bool:
    return bool(VIN_PATTERN.fullmatch(value.strip().upper()))


def pretty_json(payload) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str)


def build_markdown(identifier: str, payload: dict) -> str:
    return f"""# Estructura exploratoria de respuestas

Identificador consultado: `{identifier}`

## Geotab

```json
{pretty_json(payload["geotab"])}
```

## Fenix (SQL)

```json
{pretty_json(payload["fenix"])}
```

## Fenix (seleccion usada por la app)

```json
{pretty_json(payload["fenix_selected"])}
```

## Cummins

```json
{pretty_json(payload["cummins"])}
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Explora respuestas crudas de Geotab, Fenix y Cummins.")
    parser.add_argument("identifier", help="Placa o VIN")
    parser.add_argument(
        "--write-md",
        default=str(ROOT / "docs" / "source-response-structures.generated.md"),
        help="Ruta del markdown de salida",
    )
    args = parser.parse_args()

    load_dotenv_file(ROOT / ".env")

    identifier = args.identifier.strip().upper()
    lookup_type = "vin" if is_vin(identifier) else "plate"

    payload: dict[str, object] = {
        "lookup_type": lookup_type,
        "lookup_value": identifier,
        "geotab": {},
        "fenix": {},
        "fenix_selected": {},
        "cummins": {},
    }

    plate = identifier if lookup_type == "plate" else None
    vin = identifier if lookup_type == "vin" else None

    try:
        sql_cfg = load_sql_config()
        if lookup_type == "plate":
            geotab_cfg = load_geotab_config()
            payload["geotab"] = {
                "device": get_device_from_plate(identifier, geotab_cfg),
                "vin_from_geotab": get_geotab_vin(identifier, geotab_cfg),
            }
            payload["fenix"] = get_vehicle_by_plate_full(identifier, sql_cfg) or {}
            payload["fenix_selected"] = get_vehicle_by_plate(identifier, sql_cfg) or {}
            vin = payload["fenix_selected"].get("VIN") or payload["geotab"].get("vin_from_geotab")
        else:
            payload["fenix"] = get_vehicle_by_vin_full(identifier, sql_cfg) or {}
            payload["fenix_selected"] = get_vehicle_by_vin(identifier, sql_cfg) or {}
            plate = payload["fenix_selected"].get("plate")
            geotab_cfg = load_geotab_config()
            payload["geotab"] = {
                "device_by_plate": get_device_from_plate(str(plate), geotab_cfg) if plate else None,
                "device_by_vin": get_device_from_vin(identifier, geotab_cfg),
                "vin_from_geotab": get_geotab_vin(str(plate), geotab_cfg) if plate else None,
            }

        engine_number = payload["fenix_selected"].get("numero_motor")
        if engine_number:
            quickserve_cfg = load_quickserve_config()
            payload["cummins"] = get_engine_dataplate(str(engine_number), quickserve_cfg)
    except Exception as exc:
        payload["error"] = str(exc)

    markdown = build_markdown(identifier, payload)
    output_path = Path(args.write_md)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")

    print(pretty_json(payload))
    print(f"\nMarkdown guardado en: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
