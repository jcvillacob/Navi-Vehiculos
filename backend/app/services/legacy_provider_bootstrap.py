from __future__ import annotations

import unicodedata

from app.services.performance_types import PerformanceTarget


def _normalize_token(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(char for char in normalized if not unicodedata.combining(char))


_LEGACY_ARTIMO_BINDINGS: dict[tuple[str, str], dict[str, str]] = {
    ("opperar", "artimo"): {
        "TLK520": "68065b1e-f1a2-4510-b1b5-2fc9571b2b18",
        "TLK521": "44c21d53-2969-4b60-8d9e-22e837782ec5",
        "TLK522": "c1140340-25f5-4dd4-835d-19dd78cd6059",
        "TLK523": "9d3c9acc-5bd9-47fd-a981-dd026087cd65",
        "TLK524": "1f4c222d-c79f-4ce2-b0b8-ce053f0f9469",
        "TLK525": "2406b72d-6a39-47e3-aaa7-60f50c14f696",
        "TLK526": "a09748d5-d392-425f-8e5e-3090ada7632a",
        "TLK527": "0884ba93-f1a7-4639-98b6-17ab2bddedc4",
        "TLK528": "2063037e-4652-4d18-adb4-c1563e44c2b8",
        "TLK529": "53bc1c18-a38b-4bb1-a026-a2ea65916bab",
    }
}


def get_legacy_provider_vehicle_id(target: PerformanceTarget) -> str | None:
    if target.provider_key != "artimo":
        return None
    lookup_key = (_normalize_token(target.client_name), _normalize_token(target.database_name))
    bindings = _LEGACY_ARTIMO_BINDINGS.get(lookup_key, {})
    value = bindings.get(target.plate)
    return value.strip() if isinstance(value, str) and value.strip() else None
