"""Analysis-contract fixtures; these are unit data, never experiment evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research.report import broker_summary, common_window_summary, comparison, generate_report, mode_summary, stage_summary


CONFIG = json.loads((Path(__file__).resolve().parents[2] / "research/experiment.json").read_text())


def fixture():
    stage = {"index": 0, "start_elapsed": 0, "seconds": 45, "requested_rate": 100,
             "offered": 4500, "admitted": 4500, "completed": True}
    records = [{"phase": "load", "stage": 0, "elapsed_seconds": t, "interval_seconds": 5,
                "throughput": 100, "accepted_rate": 100, "cpu_percent": 20, "ram_percent": 30,
                "kafka_lag": 5, "latency_p95_seconds": .1, "error_rate": 0, "errors": []}
               for t in (20, 25, 30, 35, 40, 45)]
    return stage, records


def test_complete_safe_stage_establishes_only_its_tested_offered_rate():
    stage, records = fixture()
    result = stage_summary(stage, records, CONFIG)
    assert result["classification"] == "stable_offered" and not result["failed_criteria"]
    assert result["lag_slope_per_second"] == 0


def test_rejecting_demand_never_increases_sustainable_offered_load():
    stage, records = fixture()
    stage["admitted"] = 2250
    for record in records:
        record["throughput"] = record["accepted_rate"] = 50
    result = stage_summary(stage, records, CONFIG)
    assert result["classification"] == "stable_admitted_only"
    assert result["served_fraction"] == .5 and not result["stable_offered"]
    summary = mode_summary({"stages": [stage]}, records, [], CONFIG)
    assert summary["max_sustainable_offered_rate"] is None
    assert summary["max_stable_admitted_rate"] == 50


@pytest.mark.parametrize("failure", ["missing", "few_samples", "coverage_gap", "aborted", "collection_error"])
def test_missing_or_censored_data_is_inconclusive_never_stable_or_saturated(failure):
    stage, records = fixture()
    if failure == "missing":
        records[2]["cpu_percent"] = None
    elif failure == "few_samples":
        records = records[-2:]
    elif failure == "coverage_gap":
        records.pop(2)
    elif failure == "aborted":
        stage["completed"] = False
    else:
        records[2]["errors"] = ["collector unavailable"]
    result = stage_summary(stage, records, CONFIG)
    assert result["classification"] == "inconclusive"
    summary = mode_summary({"stages": [stage]}, records, [], CONFIG)
    assert summary["first_observed_unstable_stage"] is None


def test_good_mean_does_not_hide_a_resource_peak_or_growing_backlog():
    stage, records = fixture()
    records[-1]["cpu_percent"] = 95
    records[-1]["kafka_lag"] = 180
    result = stage_summary(stage, records, CONFIG)
    assert result["classification"] == "unstable"
    assert not result["checks"]["max_cpu_percent"]
    assert not result["checks"]["lag_not_growing"]


def test_broker_balance_uses_all_three_actual_loads_and_keeps_missing_missing():
    records = [{"interval_seconds": 5, "brokers": {str(i): {"leader_incoming_rate": v}
                for i, v in ((1, 10), (2, 20), (3, 30))}}]
    result = broker_summary(records)
    assert result["max_over_mean"] == 1.5
    assert result["brokers"]["3"]["leader_load_share"] == .5
    assert result["coefficient_of_variation"] == pytest.approx(.408248290463863)
    incomplete = deepcopy(records)
    incomplete[0]["brokers"]["2"]["leader_incoming_rate"] = None
    assert broker_summary(incomplete)["max_over_mean"] is None
    assert broker_summary(incomplete)["brokers"]["1"]["leader_offset_growth"] is None
    records[0]["kafka_interval_seconds"] = 2
    assert broker_summary(records)["brokers"]["1"]["leader_offset_growth"] == 20


def test_before_after_omits_unmeasured_values_and_does_not_imply_improvement():
    result = comparison({"baseline": {"metrics": {"cpu": 10, "ram": None}},
                         "adaptive": {"metrics": {"cpu": 20, "ram": 5}}})
    assert result == [{"metric": "cpu", "before_agent": 10, "after_agent": 20, "difference": 10}]


def test_recovery_after_input_stops_is_labelled_drain():
    result = mode_summary({"load_ended_at": 100, "episodes": [{"t0": 80, "t3": 110}]}, [], [], CONFIG)
    assert result["episodes"][0]["recovery_phase"] == "drain"


def write_pair(root, *, missing=False, mismatch=False):
    for mode in ("baseline", "adaptive"):
        directory = root / mode
        directory.mkdir()
        config = deepcopy(CONFIG)
        if mismatch and mode == "adaptive":
            config["seed"] += 1
        manifest = {"control": config["control"], "config": config}
        stage, records = fixture()
        for row in records:
            row.update(requested_rate=100, current_limit=100, brokers={
                str(broker): {"leader_incoming_rate": 100 / 3} for broker in (1, 2, 3)})
        report = {"status": "completed", "stages": [stage], "load_started_at": 0,
                  "load_ended_at": 45, "load_seconds": 45, "offered": 4500,
                  "admitted": 4500, "rejected": 0, "acknowledged": 4500, "final_lag": 0,
                  "episodes": []}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        (directory / "report.json").write_text(json.dumps(report))
        (directory / "observations.jsonl").write_text("" if missing else "\n".join(json.dumps(r) for r in records))
        (directory / "actions.jsonl").write_text("" if missing else json.dumps({
            "action": "hold", "current_limit": 100, "new_limit": 100, "elapsed_seconds": 20}))


def test_chart_artifacts_render_from_recorded_rows_and_summary_is_valid_json(tmp_path):
    write_pair(tmp_path)
    output = tmp_path / "analysis"
    result = generate_report(tmp_path, output)
    assert result["paired_configuration_matches"] and result["paired_runs_completed"]
    assert len(result["charts"]) == 10
    for filename in result["charts"]:
        assert Path(filename).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    persisted = json.loads((output / "summary.json").read_text())
    assert persisted["modes"]["baseline"]["max_sustainable_offered_rate"] == 100


def test_missing_observations_produce_no_demo_charts_and_mismatched_pair_no_table(tmp_path):
    write_pair(tmp_path, missing=True, mismatch=True)
    result = generate_report(tmp_path, tmp_path / "analysis")
    assert result["charts"] == []
    assert result["before_after"] == []
    assert not result["paired_configuration_matches"]


def test_unequal_duration_comparison_excludes_later_unshared_high_load(tmp_path):
    write_pair(tmp_path)
    adaptive_path = tmp_path / "adaptive/report.json"
    report = json.loads(adaptive_path.read_text())
    report["load_seconds"] = 30
    report["status"] = "safety_stopped"
    adaptive_path.write_text(json.dumps(report))
    baseline_points = tmp_path / "baseline/observations.jsonl"
    points = [json.loads(line) for line in baseline_points.read_text().splitlines()]
    for row in points:
        if row["elapsed_seconds"] > 30:
            row["cpu_percent"] = 99
    baseline_points.write_text("\n".join(json.dumps(r) for r in points))
    result = generate_report(tmp_path, tmp_path / "analysis")
    assert result["common_load_seconds"] == 30
    assert not result["paired_runs_completed"]
    assert result["modes"]["baseline"]["metrics"]["mean_cpu_percent"] > 20
    cpu = next(r for r in result["before_after"] if r["metric"] == "mean_cpu_percent")
    assert cpu["before_agent"] == cpu["after_agent"] == 20
    assert result["common_window"]["baseline"]["last_observed_endpoint_seconds"] == 30


def test_common_window_drops_partial_boundary_intervals_without_interpolating():
    records = [{"phase": "load", "elapsed_seconds": 1, "interval_seconds": 2, "cpu_percent": 90},
               {"phase": "load", "elapsed_seconds": 4, "interval_seconds": 2, "cpu_percent": 20},
               {"phase": "load", "elapsed_seconds": 8, "interval_seconds": 4, "cpu_percent": 90}]
    result = common_window_summary(records, 6)
    assert result["samples"] == 1 and result["metrics"]["mean_cpu_percent"] == 20
    assert result["last_observed_endpoint_seconds"] == 4
