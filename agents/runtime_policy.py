"""Read the control agent's expiring decisions at publication/training boundaries.

This module does not choose a rate or resource allocation. Missing decisions fail
closed only when control is explicitly enabled, and only for the selected topic.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
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
            for attempt in range(4):
                try:
                    with self.path.open("rb") as handle:
                        payload = handle.read(16385)
                    break
                except PermissionError:
                    # Atomic replacement can briefly deny new handles on
                    # Windows. Retry only this filesystem race, not bad data.
                    if attempt == 3:
                        raise
                    time.sleep(.01)
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


_ACTIVE_TRAINING_PHASES = {"preparing", "fitting"}
_TERMINAL_TRAINING_PHASES = {"completed", "deferred", "failed", "interrupted"}
_TRAINING_TRANSITIONS = {
    "new": {"preparing", "deferred"},
    "preparing": {"fitting", "deferred", "failed", "interrupted"},
    "fitting": {"completed", "deferred", "failed", "interrupted"},
}


@dataclass
class _TrainingRun:
    path: Path
    run_id: str
    decision_id: str
    started_at: float | None
    phase: str = "new"


_CURRENT_TRAINING_RUN: ContextVar[_TrainingRun | None] = ContextVar(
    "current_training_run", default=None)


def _write_training_state(policy: RuntimePolicy, data: dict) -> None:
    path = policy.path.with_name("training.json")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=True, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_training_event(policy: RuntimePolicy, event: dict) -> None:
    path = policy.path.with_name("training_events.jsonl")
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(event, ensure_ascii=True, allow_nan=False) + "\n")
        output.flush()
        os.fsync(output.fileno())


def _training_transition(policy: RuntimePolicy, run: _TrainingRun, transition: str, *,
                         reason: str | None = None, sample_count: int | None = None) -> None:
    if transition not in _TRAINING_TRANSITIONS.get(run.phase, set()):
        raise RuntimeError(f"Invalid training transition: {run.phase} -> {transition}")
    if sample_count is not None and (type(sample_count) is not int or sample_count < 0):
        raise ValueError("sample_count must be a non-negative integer")
    if (run.started_at is not None
            and (isinstance(run.started_at, bool) or not isinstance(run.started_at, (int, float))
                 or not math.isfinite(run.started_at))):
        raise RuntimeError("Training start clock is invalid")
    now = policy.wall_clock()
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise RuntimeError("Training state clock is invalid")
    status = "running" if transition in _ACTIVE_TRAINING_PHASES else transition
    event = {"schema_version": 1, "run_id": run.run_id, "decision_id": run.decision_id,
             "source_topic": policy.source_topic, "status": status, "phase": transition,
             "pid": os.getpid(), "hostname": socket.gethostname(), "updated_at": float(now),
             "started_at": run.started_at}
    if reason is not None:
        event["reason"] = reason
    if sample_count is not None:
        event["sample_count"] = sample_count
    # The append-only event is the durable source of truth.  Training fails
    # closed if it cannot be fsynced; the atomic snapshot is the model-facing
    # current view and is updated only after the event is durable.
    _append_training_event(policy, event)
    _write_training_state(policy, event)
    run.phase = transition


def _last_training_event(policy: RuntimePolicy) -> dict | None:
    path = policy.path.with_name("training_events.jsonl")
    try:
        with path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            size = source.tell()
            source.seek(max(0, size - 16384))
            tail = source.read(16385)
        if len(tail) > 16384:
            tail = tail[-16384:]
        lines = [line for line in tail.splitlines() if line.strip()]
        value = json.loads(lines[-1]) if lines else None
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError, IndexError):
        return None


def _reconcile_interrupted_training(policy: RuntimePolicy) -> None:
    path = policy.path.with_name("training.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    phase = value.get("phase")
    if phase not in _ACTIVE_TRAINING_PHASES:
        # Backward compatibility with the previous unversioned snapshot.
        phase = "preparing" if value.get("status") == "running" else None
    if phase not in _ACTIVE_TRAINING_PHASES:
        return
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        run_id = uuid4().hex
    last_event = _last_training_event(policy)
    if isinstance(last_event, dict) and last_event.get("run_id") == run_id:
        last_phase = last_event.get("phase")
        if last_phase in _TERMINAL_TRAINING_PHASES:
            # The durable event was fsynced but atomic snapshot replacement did
            # not finish. Restore the derived snapshot without inventing a crash.
            _write_training_state(policy, last_event)
            return
        if last_phase in _ACTIVE_TRAINING_PHASES:
            value, phase = last_event, last_phase
    decision_id = value.get("decision_id") if isinstance(value.get("decision_id"), str) else ""
    started_at = value.get("started_at")
    if (isinstance(started_at, bool) or not isinstance(started_at, (int, float))
            or not math.isfinite(started_at)):
        started_at = None
    stale = _TrainingRun(policy.path, run_id, decision_id, started_at, phase)
    _training_transition(policy, stale, "interrupted", reason="stale_state_after_lock_recovery")


def mark_training_fitting(policy: RuntimePolicy, *, sample_count: int | None = None) -> None:
    """Recheck permission and durably mark the exact boundary before model fit."""
    if not policy.enabled:
        return
    run = _CURRENT_TRAINING_RUN.get()
    if run is None or run.path.resolve() != policy.path.resolve():
        raise RuntimeError("No active controlled training lease")
    require_training_permission(policy)
    if run.phase == "fitting":
        # A command may contain multiple sequential fit operations. Recheck the
        # lease at each boundary without manufacturing a duplicate transition.
        return
    _training_transition(policy, run, "fitting", sample_count=sample_count)


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
        # Acquiring the OS lock proves that no compliant training process is
        # still active. Reconcile a snapshot left by a crashed predecessor
        # before recording this attempt.
        _reconcile_interrupted_training(policy)
        decision = policy.read()
        if not decision.training_allowed:
            deferred = _TrainingRun(policy.path, uuid4().hex, decision.decision_id, None)
            _training_transition(policy, deferred, "deferred", reason="policy_denied_before_prepare")
            yield False
            return
        started_at = policy.wall_clock()
        run = _TrainingRun(policy.path, uuid4().hex, decision.decision_id, started_at)
        _training_transition(policy, run, "preparing")
        token = _CURRENT_TRAINING_RUN.set(run)
        try:
            yield True
        except TrainingDeferred:
            _training_transition(policy, run, "deferred", reason="policy_revoked_before_fit")
            raise
        except BaseException as exc:
            _training_transition(policy, run, "failed", reason=type(exc).__name__)
            raise
        else:
            if run.phase == "fitting":
                _training_transition(policy, run, "completed")
            else:
                _training_transition(policy, run, "deferred", reason="fit_not_started")
        finally:
            _CURRENT_TRAINING_RUN.reset(token)
    finally:
        if locked:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
