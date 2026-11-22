"""Deterministic, simulation-only harness for the predictive admission overlay.

The queue model in this module is not a model of production latency, errors or
availability.  Its outputs can verify accounting, bounds and decision timing;
they cannot establish a live improvement.  A separate validator prepares and
checks future isolated AB/BA trials without executing or integrating them.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

from agents.traffic_control import ControlConfig, Observation, TrafficController
from research.predictive_controller import (
    PredictiveAdmissionOverlay,
    PredictiveOverlayConfig,
    require_trial_readiness,
)


ARMS = ("reactive_controller", "experimental_forecast_controller")
LOWER_IS_BETTER = ("p95_latency", "p99_latency", "error_rate", "peak_lag", "recovery_time")
HIGHER_IS_BETTER = ("served_fraction",)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class QueueSimulationConfig:
    interval_seconds: float
    service_rate: float
    max_pending_records: float = 100_000.0
    initial_pending_records: float = 0.0
    initial_processing_backlog: float = 0.0

    def __post_init__(self) -> None:
        if not _finite(self.interval_seconds) or self.interval_seconds <= 0:
            raise ValueError("interval_seconds must be finite and positive")
        if not _finite(self.service_rate) or self.service_rate <= 0:
            raise ValueError("service_rate must be finite and positive")
        if not _finite(self.max_pending_records) or self.max_pending_records <= 0:
            raise ValueError("max_pending_records must be finite and positive")
        for name in ("initial_pending_records", "initial_processing_backlog"):
            value = getattr(self, name)
            if not _finite(value) or value < 0:
                raise ValueError(name + " must be finite and nonnegative")
        if self.initial_pending_records > self.max_pending_records:
            raise ValueError("initial pending records exceed the queue bound")


def _validated_trace(trace: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in trace]
    if not rows:
        raise ValueError("simulation trace is empty")
    previous = None
    for row in rows:
        timestamp, offered = row.get("timestamp"), row.get("offered_rate")
        if not _finite(timestamp) or timestamp < 0 or not _finite(offered) or offered < 0:
            raise ValueError("trace timestamps and offered rates must be finite and nonnegative")
        if previous is not None and timestamp <= previous:
            raise ValueError("trace timestamps must increase strictly")
        previous = timestamp
    return rows


def _prediction(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    supplied = row.get("prediction")
    if isinstance(supplied, Mapping):
        return supplied
    if "predicted_traffic" not in row:
        return None
    return {
        "predicted_traffic": row.get("predicted_traffic"),
        "reason": row.get("prediction_reason", "validated_forecast"),
        "observation_timestamp": row.get("forecast_observation_timestamp", row.get("timestamp")),
    }


def _simulate_arm(trace: list[dict[str, Any]], arm: str, reactive_config: ControlConfig,
                  simulation: QueueSimulationConfig, overlay_config: PredictiveOverlayConfig,
                  readiness: Mapping[str, Any], artifact_sha256: str,
                  resource_fingerprint: str) -> dict[str, Any]:
    if arm not in ARMS:
        raise ValueError("unknown simulation arm")
    controller = TrafficController(reactive_config)
    overlay = (PredictiveAdmissionOverlay(
        readiness, overlay_config, artifact_sha256=artifact_sha256,
        resource_fingerprint=resource_fingerprint,
    ) if arm == ARMS[1] else None)
    pending = simulation.initial_pending_records
    processing = simulation.initial_processing_backlog
    totals = {"offered": 0.0, "admitted": 0.0, "committed": 0.0, "rejected": 0.0}
    peak_pending, peak_processing = pending, processing
    previous_committed = 0.0
    steps: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    for row in trace:
        timestamp = float(row["timestamp"])
        offered_rate = float(row["offered_rate"])
        queue_delay = processing / simulation.service_rate
        observation = Observation(
            timestamp=timestamp,
            incoming_rate=offered_rate,
            throughput=previous_committed / simulation.interval_seconds,
            cpu_percent=0.0,
            ram_percent=0.0,
            kafka_lag=processing,
            latency_p95_seconds=queue_delay,
            error_rate=0.0,
        )
        controller.observe(observation)
        reactive = controller.decide(timestamp)
        if reactive.changed:
            controller.acknowledge(reactive, timestamp)
        reactive_limit = controller.current_limit
        if overlay is None:
            final_limit = reactive_limit
            overlay_receipt = None
        else:
            telemetry_safe = (processing <= reactive_config.lag_low
                              and queue_delay <= reactive_config.latency_low_seconds)
            decision = overlay.decide(timestamp, reactive_limit, _prediction(row),
                                      telemetry_safe=telemetry_safe)
            overlay_receipt = decision.as_dict()
            final_limit = decision.final_limit
            actions.append(overlay_receipt)

        offered = offered_rate * simulation.interval_seconds
        totals["offered"] += offered
        pending += offered
        rejected = max(0.0, pending - simulation.max_pending_records)
        pending -= rejected
        admitted = min(pending, final_limit * simulation.interval_seconds)
        pending -= admitted
        processing += admitted
        committed = min(processing, simulation.service_rate * simulation.interval_seconds)
        processing -= committed
        previous_committed = committed
        totals["admitted"] += admitted
        totals["committed"] += committed
        totals["rejected"] += rejected
        peak_pending = max(peak_pending, pending)
        peak_processing = max(peak_processing, processing)
        steps.append({
            "timestamp": timestamp,
            "offered_rate": offered_rate,
            "reactive_limit": reactive_limit,
            "final_limit": final_limit,
            "offered": offered,
            "admitted": admitted,
            "committed": committed,
            "rejected": rejected,
            "deferred_end": pending,
            "processing_backlog_end": processing,
            "simulated_queue_delay_seconds": queue_delay,
            "overlay_decision": overlay_receipt,
        })

    accounting = {
        **totals,
        "deferred": pending,
        "processing_backlog": processing,
        "initial_pending": simulation.initial_pending_records,
        "initial_processing_backlog": simulation.initial_processing_backlog,
        "semantics": {
            "deferred": "records still waiting for simulated admission at trace end",
            "processing_backlog": "admitted records not yet served by the deterministic queue model",
            "committed": "simulated service completions, not Kafka commits or MongoDB persistence",
        },
    }
    if not math.isclose(totals["offered"] + simulation.initial_pending_records,
                        totals["admitted"] + totals["rejected"] + pending,
                        rel_tol=1e-9, abs_tol=1e-7):
        raise AssertionError("simulation admission accounting is not conserved")
    expected_processing = (simulation.initial_processing_backlog + totals["admitted"]
                           - totals["committed"])
    if not math.isclose(processing, expected_processing, rel_tol=1e-9, abs_tol=1e-7):
        raise AssertionError("simulation service accounting is not conserved")
    return {
        "arm": arm,
        "status": "completed_simulation",
        "accounting": accounting,
        "simulated_outcomes": {
            "peak_deferred_records": peak_pending,
            "peak_processing_backlog": peak_processing,
            "final_processing_backlog": processing,
            "served_fraction": totals["committed"] / (
                totals["offered"] + simulation.initial_pending_records
                + simulation.initial_processing_backlog
            ) if (totals["offered"] + simulation.initial_pending_records
                  + simulation.initial_processing_backlog) else None,
        },
        "predictive_actions": actions,
        "steps": steps,
    }


def run_paired_simulation(trace: Iterable[Mapping[str, Any]], *, readiness: Mapping[str, Any],
                          artifact_sha256: str, resource_fingerprint: str,
                          reactive_config: ControlConfig,
                          overlay_config: PredictiveOverlayConfig,
                          simulation_config: QueueSimulationConfig) -> dict[str, Any]:
    """Run both policies through the same explicit queue model and input trace."""
    identity = require_trial_readiness(
        readiness, artifact_sha256=artifact_sha256,
        resource_fingerprint=resource_fingerprint,
    )
    if not reactive_config.enabled:
        raise ValueError("simulation requires the reactive controller to be enabled")
    if (reactive_config.min_rate != overlay_config.min_rate
            or reactive_config.max_rate != overlay_config.max_rate):
        raise ValueError("reactive and overlay bounds must match")
    rows = _validated_trace(trace)
    arms = {
        arm: _simulate_arm(rows, arm, reactive_config, simulation_config, overlay_config,
                           readiness, artifact_sha256, resource_fingerprint)
        for arm in ARMS
    }
    reactive = arms[ARMS[0]]["simulated_outcomes"]
    predictive = arms[ARMS[1]]["simulated_outcomes"]
    return {
        "schema_version": 1,
        "status": "completed_simulation",
        "evidence_level": "simulation_only_not_causal",
        "production_controller_integrated": False,
        "improvement_claim_allowed": False,
        "forecast_artifact_sha256": identity["artifact_sha256"],
        "capacity_evidence_sha256": identity["capacity_evidence_sha256"],
        "resource_fingerprint": resource_fingerprint,
        "trace_sha256": _fingerprint(rows),
        "assumptions": {
            "queue_model": "deterministic bounded admission queue and constant-rate service",
            "latency": "queue-delay proxy is controller input only; no p95/p99 latency is inferred",
            "errors": "no live error or availability outcome is simulated",
            "simulation": asdict(simulation_config),
            "reactive_control": asdict(reactive_config),
            "predictive_overlay": asdict(overlay_config),
        },
        "arms": arms,
        "comparison": {
            key: predictive[key] - reactive[key]
            for key in reactive
            if _finite(reactive[key]) and _finite(predictive[key])
        },
        "conclusion": "Algorithmic replay only; a valid isolated AB/BA trial is still required.",
    }


def build_live_trial_plan(readiness: Mapping[str, Any], *, pairs: int,
                          workload_fingerprint: str, seed: int,
                          resource_fingerprint: str, initial_state_fingerprint: str,
                          duration_seconds: float, artifact_sha256: str) -> dict[str, Any]:
    """Prepare, but never execute, an alternating isolated live trial plan."""
    identity = require_trial_readiness(
        readiness, artifact_sha256=artifact_sha256,
        resource_fingerprint=resource_fingerprint,
    )
    if type(pairs) is not int or pairs < 3:
        raise ValueError("at least three AB/BA pairs are required")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if not _finite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be finite and positive")
    for name, value in (("workload_fingerprint", workload_fingerprint),
                        ("initial_state_fingerprint", initial_state_fingerprint)):
        if not _nonempty(value):
            raise ValueError(name + " is required")
    runs = []
    sequence = 0
    for pair_id in range(1, pairs + 1):
        order = ARMS if pair_id % 2 else tuple(reversed(ARMS))
        for arm in order:
            sequence += 1
            runs.append({
                "sequence": sequence,
                "pair_id": pair_id,
                "arm": arm,
                "workload_fingerprint": workload_fingerprint,
                "seed": seed + pair_id - 1,
                "resource_fingerprint": resource_fingerprint,
                "initial_state_fingerprint": initial_state_fingerprint,
                "duration_seconds": float(duration_seconds),
                "forecast_artifact_sha256": artifact_sha256 if arm == ARMS[1] else None,
            })
    plan = {
        "schema_version": 1,
        "status": "planned_not_executed",
        "minimum_pairs": 3,
        "pairs": pairs,
        "order": "AB/BA alternating",
        "production_controller_integrated": False,
        "forecast_artifact_sha256": artifact_sha256,
        "capacity_evidence_sha256": identity["capacity_evidence_sha256"],
        "readiness_sha256": identity["readiness_sha256"],
        "runs": runs,
    }
    return {**plan, "plan_sha256": _fingerprint(plan)}


def _valid_accounting(accounting: Any) -> bool:
    if not isinstance(accounting, Mapping):
        return False
    values = {key: accounting.get(key) for key in ("offered", "admitted", "committed", "deferred", "rejected")}
    if any(not _finite(value) or value < 0 for value in values.values()):
        return False
    return (math.isclose(values["offered"], values["admitted"] + values["deferred"]
                         + values["rejected"],
                         rel_tol=1e-9, abs_tol=1e-7)
            and values["committed"] <= values["admitted"])


def _valid_outcomes(outcomes: Any) -> bool:
    if not isinstance(outcomes, Mapping):
        return False
    if any(not _finite(outcomes.get(key)) or outcomes[key] < 0 for key in LOWER_IS_BETTER):
        return False
    served = outcomes.get("served_fraction")
    return _finite(served) and 0 <= served <= 1


def _directional_change(baseline: float, predictive: float, *, higher_is_better: bool) -> float | None:
    if baseline == 0:
        return None
    numerator = predictive - baseline if higher_is_better else baseline - predictive
    return numerator / baseline * 100


def validate_live_trial_results(plan: Mapping[str, Any], runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Check future live receipts and report differences without claiming causality."""
    if plan.get("status") != "planned_not_executed" or plan.get("production_controller_integrated") is not False:
        raise ValueError("invalid isolated trial plan")
    expected = plan.get("runs")
    observed = [dict(run) for run in runs]
    pairs = plan.get("pairs")
    unsigned_plan = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if plan.get("plan_sha256") != _fingerprint(unsigned_plan):
        raise ValueError("trial plan fingerprint mismatch")
    if (type(pairs) is not int or pairs < 3 or not isinstance(expected, list)
            or len(expected) != 2 * pairs or len(observed) != len(expected)):
        raise ValueError("trial results do not contain every planned AB/BA run")
    expected_order = []
    for pair_id in range(1, pairs + 1):
        order = ARMS if pair_id % 2 else tuple(reversed(ARMS))
        expected_order.extend((pair_id, arm) for arm in order)
    if [(row.get("pair_id"), row.get("arm")) for row in expected] != expected_order:
        raise ValueError("trial plan is not alternating AB/BA")

    by_pair: dict[int, dict[str, Mapping[str, Any]]] = {}
    for wanted, run in zip(expected, observed):
        for key in ("sequence", "pair_id", "arm", "workload_fingerprint", "seed",
                    "resource_fingerprint", "initial_state_fingerprint", "duration_seconds"):
            if run.get(key) != wanted.get(key):
                raise ValueError("trial condition mismatch: " + key)
        if run.get("status") != "completed":
            raise ValueError("all live trial arms must complete")
        if not _valid_accounting(run.get("accounting")):
            raise ValueError("invalid or incomplete offered/admitted/committed/deferred/rejected accounting")
        if not _valid_outcomes(run.get("outcomes")):
            raise ValueError("required live outcomes are missing or invalid")
        accounting, outcomes = run["accounting"], run["outcomes"]
        if accounting["offered"] <= 0 or not math.isclose(
                outcomes["served_fraction"], accounting["committed"] / accounting["offered"],
                rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("served_fraction does not match committed/offered accounting")
        actions = run.get("predictive_actions", [])
        if not isinstance(actions, list):
            raise ValueError("predictive_actions must be a list")
        acted = [action for action in actions if isinstance(action, Mapping) and action.get("acted") is True]
        if run["arm"] == ARMS[1]:
            if run.get("forecast_artifact_sha256") != plan.get("forecast_artifact_sha256") or not acted:
                raise ValueError("predictive arm lacks an attributed real forecast action")
            for action in acted:
                decision_at = action.get("decision_timestamp")
                response_at = action.get("response_observed_at")
                target_at = action.get("forecast_target_timestamp")
                if (action.get("artifact_sha256") != plan.get("forecast_artifact_sha256")
                        or action.get("capacity_evidence_sha256") != plan.get("capacity_evidence_sha256")
                        or action.get("readiness_sha256") != plan.get("readiness_sha256")
                        or action.get("resource_fingerprint") != run.get("resource_fingerprint")
                        or action.get("prediction_valid") is not True
                        or action.get("production_controller_integrated") is not False
                        or not _finite(decision_at) or not _finite(response_at)
                        or not _finite(target_at) or target_at <= decision_at
                        or response_at <= decision_at):
                    raise ValueError("predictive action attribution or temporal ordering is invalid")
        elif acted:
            raise ValueError("reactive arm cannot contain a predictive action")
        by_pair.setdefault(run["pair_id"], {})[run["arm"]] = run

    comparisons = []
    for pair_id in range(1, pairs + 1):
        pair = by_pair.get(pair_id, {})
        if set(pair) != set(ARMS):
            raise ValueError("each pair must contain both arms")
        baseline = pair[ARMS[0]]["outcomes"]
        predictive = pair[ARMS[1]]["outcomes"]
        if not math.isclose(
                pair[ARMS[0]]["accounting"]["offered"],
                pair[ARMS[1]]["accounting"]["offered"],
                rel_tol=1e-9, abs_tol=1e-7):
            raise ValueError("paired arms do not have equivalent offered demand")
        comparisons.append({
            "pair_id": pair_id,
            "directional_change_percent": {
                key: _directional_change(baseline[key], predictive[key],
                                         higher_is_better=key in HIGHER_IS_BETTER)
                for key in (*LOWER_IS_BETTER, *HIGHER_IS_BETTER)
            },
            "raw": {key: {"reactive": baseline[key], "predictive": predictive[key]}
                    for key in (*LOWER_IS_BETTER, *HIGHER_IS_BETTER)},
        })
    aggregate = {}
    for key in (*LOWER_IS_BETTER, *HIGHER_IS_BETTER):
        baseline = mean(by_pair[pair][ARMS[0]]["outcomes"][key] for pair in by_pair)
        predictive = mean(by_pair[pair][ARMS[1]]["outcomes"][key] for pair in by_pair)
        aggregate[key] = {
            "reactive_mean": baseline,
            "predictive_mean": predictive,
            "directional_change_percent": _directional_change(
                baseline, predictive, higher_is_better=key in HIGHER_IS_BETTER),
        }
    return {
        "schema_version": 1,
        "status": "formal_validation_passed",
        "production_controller_integrated": False,
        "plan_sha256": plan.get("plan_sha256"),
        "pairs": comparisons,
        "aggregate": aggregate,
        "interpretation": (
            "Paired observed differences only. Formal validation does not by itself establish "
            "generalization, production safety, or a causal improvement beyond these runs."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True, help="JSON array of timestamp/offered-rate rows")
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    trace = json.loads(args.trace.read_text(encoding="utf-8-sig"))
    readiness = json.loads(args.readiness.read_text(encoding="utf-8-sig"))
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    result = run_paired_simulation(
        trace,
        readiness=readiness,
        artifact_sha256=config["artifact_sha256"],
        resource_fingerprint=config["resource_fingerprint"],
        reactive_config=ControlConfig(**config["reactive_control"]),
        overlay_config=PredictiveOverlayConfig(**config["predictive_overlay"]),
        simulation_config=QueueSimulationConfig(**config["simulation"]),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
