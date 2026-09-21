"""Bounded feedback control for the isolated Kafka traffic experiment.

The observer runs independently of the decision clock.  ``observe`` records the
first *sampled* risk crossing (T0); ``decide`` records detection (T1), and the
caller acknowledges a real rate-gate change (T2).  Recovery (T3) requires a
continuous window below the separate recovery thresholds.  These timestamps
describe measured samples and actions, not an inferred physical onset.

This policy is an interpretable adaptive baseline, not a trained forecast or a
worker autoscaler.  Missing/stale telemetry cannot justify a rate increase.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import time
from typing import Any


@dataclass(frozen=True)
class ControlConfig:
    enabled: bool = False
    min_rate: float = 1.0
    max_rate: float = 100.0
    initial_rate: float = 100.0
    decrease_factor: float = .7
    increase_step: float = 5.0
    cooldown_seconds: float = 10.0
    recovery_window_seconds: float = 15.0
    telemetry_max_age_seconds: float = 10.0
    cpu_high_percent: float = 85.0
    cpu_low_percent: float = 70.0
    ram_high_percent: float = 90.0
    ram_low_percent: float = 80.0
    lag_high: float = 1000.0
    lag_low: float = 100.0
    latency_high_seconds: float = 2.0
    latency_low_seconds: float = 1.0
    error_high: float = .01
    error_low: float = .001

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        for name, value in asdict(self).items():
            if name != "enabled" and not _finite_nonnegative(value):
                raise ValueError(f"{name} must be a finite nonnegative number")
        if not 0 < self.min_rate <= self.initial_rate <= self.max_rate <= 10_000:
            raise ValueError("rates must satisfy 0 < min <= initial <= max <= 10000")
        if not 0 < self.decrease_factor < 1 or self.increase_step <= 0:
            raise ValueError("decrease_factor must be in (0, 1) and increase_step positive")
        if self.telemetry_max_age_seconds <= 0 or self.recovery_window_seconds <= 0:
            raise ValueError("telemetry age and recovery window must be positive")
        for low_name, high_name, maximum in (
            ("cpu_low_percent", "cpu_high_percent", 100),
            ("ram_low_percent", "ram_high_percent", 100),
            ("lag_low", "lag_high", math.inf),
            ("latency_low_seconds", "latency_high_seconds", math.inf),
            ("error_low", "error_high", 1),
        ):
            if not getattr(self, low_name) < getattr(self, high_name) <= maximum:
                raise ValueError(f"require 0 <= {low_name} < {high_name} <= {maximum}")


def _finite_nonnegative(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


@dataclass(frozen=True)
class Observation:
    timestamp: float
    # Offered demand BEFORE rate-gate admission; not the accepted Kafka rate.
    incoming_rate: float | None = None
    throughput: float | None = None
    # Selected pipeline CPU / daemon cores; working-set RAM / daemon memory.
    cpu_percent: float | None = None
    ram_percent: float | None = None
    kafka_lag: float | None = None
    latency_p95_seconds: float | None = None
    # Error fraction in [0, 1], not a percentage or raw errors/second.
    error_rate: float | None = None
    predicted_traffic: float | None = None

    def __post_init__(self) -> None:
        if not _finite_nonnegative(self.timestamp):
            raise ValueError("timestamp must be a nonnegative finite epoch timestamp")

    def invalid_fields(self) -> list[str]:
        fields = []
        for name in ("incoming_rate", "throughput", "cpu_percent", "ram_percent",
                     "kafka_lag", "latency_p95_seconds", "error_rate"):
            value = getattr(self, name)
            if not _finite_nonnegative(value):
                fields.append(name)
            elif name in {"cpu_percent", "ram_percent"} and value > 100:
                fields.append(name)
            elif name == "error_rate" and value > 1:
                fields.append(name)
        return fields


@dataclass(frozen=True)
class Decision:
    decision_id: int
    timestamp: float
    current_limit: float
    new_limit: float
    action: str
    reason: str
    decision_latency_seconds: float
    episode_id: int | None
    observation: dict[str, Any] | None

    @property
    def changed(self) -> bool:
        return self.new_limit != self.current_limit

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Episode:
    episode_id: int
    t0: float
    triggers: list[str]
    t1: float | None = None
    t2: float | None = None
    t3: float | None = None
    resolved_without_adjustment_at: float | None = None

    def report(self) -> dict[str, Any]:
        result = asdict(self)
        for name, left, right in (
            ("detection_latency_seconds", self.t0, self.t1),
            ("adjustment_latency_seconds", self.t1, self.t2),
            ("recovery_latency_seconds", self.t2, self.t3),
            ("total_response_seconds", self.t0, self.t3),
        ):
            result[name] = None if left is None or right is None else right - left
        result["status"] = ("recovered" if self.t3 is not None else
                            "resolved_without_adjustment" if self.resolved_without_adjustment_at is not None
                            else "unresolved")
        result["onset_semantics"] = "first sampled threshold crossing"
        return result


class TrafficController:
    """Observe/decide controller whose limit changes only after acknowledgement.

    Call ``observe`` on every independent telemetry sample, ``decide`` on the
    slower agent cadence, then apply ``new_limit`` to the real admission gate
    and call ``acknowledge``.  Persist decisions, returned action receipts, and
    ``episode_reports``.  A missing T2 or T3 remains null when a run ends.
    """

    def __init__(self, config: ControlConfig):
        self.config = config
        self.current_limit = config.initial_rate
        self.latest: Observation | None = None
        self._safe_since: float | None = None
        self._last_change: float | None = None
        self._last_decision: float | None = None
        self._decision_id = 0
        self._pending: Decision | None = None
        self._episodes: list[_Episode] = []
        self._active: _Episode | None = None

    def _triggers(self, observation: Observation) -> list[str]:
        cfg = self.config
        invalid = observation.invalid_fields()
        return [name for name, threshold in (
            ("cpu_percent", cfg.cpu_high_percent), ("ram_percent", cfg.ram_high_percent),
            ("kafka_lag", cfg.lag_high), ("latency_p95_seconds", cfg.latency_high_seconds),
            ("error_rate", cfg.error_high),
        ) if name not in invalid
            and getattr(observation, name) >= threshold]

    def _safe(self, observation: Observation) -> bool:
        cfg = self.config
        return not observation.invalid_fields() and all(
            getattr(observation, name) <= threshold for name, threshold in (
                ("cpu_percent", cfg.cpu_low_percent), ("ram_percent", cfg.ram_low_percent),
                ("kafka_lag", cfg.lag_low), ("latency_p95_seconds", cfg.latency_low_seconds),
                ("error_rate", cfg.error_low),
            )
        )

    def observe(self, observation: Observation) -> None:
        if self.latest and observation.timestamp <= self.latest.timestamp:
            raise ValueError("telemetry sample timestamps must increase strictly")
        if self.latest and observation.timestamp - self.latest.timestamp > self.config.telemetry_max_age_seconds:
            self._safe_since = None
        self.latest = observation
        triggers = self._triggers(observation)
        if triggers and self._active is None:
            self._active = _Episode(len(self._episodes) + 1, observation.timestamp, triggers)
            self._episodes.append(self._active)
        if self._safe(observation):
            if self._safe_since is None:
                self._safe_since = observation.timestamp
            if (self._active is not None and
                    observation.timestamp - self._safe_since >= self.config.recovery_window_seconds):
                if self._active.t2 is None:
                    if self._pending is not None and self._pending.episode_id == self._active.episode_id:
                        # A command awaiting real actuation cannot already be
                        # classified as an episode with no adjustment.
                        return
                    self._active.resolved_without_adjustment_at = observation.timestamp
                else:
                    self._active.t3 = observation.timestamp
                self._active = None
        else:
            self._safe_since = None

    def decide(self, timestamp: float) -> Decision:
        started = time.perf_counter()
        if not _finite_nonnegative(timestamp):
            raise ValueError("decision timestamp must be nonnegative and finite")
        if self._last_decision is not None and timestamp < self._last_decision:
            raise ValueError("decision timestamps cannot move backwards")
        if self.latest is not None and timestamp < self.latest.timestamp:
            raise ValueError("decision cannot predate its telemetry sample")
        self._last_decision = timestamp
        self._decision_id += 1
        cfg, observation = self.config, self.latest
        new_limit, reason = self.current_limit, "hysteresis_hold"
        triggers = self._triggers(observation) if observation else []
        if not cfg.enabled:
            reason = "fixed_limit_baseline"
        elif self._pending is not None:
            reason = "awaiting_actuation"
        else:
            if self._active and triggers and self._active.t1 is None:
                self._active.t1 = timestamp
            invalid = observation.invalid_fields() if observation else ["all"]
            stale = observation is not None and timestamp - observation.timestamp > cfg.telemetry_max_age_seconds
            if invalid or stale:
                new_limit = cfg.min_rate
                reason = "telemetry_stale" if stale else "telemetry_missing_or_invalid:" + ",".join(invalid)
                self._safe_since = None
            elif self._last_change is not None and timestamp - self._last_change < cfg.cooldown_seconds:
                reason = "cooldown"
            elif triggers:
                new_limit = max(cfg.min_rate, self.current_limit * cfg.decrease_factor)
                reason = "congestion:" + ",".join(triggers)
            elif (self._safe_since is not None and
                  observation.timestamp - self._safe_since >= cfg.recovery_window_seconds):
                if observation.incoming_rate > self.current_limit:
                    new_limit = min(cfg.max_rate, self.current_limit + cfg.increase_step)
                    reason = "safe_additive_increase"
                else:
                    reason = "offered_demand_within_limit"
        action = "decrease" if new_limit < self.current_limit else "increase" if new_limit > self.current_limit else "hold"
        snapshot = asdict(observation) if observation else None
        if snapshot is not None:
            for name in observation.invalid_fields():
                snapshot[name] = None
            if not _finite_nonnegative(snapshot["predicted_traffic"]):
                snapshot["predicted_traffic"] = None
        decision = Decision(self._decision_id, timestamp, self.current_limit, new_limit, action, reason,
                            max(0.0, time.perf_counter() - started),
                            self._active.episode_id if self._active else None,
                            snapshot)
        if decision.changed:
            self._pending = decision
        return decision

    def acknowledge(self, decision: Decision, timestamp: float, applied_limit: float | None = None) -> dict[str, Any]:
        """Record a gate change only AFTER the caller has actually applied it."""
        if self._pending != decision or not decision.changed:
            raise ValueError("only the pending changed decision can be acknowledged")
        if not _finite_nonnegative(timestamp) or timestamp < decision.timestamp:
            raise ValueError("actuation cannot predate the decision")
        if self.latest is not None and timestamp < self.latest.timestamp:
            raise ValueError("actuation cannot predate the latest observed sample")
        value = decision.new_limit if applied_limit is None else applied_limit
        if (not _finite_nonnegative(value) or not self.config.min_rate <= value <= self.config.max_rate
                or not math.isclose(value, decision.new_limit, rel_tol=1e-9, abs_tol=1e-9)):
            raise ValueError("applied limit must match the bounded requested limit")
        self.current_limit = value
        self._last_change = timestamp
        self._pending = None
        if decision.episode_id is not None and decision.action == "decrease":
            episode = self._episodes[decision.episode_id - 1]
            if episode.t1 is not None and episode.t2 is None:
                episode.t2 = timestamp
                # Count recovery only after actuation, even if safe samples
                # happened while the command was waiting to be applied.
                self._safe_since = None
        return {"decision_id": decision.decision_id, "timestamp": timestamp,
                "old_limit": decision.current_limit, "new_limit": value,
                "reason": decision.reason, "episode_id": decision.episode_id,
                "actuation_latency_seconds": timestamp - decision.timestamp}

    def episode_reports(self) -> list[dict[str, Any]]:
        return [episode.report() for episode in self._episodes]
