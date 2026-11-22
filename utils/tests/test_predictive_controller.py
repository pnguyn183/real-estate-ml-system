import json

import pytest

from research.predictive_controller import (
    PredictiveAdmissionOverlay,
    PredictiveOverlayConfig,
    require_trial_readiness,
)


ARTIFACT = "a" * 64
RESOURCE = "c" * 64


def readiness(**updates):
    value = {
        "schema_version": 1,
        "status": "ready_for_isolated_predictive_control_trial",
        "production_controller_integrated": False,
        "blockers": [],
        "forecast": {
            "artifact_sha256": ARTIFACT,
            "horizon_seconds": 300,
            "training_support": {
                "min": 0,
                "max": 100,
                "sha256": "d" * 64,
                "split": {"sha256": "e" * 64},
            },
        },
        "capacity": {
            "status": "calibration_complete",
            "overload_rate": 80,
            "resource_fingerprint": RESOURCE,
            "evidence_sha256": "f" * 64,
        },
    }
    value.update(updates)
    return value


def prediction(value, observed_at, reason="validated_forecast"):
    return {"predicted_traffic": value, "observation_timestamp": observed_at, "reason": reason}


def overlay(**changes):
    config = PredictiveOverlayConfig(cooldown_seconds=2, release_samples=2,
                                     increase_step=10, **changes)
    return PredictiveAdmissionOverlay(readiness(), config, artifact_sha256=ARTIFACT,
                                      resource_fingerprint=RESOURCE)


def test_blocked_readiness_and_identity_mismatch_refuse_the_experimental_overlay():
    blocked = readiness(status="blocked_before_trial", blockers=["measured_overload_rate_missing"])
    with pytest.raises(ValueError, match="blocked"):
        PredictiveAdmissionOverlay(blocked, artifact_sha256=ARTIFACT,
                                   resource_fingerprint=RESOURCE)
    with pytest.raises(ValueError, match="artifact hash"):
        PredictiveAdmissionOverlay(readiness(), artifact_sha256="wrong",
                                   resource_fingerprint=RESOURCE)
    with pytest.raises(ValueError, match="resource fingerprint"):
        PredictiveAdmissionOverlay(readiness(), artifact_sha256=ARTIFACT,
                                   resource_fingerprint="other-allocation")


def test_readiness_rechecks_training_support_instead_of_trusting_status():
    receipt = readiness()
    receipt["forecast"]["training_support"]["max"] = 79
    with pytest.raises(ValueError, match="does not cover"):
        require_trial_readiness(receipt, artifact_sha256=ARTIFACT,
                                resource_fingerprint=RESOURCE)


def test_readiness_rechecks_capacity_status_and_provenance_hashes():
    incomplete = readiness()
    incomplete["capacity"]["status"] = "calibration_inconclusive"
    with pytest.raises(ValueError, match="not complete"):
        require_trial_readiness(incomplete, artifact_sha256=ARTIFACT,
                                resource_fingerprint=RESOURCE)

    unbound = readiness()
    unbound["forecast"]["training_support"].pop("sha256")
    with pytest.raises(ValueError, match="provenance"):
        require_trial_readiness(unbound, artifact_sha256=ARTIFACT,
                                resource_fingerprint=RESOURCE)


def test_forecast_can_only_add_a_lower_cap_over_the_reactive_limit():
    policy = overlay()
    decision = policy.decide(100, 100, prediction(85, 100), telemetry_safe=False)
    assert decision.acted and decision.prediction_valid
    assert decision.predictive_cap == 64
    assert decision.final_limit == 64
    assert decision.final_limit <= decision.reactive_limit
    assert decision.production_controller_integrated is False
    assert decision.artifact_sha256 == ARTIFACT
    assert decision.forecast_target_timestamp == 400
    json.dumps(decision.as_dict(), allow_nan=False)

    # A lower reactive safety decision always wins over the forecast overlay.
    lower_reactive = policy.decide(101, 50, prediction(85, 101), telemetry_safe=False)
    assert lower_reactive.final_limit == 50
    assert lower_reactive.final_limit <= lower_reactive.reactive_limit


@pytest.mark.parametrize("forecast,reason", [
    (None, "forecast_missing"),
    (prediction(85, 80), "forecast_stale"),
    (prediction(101, 100), "forecast_outside_training_support"),
    (prediction(85, 100, "telemetry_unavailable"), "forecast_telemetry_unavailable"),
])
def test_invalid_forecast_falls_back_without_releasing_an_active_cap(forecast, reason):
    policy = overlay()
    policy.decide(90, 100, prediction(85, 90), telemetry_safe=False)
    decision = policy.decide(100, 100, forecast, telemetry_safe=True)
    assert not decision.prediction_valid
    assert not decision.acted
    assert decision.predictive_cap == decision.final_limit == 64
    assert decision.reason == "reactive_fallback_hold:" + reason


def test_release_is_consecutive_additive_and_subject_to_cooldown():
    policy = overlay()
    policy.decide(100, 100, prediction(85, 100), telemetry_safe=False)
    first = policy.decide(101, 100, prediction(20, 101), telemetry_safe=True)
    second = policy.decide(102, 100, prediction(20, 102), telemetry_safe=True)
    assert first.reason == "forecast_release_wait" and not first.acted
    assert second.reason == "forecast_safe_additive_release" and second.acted
    assert second.predictive_cap == second.final_limit == 74

    # An unsafe telemetry sample breaks the consecutive release sequence.
    held = policy.decide(103, 100, prediction(20, 103), telemetry_safe=False)
    assert held.reason == "forecast_hysteresis_hold" and held.predictive_cap == 74


def test_same_inputs_produce_identical_receipts_and_time_cannot_move_backwards():
    left, right = overlay(), overlay()
    inputs = [
        (100, 100, prediction(85, 100), False),
        (101, 100, prediction(20, 101), True),
        (102, 100, prediction(20, 102), True),
    ]
    left_receipts = [left.decide(stamp, limit, forecast, telemetry_safe=safe).as_dict()
                     for stamp, limit, forecast, safe in inputs]
    right_receipts = [right.decide(stamp, limit, forecast, telemetry_safe=safe).as_dict()
                      for stamp, limit, forecast, safe in inputs]
    assert left_receipts == right_receipts
    with pytest.raises(ValueError, match="backwards"):
        left.decide(99, 100, prediction(20, 99), telemetry_safe=True)


@pytest.mark.parametrize("changes", [
    {"reserve_fraction": 1}, {"release_fraction": 1}, {"trigger_fraction": .5},
    {"increase_step": 0}, {"cooldown_seconds": -1}, {"release_samples": 0},
    {"max_prediction_age_seconds": 0}, {"min_rate": 101},
])
def test_overlay_configuration_is_bounded(changes):
    with pytest.raises(ValueError):
        PredictiveOverlayConfig(**changes)
