"""Opt-in, event-scoped provider evidence; never records transport headers.

Only operator-selected event IDs are captured. Listing text can be private:
keep the Mongo collection/runtime exports local. Audit failures do not change
extraction decisions and are surfaced as bounded structured log events.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re

_sink = ContextVar("provider_audit_sink", default=None)
log = logging.getLogger(__name__)


def active():
    return _sink.get() is not None


def scrub(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if re.search(r"api.?key|authorization|access.?token|secret|password", str(k), re.I)
                else scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"AIza[0-9A-Za-z_-]{30,}", "[REDACTED]", value)
        return value
    return value


def emit(stage, payload):
    sink = _sink.get()
    if sink is not None:
        try:
            sink(stage, payload)
        except Exception:
            # Never log an exception body: it might contain a URL or credentials.
            log.warning('provider_audit_write_failed stage=%s', stage)


@contextmanager
def capture(database, event_id, before):
    selected = {v.strip() for v in os.getenv("AI_AUDIT_EVENT_IDS", "").split(",") if v.strip()}
    if event_id not in selected:
        yield
        return
    secrets = tuple(os.getenv(name, "") for name in ("LLM_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"))
    def sink(stage, payload):
        safe = scrub(payload, secrets)
        serialized = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
        row = {"stage": stage, "timestamp": datetime.now(timezone.utc).isoformat(),
               "payload": safe, "sha256": hashlib.sha256(serialized.encode()).hexdigest()}
        database["ai_provider_audit"].update_one({"_id": event_id}, {"$set": {"event_id": event_id},
                              "$push": {"stages": row}}, upsert=True)
        log.info(json.dumps({"event": "provider_audit", "event_id": event_id,
                             "stage": stage, "sha256": row["sha256"]}))
    token = _sink.set(sink)
    try:
        emit("before", {"record": before})
        yield
    finally:
        _sink.reset(token)
