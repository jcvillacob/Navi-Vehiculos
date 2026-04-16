from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


_logger = logging.getLogger("app.security")


def log_security_event(event: str, **fields: Any) -> None:
    payload = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    _logger.warning(json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True))
