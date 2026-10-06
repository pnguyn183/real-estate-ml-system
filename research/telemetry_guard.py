"""Bounded admission pause for explicitly classified telemetry transport failures.

Core transport recovery is opt-in; unclassified failures remain fail-fast.
The caller must still check hard resource thresholds on every available sample.
All clocks are monotonic values supplied by the caller, never sample timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Any, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class TelemetryRecoveryConfig:
    max_consecutive_errors: int = 1
    max_recovery_seconds: float = 20.0
    healthy_samples_to_resume: int = 2
    allow_core_transport_recovery: bool = False

    def __post_init__(self) -> None:
        for name, upper in (("max_consecutive_errors", 10), ("healthy_samples_to_resume", 5)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f"{name} must be an integer between 1 and {upper}")
        if not _finite_number(self.max_recovery_seconds) or not 1 <= self.max_recovery_seconds <= 120:
            raise ValueError("max_recovery_seconds must be finite and between 1 and 120")
        if not isinstance(self.allow_core_transport_recovery, bool):
            raise ValueError("allow_core_transport_recovery must be a boolean")


def _finite_number(value: Any) -> bool:
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


# librdkafka local transport codes: _TIMED_OUT, _TRANSPORT, _ALL_BROKERS_DOWN.
_RECOVERABLE_KAFKA_CODES = frozenset((-185, -195, -187))


def _transport_error_sources(sample: Mapping[str, Any], allow_core: bool) -> set[str] | None:
    """Match every error to one allowed structured source, never guess from text."""
    errors = sample.get("errors")
    if allow_core and "collection_transport_errors" in sample:
        details = sample.get("collection_transport_errors")
    else:
        details = sample.get("worker_transport_errors")
    if not isinstance(errors, list) or not errors or not isinstance(details, list) or len(errors) != len(details):
        return None
    unmatched = list(errors)
    seen_sources: set[str] = set()
    for detail in details:
        if not isinstance(detail, dict):
            return None
        source, error_type = detail.get("source"), detail.get("error_type")
        if not isinstance(source, str) or source in seen_sources:
            return None
        if allow_core and source == "docker":
            if error_type != "TimeoutExpired":
                return None
        elif allow_core and source == "kafka":
            code = detail.get("error_code")
            if (error_type != "KafkaException" or isinstance(code, bool)
                    or not isinstance(code, int) or code not in _RECOVERABLE_KAFKA_CODES):
                return None
        else:
            try:
                parsed = urlsplit(source)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    return None
            except ValueError:
                return None
            if error_type not in ("ReadTimeout", "ConnectTimeout", "ConnectionError", "Timeout"):
                return None
        prefix = f"{source}: {error_type}:"
        match = next((index for index, error in enumerate(unmatched)
                      if isinstance(error, str) and error.startswith(prefix)), None)
        if match is None:
            return None
        unmatched.pop(match)
        seen_sources.add(source)
    return seen_sources if not unmatched else None


class TelemetryRecoveryGuard:
    """Pause immediately; resume after good samples or stop within a fixed budget."""

    def __init__(self, config: TelemetryRecoveryConfig):
        self.config = config
        self.consecutive_errors = 0
        self.consecutive_healthy = 0
        self._paused_since: float | None = None
        self._stop_reason: str | None = None
        self._last_now: float | None = None

    @property
    def paused(self) -> bool:
        return self._paused_since is not None or self._stop_reason is not None

    def deadline_reason(self, now: float) -> str | None:
        """Check the budget between samples, so a slow next sample cannot extend it."""
        if self._stop_reason is not None:
            return self._stop_reason
        if not _finite_number(now) or (self._last_now is not None and now < self._last_now):
            self._stop_reason = "telemetry recovery clock is invalid or moved backwards"
        else:
            self._last_now = now
            if self._paused_since is not None and now - self._paused_since >= self.config.max_recovery_seconds:
                self._stop_reason = "telemetry recovery time budget exhausted"
        return self._stop_reason

    def _decision(self, state: str, reason: str | None = None) -> dict[str, Any]:
        return {"state": state, "reason": reason, "admission_paused": self.paused,
                "telemetry_usable": state in ("healthy", "resumed")}

    def _stop(self, reason: str) -> dict[str, Any]:
        self._stop_reason = reason
        return self._decision("stopped", reason)

    def observe(self, sample: Mapping[str, Any], now: float) -> dict[str, Any]:
        deadline = self.deadline_reason(now)
        if deadline is not None:
            return self._decision("stopped", deadline)
        if not isinstance(sample, Mapping):
            return self._stop("required CPU, RAM, or Kafka lag telemetry is unavailable")

        errors = sample.get("errors")
        sources = _transport_error_sources(sample, self.config.allow_core_transport_recovery) if errors else None
        for key, source in (("cpu_percent", "docker"), ("ram_percent", "docker"), ("kafka_lag", "kafka")):
            value = sample.get(key)
            if _finite_number(value):
                continue
            if (self.config.allow_core_transport_recovery and sources is not None
                    and source in sources and value is None):
                continue
            return self._stop("required CPU, RAM, or Kafka lag telemetry is unavailable")

        if errors:
            if sources is None:
                scope = "collection" if self.config.allow_core_transport_recovery else "worker"
                return self._stop(f"telemetry contains an error outside {scope} transport recovery")
            if self._paused_since is None:
                self._paused_since = now
            self.consecutive_errors += 1
            self.consecutive_healthy = 0
            if self.consecutive_errors >= self.config.max_consecutive_errors:
                scope = "collection" if self.config.allow_core_transport_recovery else "worker"
                return self._stop(f"consecutive {scope} telemetry error budget exhausted")
            reason = ("collection telemetry transport unavailable; admission paused"
                      if sources & {"docker", "kafka"} else "worker metrics transport unavailable; admission paused")
            return self._decision("paused", reason)

        # Explicit readiness is mandatory; no-data derivatives may still be None.
        if (errors != [] or sample.get("instrumentation_ready") is not True
                or sample.get("worker_transport_errors") or sample.get("collection_transport_errors")):
            return self._stop("worker telemetry instrumentation is not ready")
        self.consecutive_errors = 0
        if self._paused_since is None:
            return self._decision("healthy")
        self.consecutive_healthy += 1
        if self.consecutive_healthy < self.config.healthy_samples_to_resume:
            return self._decision("paused", "waiting for consecutive healthy telemetry samples")
        self._paused_since = None
        self.consecutive_healthy = 0
        return self._decision("resumed")
