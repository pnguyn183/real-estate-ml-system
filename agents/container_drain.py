"""Cooperative idle boundary for stopping the trainer without interrupting a fit.

Only the trainer's main thread calls this helper, outside ``run_trainer``. A
request cannot interrupt work: it is acknowledged only after that work returns.
The controller must revoke training permission before waiting for the receipt.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import socket
import tempfile
import time


MAX_DRAIN_LEASE_SECONDS = 120.0
POLL_SECONDS = 1.0


def _read_json(path: Path):
    try:
        with path.open("rb") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _request(path: Path, now: float):
    value = _read_json(path)
    if value is None or set(value) != {"request_id", "expires_at"}:
        return None
    request_id, expires = value["request_id"], value["expires_at"]
    if (not isinstance(request_id, str)
            or re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", request_id) is None
            or type(expires) not in {int, float} or not math.isfinite(expires)
            or not 0 < expires - now <= MAX_DRAIN_LEASE_SECONDS):
        return None
    return value


def _acknowledge(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as output:
            name = output.name
            json.dump(value, output, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        # Windows readers may briefly hold a handle without delete sharing.
        for attempt in range(4):
            try:
                os.replace(name, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(.01)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def _clear_own_ack(path: Path, request_id: str | None):
    value = _read_json(path)
    if request_id is not None and value and value.get("request_id") == request_id:
        path.unlink(missing_ok=True)


def wait_for_container_drain(policy, *, sleep_seconds: float = 0,
                             clock=time.time, monotonic=time.monotonic,
                             sleep=time.sleep, hostname: str | None = None):
    """Wait at an idle boundary, servicing drain requests during ordinary sleep.

    This function never grants training permission and never writes the policy.
    Callers must still use the existing training lease and permission checks.
    Malformed or expired requests cannot authorize Docker to stop the trainer.
    Once acknowledged, no new work starts until the request ends, even when the
    ordinary sleep deadline has passed or a subsequent policy permits training.
    """
    if (type(sleep_seconds) not in {int, float} or not math.isfinite(sleep_seconds)
            or sleep_seconds < 0):
        raise ValueError("sleep_seconds must be finite and nonnegative")
    if not policy.enabled:
        if sleep_seconds:
            sleep(sleep_seconds)
        return
    request_path = policy.path.with_name("trainer-drain.json")
    ack_path = policy.path.with_name("trainer-drain-ack.json")
    identity = hostname if hostname is not None else socket.gethostname()
    deadline = monotonic() + sleep_seconds
    acknowledged = None
    try:
        while True:
            now = clock()
            request = _request(request_path, now)
            if request is not None:
                if acknowledged != request["request_id"]:
                    _clear_own_ack(ack_path, acknowledged)
                    acknowledged = None
                # A request blocks future work immediately. The acknowledgement
                # additionally proves the controller revoked training permission.
                if not policy.read().training_allowed:
                    _acknowledge(ack_path, {"request_id": request["request_id"],
                                           "hostname": identity, "timestamp": now})
                    acknowledged = request["request_id"]
                delay = min(POLL_SECONDS, request["expires_at"] - now)
            else:
                _clear_own_ack(ack_path, acknowledged)
                acknowledged = None
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return
                delay = min(POLL_SECONDS, remaining)
            sleep(delay)
    finally:
        _clear_own_ack(ack_path, acknowledged)
