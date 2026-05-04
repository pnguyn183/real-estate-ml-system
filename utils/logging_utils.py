from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any


def log_structured(level: int, message: str, **kwargs: Any) -> None:
    payload = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "level": logging.getLevelName(level),
        "message": message,
    }
    if kwargs:
        payload.update(kwargs)
    logger = logging.getLogger("structured")
    # Emit as single JSON string to the logger
    logger.log(level, json.dumps(payload, ensure_ascii=False))


def get_logger(name: str | None = None) -> logging.Logger:
    logger = logging.getLogger(name or "app")
    if not logger.handlers:
        handler = logging.StreamHandler()
        # Simple text formatter; messages are already JSON
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
