"""Synthetic analysis-contract fixtures; these are never live experiment evidence."""
from copy import deepcopy
import json

import pytest

from research.llm_report import adaptation_chains, build_report, markdown_report, summarize_run, write_report


def run(mode="llm", pair=1, start=1000):
    samples = []
    for index in range(1, 8):
        recovered = mode == "llm" and index >= 5
        samples.append({"timestamp": start + index * 10, "interval_seconds": 10,
                        "instrumentation_ready": True,
                        "stage": "overload", "offered_rate": 100, "incoming_rate": 50 if recovered else 100,
                        "admission_limit": 50 if recovered else 100,
                        "throughput": 50 if recovered else 60, "kafka_lag": 0 if recovered else index * 10,
                        "latency_p95_seconds": .3 if recovered else index + 2,
                        "error_rate": 0, "pending": 0})
    admitted = 350 if mode == "llm" else 700
    return {"pair_id": pair, "run_id": f"{pair}-{mode}", "mode": mode, "status": "completed",
            "sample_seconds": 10, "load_started_at": start, "load_ended_at": start + 80,
            "training_disabled": True, "config_fingerprint": "schedule+seed", "resource_fingerprint": "limits",
            "initial_rate": 100, "samples": samples,
            "completion_observer": {"errors": [], "max_poll_gap_seconds": .3},
            "actions": [{"timestamp": start + 35, "action_origin": "llm", "status": "applied",
                         "provider": "provider", "model": "actual-model", "decision_id": "decision-1",
                         "decision": {"rate_per_second": 50, "reason": "Measured growing lag", "resources": {}}}]
                        if mode == "llm" else [],
            "accounting": {"offered": 700, "planned": 700, "workload_fingerprint": "same-real-generated-payloads",
                           "scheduler_lateness_p95_seconds": .001, "scheduler_lateness_max_seconds": .01,
                           "errors": [], "rate_read_errors": 0, "admitted": admitted, "deferred": 200,
                           "rejected": 700 - admitted, "acknowledged": admitted, "committed": admitted,
                           "latency_count": admitted,
                           "delivery_failed": 0, "enqueue_failed": 0, "pending": 0,
                           "offered_to_commit_p95_upper_seconds": 15 if mode == "llm" else 30}}


def three_pairs():
    return [run(mode, pair, start=pair * 1000 + offset)
            for pair in range(1, 4)
            for mode, offset in (("baseline", 0 if pair % 2 else 100), ("llm", 100 if pair % 2 else 0))]


def test_three_matched_ab_ba_pairs_with_actual_response_can_demonstrate():
    report = build_report(three_pairs())
    assert report["verdict"] == "demonstrated"
    assert report["eligible_pairs"] == report["demonstrated_pairs"] == 3
    proof = report["pairs"][0]["llm"]
    assert proof["served_fraction"] == .5
    assert proof["accounting"]["rejected"] == 350
    assert proof["adaptation_chains"][0]["recovery_phase"] == "same_offered_overload"
    assert "wait" in " ".join(report["notes"])


@pytest.mark.parametrize("change, expected", [
    (lambda value: value.update(status="guard_stopped"), "llm_not_completed"),
    (lambda value: value.update(resource_fingerprint="different"), "mismatched_resource_fingerprint"),
    (lambda value: value.update(config_fingerprint="different"), "mismatched_config_fingerprint"),
    (lambda value: value.update(training_disabled=False), "llm_training_not_verified_disabled"),
    (lambda value: value["accounting"].update(offered=701), "llm_unaccounted_offers"),
    (lambda value: value["accounting"].update(cancelled_future_offers=10), "llm_incomplete_offered_schedule"),
    (lambda value: value["accounting"].update(committed=300), "llm_unobserved_commits"),
    (lambda value: value["accounting"].update(planned=1000), "llm_incomplete_planned_schedule"),
    (lambda value: value["accounting"].update(workload_fingerprint="different"), "mismatched_workload_fingerprint"),
    (lambda value: value["accounting"].update(errors=["scheduler:RuntimeError"]), "llm_load_generator_errors"),
    (lambda value: value["accounting"].update(rate_read_errors=1), "llm_load_generator_errors"),
    (lambda value: value["accounting"].update(scheduler_lateness_p95_seconds=2), "llm_invalid_load_generator_timing"),
    (lambda value: value["samples"][3].update(latency_p95_seconds=None), "llm_incomplete_telemetry"),
    (lambda value: value["samples"][3].update(instrumentation_ready=False), "llm_incomplete_telemetry"),
    (lambda value: value["samples"][3].pop("instrumentation_ready"), "llm_incomplete_telemetry"),
    (lambda value: value["samples"][3].update(source_errors=["KafkaError"]), "llm_incomplete_telemetry"),
    (lambda value: value["accounting"].update(scheduler_lateness_max_seconds=11), "llm_invalid_load_generator_timing"),
    (lambda value: value["completion_observer"].update(errors=["KafkaError"]), "llm_incomplete_completion_observation"),
    (lambda value: value.pop("completion_observer"), "llm_incomplete_completion_observation"),
    (lambda value: value.update(cleanup_errors=["restore_failure"]), "llm_cleanup_errors"),
    (lambda value: value["accounting"].update(latency_count=1), "llm_incomplete_offered_latency"),
    (lambda value: value["accounting"].update(offered_to_commit_p95_upper_seconds=None), "llm_incomplete_offered_latency"),
])
def test_missing_or_confounded_evidence_is_inconclusive(change, expected):
    runs = three_pairs()
    change(runs[1])
    report = build_report(runs)
    assert report["verdict"] == "inconclusive"
    assert expected in report["pairs"][0]["reasons"]


