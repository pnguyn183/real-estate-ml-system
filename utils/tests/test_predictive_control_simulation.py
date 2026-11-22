from copy import deepcopy

import pytest

from agents.traffic_control import ControlConfig
from research.predictive_controller import PredictiveOverlayConfig
from research.predictive_control_simulation import (
    ARMS,
    QueueSimulationConfig,
    build_live_trial_plan,
    run_paired_simulation,
    validate_live_trial_results,
)


ARTIFACT = "b" * 64
RESOURCE = "c" * 64


def readiness(*, blocked=False):
    return {
        "schema_version": 1,
        "status": "blocked_before_trial" if blocked else "ready_for_isolated_predictive_control_trial",
        "production_controller_integrated": False,
        "blockers": ["capacity_calibration_not_completed"] if blocked else [],
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


def reactive_config():
    return ControlConfig(
        enabled=True,
        min_rate=1,
        max_rate=100,
        initial_rate=100,
        cooldown_seconds=0,
        recovery_window_seconds=1,
        lag_low=9_000,
        lag_high=10_000,
        latency_low_seconds=900,
        latency_high_seconds=1_000,
    )


def trace():
    return [
        {"timestamp": 100 + index, "offered_rate": 90, "predicted_traffic": 85}
        for index in range(6)
    ]


def simulation():
    return run_paired_simulation(
        trace(),
        readiness=readiness(),
        artifact_sha256=ARTIFACT,
        resource_fingerprint=RESOURCE,
        reactive_config=reactive_config(),
        overlay_config=PredictiveOverlayConfig(min_rate=1, max_rate=100),
        simulation_config=QueueSimulationConfig(interval_seconds=1, service_rate=50),
    )


def test_paired_simulation_is_deterministic_conservative_and_explicitly_noncausal():
    first, second = simulation(), simulation()
    assert first == second
    assert first["evidence_level"] == "simulation_only_not_causal"
    assert first["production_controller_integrated"] is False
    assert first["improvement_claim_allowed"] is False
    assert "p95_latency" not in first["arms"][ARMS[1]]["simulated_outcomes"]
    assert first["arms"][ARMS[1]]["simulated_outcomes"]["peak_processing_backlog"] < first["arms"][ARMS[0]]["simulated_outcomes"]["peak_processing_backlog"]
    assert all(step["final_limit"] <= step["reactive_limit"]
               for step in first["arms"][ARMS[1]]["steps"])


def test_simulation_preserves_admission_and_service_accounting():
    result = simulation()
    for arm in ARMS:
        accounting = result["arms"][arm]["accounting"]
        assert accounting["offered"] == pytest.approx(
            accounting["admitted"] + accounting["rejected"] + accounting["deferred"])
        assert accounting["admitted"] == pytest.approx(
            accounting["committed"] + accounting["processing_backlog"])


def test_blocked_readiness_refuses_simulation_and_live_plan():
    kwargs = dict(
        readiness=readiness(blocked=True), artifact_sha256=ARTIFACT,
        resource_fingerprint=RESOURCE,
    )
    with pytest.raises(ValueError, match="blocked"):
        run_paired_simulation(
            trace(), reactive_config=reactive_config(),
            overlay_config=PredictiveOverlayConfig(),
            simulation_config=QueueSimulationConfig(interval_seconds=1, service_rate=50),
            **kwargs,
        )
    with pytest.raises(ValueError, match="blocked"):
        build_live_trial_plan(
            pairs=3, workload_fingerprint="workload", seed=7,
            initial_state_fingerprint="clean", duration_seconds=60,
            **kwargs,
        )


def plan():
    return build_live_trial_plan(
        readiness(), pairs=3, workload_fingerprint="workload-v1", seed=7,
        resource_fingerprint=RESOURCE, initial_state_fingerprint="clean-v1",
        duration_seconds=60, artifact_sha256=ARTIFACT,
    )


def completed_runs(selected_plan):
    rows = []
    for wanted in selected_plan["runs"]:
        predictive = wanted["arm"] == ARMS[1]
        row = {
            **wanted,
            "status": "completed",
            "accounting": {
                "offered": 100, "admitted": 90, "committed": 90,
                "deferred": 10, "rejected": 0,
            },
            "outcomes": {
                "p95_latency": 8 if predictive else 10,
                "p99_latency": 12 if predictive else 15,
                "error_rate": 0,
                "peak_lag": 80 if predictive else 100,
                "recovery_time": 16 if predictive else 20,
                "served_fraction": .9,
            },
            "predictive_actions": ([{
                "acted": True,
                "prediction_valid": True,
                "artifact_sha256": ARTIFACT,
                "capacity_evidence_sha256": selected_plan["capacity_evidence_sha256"],
                "readiness_sha256": selected_plan["readiness_sha256"],
                "resource_fingerprint": RESOURCE,
                "decision_timestamp": 10,
                "forecast_target_timestamp": 310,
                "response_observed_at": 20,
                "production_controller_integrated": False,
            }] if predictive else []),
        }
        rows.append(row)
    return rows


def test_live_plan_alternates_three_pairs_and_validator_only_reports_observed_change():
    selected = plan()
    assert [run["arm"] for run in selected["runs"]] == [
        ARMS[0], ARMS[1], ARMS[1], ARMS[0], ARMS[0], ARMS[1],
    ]
    result = validate_live_trial_results(selected, completed_runs(selected))
    assert result["status"] == "formal_validation_passed"
    assert result["production_controller_integrated"] is False
    assert result["aggregate"]["p95_latency"]["directional_change_percent"] == 20
    # A zero baseline is never turned into a misleading percentage.
    assert result["aggregate"]["error_rate"]["directional_change_percent"] is None
    assert "does not by itself establish" in result["interpretation"]


@pytest.mark.parametrize("mutation,match", [
    (lambda rows: rows[0].update(seed=999), "condition mismatch"),
    (lambda rows: rows[1].update(predictive_actions=[]), "lacks an attributed"),
    (lambda rows: rows[1]["predictive_actions"][0].update(response_observed_at=5), "temporal ordering"),
    (lambda rows: rows[0]["accounting"].update(committed=80), "accounting"),
    (lambda rows: (
        rows[1]["accounting"].update(offered=110, admitted=100, committed=99),
        rows[1]["outcomes"].update(served_fraction=.9),
    ), "equivalent offered demand"),
])
def test_live_validator_rejects_unmatched_incomplete_or_unattributed_runs(mutation, match):
    selected = plan()
    rows = completed_runs(selected)
    mutation(rows)
    with pytest.raises(ValueError, match=match):
        validate_live_trial_results(selected, rows)


def test_live_plan_requires_three_pairs():
    with pytest.raises(ValueError, match="three"):
        build_live_trial_plan(
            readiness(), pairs=2, workload_fingerprint="workload", seed=1,
            resource_fingerprint=RESOURCE, initial_state_fingerprint="clean",
            duration_seconds=30, artifact_sha256=ARTIFACT,
        )
