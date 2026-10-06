"""Read the control agent's expiring decisions at publication/training boundaries.

This module does not choose a rate or resource allocation. Missing decisions fail
closed only when control is explicitly enabled, and only for the selected topic.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import socket
import time
from typing import Callable, Mapping
from uuid import uuid4


@dataclass(frozen=True)
class PolicySnapshot:
    rate_per_second: float
    training_allowed: bool
    valid: bool
    reason: str
    decision_id: str = ""


@dataclass(frozen=True)
class RuntimePolicy:
    enabled: bool = False
    path: Path = Path("runtime/control/policy.json")
    source_topic: str = "real_estate_stress_raw"
    failsafe_rate: float = 0
    wall_clock: Callable[[], float] = time.time

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimePolicy":
        env = os.environ if env is None else env
        enabled = env.get("CONTROL_ENABLED", "false").lower()
        if enabled not in {"true", "false"}:
            raise ValueError("CONTROL_ENABLED must be true or false")
        failsafe = float(env.get("CONTROL_FAILSAFE_RATE", "0"))
        if not math.isfinite(failsafe) or not 0 <= failsafe <= 10000:
            raise ValueError("CONTROL_FAILSAFE_RATE must be between 0 and 10000")
        topic = env.get("CONTROL_SOURCE_TOPIC", cls.source_topic)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,249}", topic):
            raise ValueError("Invalid CONTROL_SOURCE_TOPIC")
        return cls(enabled == "true", Path(env.get("CONTROL_POLICY_PATH", "runtime/control/policy.json")),
                   topic, failsafe)

    def applies(self, source_topic: str) -> bool:
        return self.enabled and source_topic == self.source_topic

    def read(self, source_topic: str | None = None) -> PolicySnapshot:
        if not self.enabled or (source_topic is not None and not self.applies(source_topic)):
            return PolicySnapshot(math.inf, True, True, "disabled")
        try:
            with self.path.open("rb") as handle:
                payload = handle.read(16385)
            if len(payload) > 16384:
                raise ValueError("oversized policy")
            value = json.loads(payload)
            if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
                raise ValueError("unsupported schema")
            decision_id = value.get("decision_id")
            if not isinstance(decision_id, str) or not 1 <= len(decision_id) <= 200:
                raise ValueError("invalid decision id")
            if value.get("source_topic") != self.source_topic:
                raise ValueError("topic mismatch")
            for key in ("issued_at", "expires_at", "rate_per_second"):
                number = value.get(key)
                if type(number) not in {int, float} or not math.isfinite(number):
                    raise ValueError("invalid numeric field")
            issued, expires, rate = value["issued_at"], value["expires_at"], value["rate_per_second"]
            if not 0 <= rate <= 10000 or not 0 < expires - issued <= 3600:
                raise ValueError("out of bounds")
            now = self.wall_clock()
            if issued > now + 5 or expires <= now:
                raise ValueError("expired or future policy")
            if type(value.get("training_allowed")) is not bool:
                raise ValueError("invalid training permission")
            return PolicySnapshot(float(rate), value["training_allowed"], True, "agent", decision_id)
        except (OSError, ValueError, TypeError, OverflowError):
            return PolicySnapshot(self.failsafe_rate, False, False, "policy_unavailable")


class RateGate:
    """Interruptible, no-burst pacing which rereads the lease while waiting."""

    def __init__(self, policy: RuntimePolicy, source_topic: str, *, clock=time.monotonic,
                 check_interval: float = 0.25):
        if not 0 < check_interval <= 1:
            raise ValueError("check_interval must be between 0 and 1 second")
        self.policy, self.source_topic = policy, source_topic
        self.clock, self.check_interval = clock, check_interval
        self.last_grant: float | None = None

    def published(self) -> None:
        """Anchor spacing to the actual send if preparation after the grant was slow."""
        self.last_grant = self.clock()

    def wait(self, stop, *, deadline: float | None = None,
             requested_rate: float | Callable[[], float] | None = None, poll=None) -> bool:
        if not self.policy.applies(self.source_topic):
            return not stop.is_set() and (deadline is None or self.clock() < deadline)
        while not stop.is_set():
            now = self.clock()
            if deadline is not None and now >= deadline:
                return False
            if poll is not None:
                poll(0)
            decision = self.policy.read(self.source_topic)
            requested = requested_rate() if callable(requested_rate) else requested_rate
            if requested is not None and (not math.isfinite(requested) or requested <= 0):
                raise ValueError("requested rate must be finite and positive")
            rate = min(decision.rate_per_second, requested) if requested is not None else decision.rate_per_second
            due = now if self.last_grant is None else self.last_grant + (1 / rate if rate > 0 else math.inf)
            if rate > 0 and now >= due:
                self.last_grant = now
                return True
            delay = self.check_interval
            if rate > 0:
                delay = min(delay, max(due - now, 0))
            if deadline is not None:
                delay = min(delay, deadline - now)
            if stop.wait(delay):
                return False
        return False


class TrainingDeferred(RuntimeError):
    """The latest agent decision no longer permits a new fit."""


def require_training_permission(policy: RuntimePolicy) -> None:
    if not policy.read().training_allowed:
        raise TrainingDeferred("Control agent deferred the next training fit")


def _write_training_state(policy: RuntimePolicy, status: str, *, started_at=None) -> None:
    path = policy.path.with_name("training.json")
    data = {"status": status, "pid": os.getpid(), "hostname": socket.gethostname(),
            "updated_at": policy.wall_clock(), "started_at": started_at}
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def training_lease(policy: RuntimePolicy | None = None):
    """Serialize controlled training launches; never abort a fit already running.

    OS locks release on process exit, including crashes. Every trainer container
    must mount the same control directory for cross-process exclusion.
    """
    policy = policy or RuntimePolicy.from_env()
    if not policy.enabled:
        yield True
        return
    handle = None
    locked = False
    try:
        policy.path.parent.mkdir(parents=True, exist_ok=True)
        handle = policy.path.with_name("training.lock").open("a+b")
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
    except OSError:
        if handle is not None:
            handle.close()
        yield False
        return
    try:
        if not policy.read().training_allowed:
            _write_training_state(policy, "deferred")
            yield False
            return
        started_at = policy.wall_clock()
        _write_training_state(policy, "running", started_at=started_at)
        try:
            yield True
        except TrainingDeferred:
            _write_training_state(policy, "deferred", started_at=started_at)
            raise
        except BaseException:
            _write_training_state(policy, "failed", started_at=started_at)
            raise
        else:
            _write_training_state(policy, "completed", started_at=started_at)
    finally:
        if locked:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