def test_guard_rate_reduction_never_receives_model_credit():
    value = run()
    value["actions"][0].update(action_origin="guard", status="paused", reason="decision_failed:unavailable")
    summary = summarize_run(value)
    assert summary["adaptation_chains"] == []
    assert summary["guards_under_load"] == 1
    assert summary["applied_llm_decisions"] == 0


def test_guard_between_model_action_and_recovery_breaks_chain():
    value = run()
    value["actions"].append({"timestamp": 1045, "status": "paused", "action_origin": "guard", "reason": "host_memory"})
    assert adaptation_chains(value) == []


@pytest.mark.parametrize("origin", ["baseline", "operator", "shutdown"])
def test_other_control_changes_cannot_be_attributed_to_model(origin):
    value = run()
    value["actions"].append({"timestamp": 1045, "status": "applied", "action_origin": origin,
                             "decision": {"rate_per_second": 50}})
    assert adaptation_chains(value) == []


def test_guard_at_the_same_time_as_model_action_breaks_chain():
    value = run()
    value["actions"].append({"timestamp": 1035, "status": "paused", "action_origin": "guard"})
    assert adaptation_chains(value) == []


def test_zero_action_requires_explicit_model_resume_and_open_actual_gate():
    value = run()
    value["actions"][0]["decision"]["rate_per_second"] = 0
    assert adaptation_chains(value) == []  # residual ingress alone is insufficient.
    resume = deepcopy(value["actions"][0])
    resume.update(timestamp=1045, decision_id="resume")
    resume["decision"]["rate_per_second"] = 50
    value["actions"].append(resume)
    assert adaptation_chains(value)[0]["followup_llm_decision_ids"] == ["resume"]
    value["samples"][-1]["admission_limit"] = 0
    assert adaptation_chains(value) == []


@pytest.mark.parametrize("mutation", ["offered_drop", "drain", "no_ingress_drop", "no_positive_service", "gap", "proposed"])
def test_recovery_requires_same_demand_actual_ingress_and_contiguous_positive_service(mutation):
    value = run()
    for sample in value["samples"][4:]:
        if mutation == "offered_drop":
            sample["offered_rate"] = 50
        elif mutation == "drain":
            sample["phase"] = "drain"
        elif mutation == "no_ingress_drop":
            sample["incoming_rate"] = 100
        elif mutation == "no_positive_service":
            sample.update(incoming_rate=0, throughput=0)
    if mutation == "gap":
        value["samples"][-1]["timestamp"] += 30
        value["load_ended_at"] += 30
    if mutation == "proposed":
        value["actions"][0]["status"] = "proposed"
    assert adaptation_chains(value) == []


def test_model_zero_is_not_proof_of_stability_while_no_work_is_served():
    value = run()
    value["actions"][0]["decision"]["rate_per_second"] = 0
    for sample in value["samples"][4:]:
        sample.update(incoming_rate=0, throughput=0, kafka_lag=0, latency_p95_seconds=None)
    assert adaptation_chains(value) == []


def test_rate_increase_with_recovery_is_not_misreported_as_decrease():
    value = run()
    value["initial_rate"] = 10
    assert adaptation_chains(value) == []


def test_baseline_must_really_overload_and_order_must_be_counterbalanced():
    runs = three_pairs()
    for value in runs:
        if value["mode"] == "baseline":
            for sample in value["samples"]:
                sample.update(kafka_lag=0, latency_p95_seconds=.1)
    report = build_report(runs)
    assert report["verdict"] == "not_demonstrated"
    assert "baseline_overload_not_observed" in report["pairs"][0]["reasons"]
    only_ab = [run(mode, pair, pair * 1000 + (100 if mode == "llm" else 0))
               for pair in range(1, 4) for mode in ("baseline", "llm")]
    assert "both_AB_and_BA_orders_required" in build_report(only_ab)["reasons"]


