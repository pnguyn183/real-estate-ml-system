"""Read-only, bounded readiness checks before a research workload is published.

Retries only cover classified transport failures. Each retry requires two fresh
healthy samples, so an observation gap cannot prove an idle queue. The deadline
is checked between calls; an in-flight topology/collector call remains subject
to its own timeout and cannot be interrupted by this synchronous helper.
"""
from __future__ import annotations

import math
from numbers import Real
import time
from typing import Any, Callable, Mapping

from research.telemetry import transport_error_detail
from research.telemetry_guard import TelemetryRecoveryConfig, TelemetryRecoveryGuard


class PreflightCancelled(RuntimeError):
    """The user requested stop before workload admission began."""


class _AttemptFailure(RuntimeError):
    def __init__(self, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.retryable = retryable


def _finite(value: Any) -> bool:
    try:
        return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    except (ValueError, TypeError, OverflowError):
        return False


def _bounded_error(value: Any) -> str:
    # Persist only errors/reasons, never the complete sample or configuration.
    if isinstance(value, (tuple, list)):
        parts, used = [], 0
        for item in value:
            part = str(item)[:max(0, 2000 - used)]
            parts.append(part)
            used += len(part) + 2
            if used >= 2000:
                break
        return "; ".join(parts)[:2000]
    return str(value)[:2000]


def wait_for_ready(
    collector: Any,
    topology_check: Callable[[], Any],
    *,
    timeout_seconds: float = 0,
    retry_seconds: float = 2,
    stop_requested: Callable[[], bool] | None = None,
    attempts: list[dict] | None = None,
    memory_check: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> tuple[Any, Mapping[str, Any]]:
    """Return validated topology and a second idle sample, or fail without load.

    ``timeout_seconds=0`` preserves single-attempt behavior. A positive timeout
    permits only known transient transport failures to retry within that fixed
    budget. Topology errors, occupied queues, unclassified measurement failures,
    and resource pressure remain fail-fast. No consumer offsets are changed.
    """
    for name, value, minimum in (("timeout_seconds", timeout_seconds, 0), ("retry_seconds", retry_seconds, 0)):
        if not _finite(value) or value < minimum or (name == "retry_seconds" and value == 0):
            raise ValueError(f"{name} must be finite and {'positive' if name == 'retry_seconds' else 'nonnegative'}")
    if attempts is None:
        attempts = []
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    count = 0
    last_failure = "readiness checks have not completed"

    def check_stop() -> None:
        if stop_requested is not None and stop_requested():
            raise PreflightCancelled("preflight cancelled by stop request")

    def check_budget() -> None:
        check_stop()
        if deadline is not None and time.monotonic() >= deadline:
            raise _AttemptFailure(f"preflight readiness timeout: {last_failure}")

    def record(status: str, retryable: bool, error: str | None = None) -> None:
        attempts.append({"count": count, "timestamp": time.time(), "status": status,
                         "retryable": retryable, "errors": [_bounded_error(error)] if error else []})

    def sample_ready(*, second: bool) -> Mapping[str, Any]:
        prefix = ("preflight detected concurrent traffic or missing telemetry"
                  if second else "preflight needs drained queue and healthy telemetry")
        try:
            sample = collector.sample()
        except Exception as exc:
            raise _AttemptFailure(f"{prefix}: {type(exc).__name__}: {_bounded_error(exc)}") from exc
        check_budget()
        if not isinstance(sample, Mapping):
            raise _AttemptFailure(f"{prefix}: collector did not return a measurement mapping")
        if memory_check is not None:
            try:
                reason = memory_check(sample)
            except Exception as exc:
                raise _AttemptFailure(f"{prefix}: memory check failed: {type(exc).__name__}: {_bounded_error(exc)}") from exc
            if reason:
                raise _AttemptFailure(f"{prefix}: {_bounded_error(reason)}")
        # A measured backlog/concurrent producer is not a transport failure.
        lag = sample.get("kafka_lag")
        if _finite(lag) and lag != 0:
            raise _AttemptFailure(f"{prefix}: lag={lag}")
        incoming = sample.get("incoming_rate")
        if second and _finite(incoming) and incoming != 0:
            raise _AttemptFailure(f"{prefix}: incoming_rate={incoming}; finish other stress producers first")
        guard = TelemetryRecoveryGuard(TelemetryRecoveryConfig(
            max_consecutive_errors=10, max_recovery_seconds=120,
            healthy_samples_to_resume=1, allow_core_transport_recovery=True,
        ))
        decision = guard.observe(sample, time.monotonic())
        if decision["state"] == "paused":
            raise _AttemptFailure(f"{prefix}: {_bounded_error(sample.get('errors'))}", retryable=True)
        if decision["state"] != "healthy":
            raise _AttemptFailure(f"{prefix}: {decision['reason']}; errors={_bounded_error(sample.get('errors'))}")
        if second and (not _finite(incoming) or incoming != 0):
            raise _AttemptFailure(f"{prefix}: incoming_rate is unavailable; finish other stress producers first")
        return sample

    while True:
        count += 1
        try:
            check_budget()
            try:
                topology = topology_check()
            except Exception as exc:
                retryable = transport_error_detail("kafka", exc) is not None
                raise _AttemptFailure(f"preflight topology: {type(exc).__name__}: {_bounded_error(exc)}", retryable) from exc
            check_budget()
            sample_ready(second=False)
            idle = sample_ready(second=True)
            record("ready", False)
            return topology, idle
        except PreflightCancelled as exc:
            record("cancelled", False, str(exc))
            raise
        except _AttemptFailure as exc:
            last_failure = _bounded_error(exc)
            exhausted = deadline is None or time.monotonic() >= deadline
            record("failed" if not exc.retryable or exhausted else "retry", exc.retryable, last_failure)
            if not exc.retryable or exhausted:
                prefix = ("preflight readiness timeout: "
                          if deadline is not None and exhausted and not last_failure.startswith("preflight readiness timeout:") else "")
                raise RuntimeError(_bounded_error(prefix + last_failure)) from exc

        # Short slices keep user stop responsive without bypassing the budget.
        retry_until = min(time.monotonic() + retry_seconds, deadline)
        try:
            while time.monotonic() < retry_until:
                check_stop()
                time.sleep(max(0, min(0.2, retry_until - time.monotonic())))
            check_stop()
        except PreflightCancelled as exc:
            record("cancelled", False, str(exc))
            raise
