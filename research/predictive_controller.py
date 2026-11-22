"""Fail-closed forecast overlay for isolated predictive-control research.

This module is deliberately outside the production controller.  It can only
place a lower ceiling over an existing reactive admission limit; a forecast can
never raise that limit.  Construction requires a readiness receipt produced by
``research.predictive_control_experiment`` and the exact artifact and runtime
fingerprints used by the caller.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import Any, Mapping


READY_STATUS = "ready_for_isolated_predictive_control_trial"


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive(value: Any) -> bool:
    return _finite(value) and value > 0


def _sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value.lower()))


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _epoch(value: Any) -> float | None:
    if _finite(value) and value >= 0:
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        result = stamp.timestamp()
        return result if math.isfinite(result) and result >= 0 else None
    except (OverflowError, ValueError):
        return None


def require_trial_readiness(readiness: Mapping[str, Any], *, artifact_sha256: str,
                            resource_fingerprint: str) -> dict[str, Any]:
    """Validate authorization for an isolated trial, never production use."""
    if not isinstance(readiness, Mapping):
        raise ValueError("predictive-control readiness must be an object")
    blockers = readiness.get("blockers")
    if readiness.get("status") != READY_STATUS or blockers != []:
        detail = ",".join(str(item) for item in blockers or [readiness.get("status", "unknown")])
        raise ValueError("predictive-control trial is blocked: " + detail)
    if readiness.get("production_controller_integrated") is not False:
        raise ValueError("readiness receipt must not authorize production integration")

    forecast = readiness.get("forecast")
    capacity = readiness.get("capacity")
    if not isinstance(forecast, Mapping) or not isinstance(capacity, Mapping):
        raise ValueError("readiness receipt is missing forecast or capacity evidence")
    if not _sha256(artifact_sha256) or artifact_sha256 != forecast.get("artifact_sha256"):
        raise ValueError("forecast artifact hash does not match readiness")
    if not _sha256(resource_fingerprint) or resource_fingerprint != capacity.get("resource_fingerprint"):
        raise ValueError("resource fingerprint does not match readiness")
    if capacity.get("status") not in {"calibration_complete", "completed"}:
        raise ValueError("capacity calibration is not complete")

    overload_rate = capacity.get("overload_rate")
    horizon_seconds = forecast.get("horizon_seconds")
    support = forecast.get("training_support")
    if not _positive(overload_rate) or not _positive(horizon_seconds) or not isinstance(support, Mapping):
        raise ValueError("readiness receipt lacks a valid overload rate, horizon, or training support")
    if (not _finite(support.get("min")) or not _finite(support.get("max"))
            or support["min"] < 0 or support["min"] > support["max"]
            or support["max"] < overload_rate):
        raise ValueError("forecast training support does not cover the measured overload rate")
    split = support.get("split")
    if (not _sha256(support.get("sha256")) or not isinstance(split, Mapping)
            or not _sha256(split.get("sha256"))):
        raise ValueError("forecast support or split provenance hash is missing")
    if not _sha256(capacity.get("evidence_sha256")):
        raise ValueError("capacity evidence hash is missing or invalid")
    return {
        "artifact_sha256": artifact_sha256,
        "resource_fingerprint": resource_fingerprint,
        "capacity_evidence_sha256": capacity["evidence_sha256"],
        "readiness_sha256": _canonical_sha256(readiness),
        "overload_rate": float(overload_rate),
        "horizon_seconds": float(horizon_seconds),
        "support_min": float(support["min"]),
        "support_max": float(support["max"]),
    }


@dataclass(frozen=True)
class PredictiveOverlayConfig:
    min_rate: float = 1.0
    max_rate: float = 100.0
    reserve_fraction: float = .8
    trigger_fraction: float = 1.0
    release_fraction: float = .7
    increase_step: float = 5.0
    cooldown_seconds: float = 10.0
    release_samples: int = 3
    max_prediction_age_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not 0 < self.min_rate <= self.max_rate <= 10_000:
            raise ValueError("overlay rates must satisfy 0 < min_rate <= max_rate <= 10000")
        if not _finite(self.reserve_fraction) or not 0 < self.reserve_fraction < 1:
            raise ValueError("reserve_fraction must be in (0, 1)")
        if (not _finite(self.release_fraction) or not _finite(self.trigger_fraction)
                or not 0 < self.release_fraction < 1 <= self.trigger_fraction):
            raise ValueError("release_fraction must be in (0, 1) and trigger_fraction at least 1")
        if not _positive(self.increase_step) or not _finite(self.cooldown_seconds) or self.cooldown_seconds < 0:
            raise ValueError("increase_step must be positive and cooldown_seconds nonnegative")
        if type(self.release_samples) is not int or self.release_samples < 1:
            raise ValueError("release_samples must be a positive integer")
        if not _positive(self.max_prediction_age_seconds):
            raise ValueError("max_prediction_age_seconds must be positive")


@dataclass(frozen=True)
class PredictiveOverlayDecision:
    schema_version: int
    decision_id: int
    decision_timestamp: float
    forecast_observation_timestamp: float | None
    forecast_target_timestamp: float | None
    predicted_traffic: float | None
    overload_rate: float
    reactive_limit: float
    previous_predictive_cap: float
    predictive_cap: float
    final_limit: float
    acted: bool
    prediction_valid: bool
    reason: str
    artifact_sha256: str
    capacity_evidence_sha256: str
    readiness_sha256: str
    resource_fingerprint: str
    production_controller_integrated: bool = False
    evidence_level: str = "experimental_decision_receipt_not_outcome_evidence"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PredictiveAdmissionOverlay:
    """A deterministic ceiling over a separately operated reactive controller."""

    def __init__(self, readiness: Mapping[str, Any], config: PredictiveOverlayConfig | None = None,
                 *, artifact_sha256: str, resource_fingerprint: str):
        self.config = config or PredictiveOverlayConfig()
        self.identity = require_trial_readiness(
            readiness, artifact_sha256=artifact_sha256,
            resource_fingerprint=resource_fingerprint,
        )
        self.current_cap = self.config.max_rate
        self._last_timestamp: float | None = None
        self._last_change: float | None = None
        self._release_count = 0
        self._decision_id = 0

    def _prediction(self, prediction: Mapping[str, Any] | None, timestamp: float) -> tuple[bool, str, float | None, float | None, float | None]:
        if not isinstance(prediction, Mapping):
            return False, "forecast_missing", None, None, None
        if prediction.get("reason") != "validated_forecast":
            return False, "forecast_" + str(prediction.get("reason") or "unavailable"), None, None, None
        value = prediction.get("predicted_traffic")
        observed_at = _epoch(prediction.get("observation_timestamp"))
        if not _finite(value) or value < 0:
            return False, "forecast_nonfinite", None, observed_at, None
        if observed_at is None or observed_at < 0 or observed_at > timestamp:
            return False, "forecast_timestamp_invalid", float(value), observed_at, None
        target_at = observed_at + self.identity["horizon_seconds"]
        if timestamp - observed_at > self.config.max_prediction_age_seconds:
            return False, "forecast_stale", float(value), observed_at, target_at
        if target_at <= timestamp:
            return False, "forecast_target_not_future", float(value), observed_at, target_at
        if not self.identity["support_min"] <= value <= self.identity["support_max"]:
            return False, "forecast_outside_training_support", float(value), observed_at, target_at
        return True, "validated_forecast", float(value), observed_at, target_at

    def decide(self, timestamp: float, reactive_limit: float,
               prediction: Mapping[str, Any] | None, *, telemetry_safe: bool) -> PredictiveOverlayDecision:
        if not _finite(timestamp) or timestamp < 0:
            raise ValueError("decision timestamp must be finite and nonnegative")
        timestamp = float(timestamp)
        if self._last_timestamp is not None and timestamp < self._last_timestamp:
            raise ValueError("decision timestamps cannot move backwards")
        if not _positive(reactive_limit) or reactive_limit > self.config.max_rate:
            raise ValueError("reactive limit must be positive and within the configured maximum")
        if not isinstance(telemetry_safe, bool):
            raise ValueError("telemetry_safe must be boolean")
        self._last_timestamp = timestamp
        self._decision_id += 1
        previous = self.current_cap
        valid, reason, predicted, observed_at, target_at = self._prediction(prediction, timestamp)

        if not valid:
            self._release_count = 0
            reason = "reactive_fallback_hold:" + reason
        else:
            overload = self.identity["overload_rate"]
            if predicted >= overload * self.config.trigger_fraction:
                self._release_count = 0
                target_cap = max(self.config.min_rate, min(self.config.max_rate,
                                                           overload * self.config.reserve_fraction))
                self.current_cap = min(self.current_cap, target_cap)
                reason = "forecast_preemptive_cap" if self.current_cap < previous else "forecast_cap_hold"
            elif predicted <= overload * self.config.release_fraction and telemetry_safe:
                self._release_count += 1
                cooling = self._last_change is not None and timestamp - self._last_change < self.config.cooldown_seconds
                if self._release_count >= self.config.release_samples and not cooling:
                    self.current_cap = min(self.config.max_rate, self.current_cap + self.config.increase_step)
                    self._release_count = 0
                    reason = "forecast_safe_additive_release" if self.current_cap > previous else "forecast_cap_fully_released"
                else:
                    reason = "forecast_release_wait"
            else:
                self._release_count = 0
                reason = "forecast_hysteresis_hold"

        acted = not math.isclose(previous, self.current_cap, rel_tol=1e-12, abs_tol=1e-12)
        if acted:
            self._last_change = timestamp
        final_limit = min(float(reactive_limit), self.current_cap)
        return PredictiveOverlayDecision(
            schema_version=1,
            decision_id=self._decision_id,
            decision_timestamp=timestamp,
            forecast_observation_timestamp=observed_at,
            forecast_target_timestamp=target_at,
            predicted_traffic=predicted,
            overload_rate=self.identity["overload_rate"],
            reactive_limit=float(reactive_limit),
            previous_predictive_cap=previous,
            predictive_cap=self.current_cap,
            final_limit=final_limit,
            acted=acted,
            prediction_valid=valid,
            reason=reason,
            artifact_sha256=self.identity["artifact_sha256"],
            capacity_evidence_sha256=self.identity["capacity_evidence_sha256"],
            readiness_sha256=self.identity["readiness_sha256"],
            resource_fingerprint=self.identity["resource_fingerprint"],
        )