def test_small_lag_fluctuations_cannot_demonstrate_overload_stabilization():
    runs = three_pairs()
    for value in runs:
        for index, sample in enumerate(value["samples"], 1):
            recovered = value["mode"] == "llm" and index >= 5
            sample.update(kafka_lag=0 if recovered else index,
                          latency_p95_seconds=.3, throughput=sample["incoming_rate"])
    report = build_report(runs)
    assert report["verdict"] == "not_demonstrated"
    assert report["demonstrated_pairs"] == 0
    for pair in report["pairs"]:
        assert "baseline_overload_not_observed" in pair["reasons"]


def test_sustained_lag_growth_matching_calibration_floor_establishes_overload():
    value = run("baseline")
    for sample in value["samples"]:
        sample["latency_p95_seconds"] = .3
    # The existing fixture grows 10 -> 20 -> 30: exactly the calibration floor.
    summary = summarize_run(value)
    assert summary["overload_observed"] is True
    assert summary["overload_windows"][0]["reasons"] == ["sustained_growing_lag"]


def test_no_cherry_picking_three_good_pairs_when_fourth_valid_pair_fails():
    runs = three_pairs() + [run("baseline", 4, 4000), run("llm", 4, 4100)]
    runs[-1]["actions"] = []
    report = build_report(runs)
    assert report["verdict"] == "not_demonstrated"
    assert "not_all_eligible_pairs_support_the_claim" in report["reasons"]


def test_invalid_fourth_pair_cannot_be_silently_excluded_to_claim_success():
    runs = three_pairs() + [run("baseline", 4, 4000), run("llm", 4, 4100)]
    runs[-1]["accounting"]["pending"] = 1
    report = build_report(runs)
    assert report["verdict"] != "demonstrated"
    assert "ineligible_pairs_require_review" in report["reasons"]


def test_both_orders_are_insufficient_if_they_do_not_alternate():
    runs = [run(mode, pair, start=pair * 1000 + offset)
            for pair in range(1, 4)
            for mode, offset in (("baseline", 0 if pair < 3 else 100), ("llm", 100 if pair < 3 else 0))]
    assert "pair_orders_must_alternate" in build_report(runs)["reasons"]


def test_parallel_trial_loads_are_not_a_matched_comparison():
    runs = three_pairs()
    runs[1] = run("llm", 1, start=1050)
    assert "overlapping_trial_loads" in build_report(runs)["pairs"][0]["reasons"]


def test_duplicate_or_missing_pair_members_never_succeed():
    runs = three_pairs()
    runs.append(deepcopy(runs[0]))
    report = build_report(runs)
    assert report["verdict"] == "inconclusive"
    assert report["pairs"][0]["reasons"] == ["pair_requires_exactly_one_baseline_and_one_llm"]
    assert build_report([])["verdict"] == "inconclusive"


def test_single_outlier_does_not_establish_sustained_overload():
    value = run("baseline")
    for sample in value["samples"]:
        sample.update(kafka_lag=0, latency_p95_seconds=.1)
    value["samples"][3].update(kafka_lag=100, latency_p95_seconds=50)
    assert summarize_run(value)["overload_observed"] is False


def test_baseline_overload_requires_the_generator_to_deliver_most_offered_demand():
    value = run("baseline")
    for sample in value["samples"]:
        sample["incoming_rate"] = 50
    assert summarize_run(value)["overload_observed"] is False


def test_dropping_work_through_transport_failures_cannot_count_as_improvement():
    runs = three_pairs()
    runs[1]["accounting"].update(delivery_failed=100, acknowledged=250, committed=250)
    report = build_report(runs)
    assert report["verdict"] != "demonstrated"
    assert "delivery_or_enqueue_failure_fraction_missing_or_worse" in report["pairs"][0]["reasons"]


def test_shedding_cost_and_latency_semantics_remain_visible(tmp_path, monkeypatch):
    monkeypatch.setattr("research.llm_report.plot_runs", lambda *args, **kwargs: {"status": "unavailable"})
    report = write_report(three_pairs(), tmp_path)
    assert json.loads((tmp_path / "comparison.json").read_text())["verdict"] == report["verdict"]
    markdown = (tmp_path / "comparison.md").read_text(encoding="utf-8")
    assert "Deferred" in markdown and "Rejected" in markdown and "Served fraction" in markdown
    assert "polling upper bound" in markdown
    assert "350" in markdown
    assert markdown == markdown_report(report)
