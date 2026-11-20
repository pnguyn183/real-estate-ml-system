import csv
import hashlib
import json

from research.predictive_control_experiment import evaluate_readiness, main


def fixture(tmp_path, *, capacity_rate=40, capacity_status="calibration_complete", fingerprint="fixed-allocation"):
    directory = tmp_path / "forecast"
    directory.mkdir(parents=True)
    artifact = directory / "forecast.joblib"
    artifact.write_bytes(b"fixture model; not runtime evidence")
    manifest = {
        "schema_version": 1,
        "eligible": True,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "model": "fixture",
        "target": "requested_rate",
        "target_semantics": "offered demand before admission",
        "horizon_seconds": 300,
    }
    with (directory / "supervised.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["requested_rate"])
        writer.writeheader()
        writer.writerows({"requested_rate": value} for value in (5, 20, 40, 60))
    (directory / "split.json").write_text(json.dumps({"train_indices": [0, 1, 2]}), encoding="utf-8")
    manifest["support_sha256"] = hashlib.sha256((directory / "supervised.csv").read_bytes()).hexdigest()
    manifest["split_sha256"] = hashlib.sha256((directory / "split.json").read_bytes()).hexdigest()
    artifact.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
    capacity = tmp_path / "capacity.json"
    capacity.write_text(json.dumps({
        "status": capacity_status,
        "calibration_evidence": {"rate": capacity_rate, "reason": "fixture_overload"},
        "resource_fingerprint": fingerprint,
    }), encoding="utf-8")
    return artifact, capacity


def test_ready_only_authorizes_separate_trial(tmp_path):
    artifact, capacity = fixture(tmp_path)
    result = evaluate_readiness(artifact, capacity)
    assert result["status"] == "ready_for_isolated_predictive_control_trial"
    assert result["blockers"] == []
    assert result["production_controller_integrated"] is False
    assert result["required_trial"]["minimum_pairs"] == 3


def test_inconclusive_capacity_and_missing_fingerprint_fail_closed(tmp_path):
    artifact, capacity = fixture(tmp_path, capacity_rate=None,
                                 capacity_status="calibration_inconclusive", fingerprint=None)
    result = evaluate_readiness(artifact, capacity)
    assert result["status"] == "blocked_before_trial"
    assert set(result["blockers"]) >= {
        "capacity_calibration_not_completed",
        "measured_overload_rate_missing",
        "capacity_resource_fingerprint_missing",
    }


def test_forecast_must_cover_measured_overload_range(tmp_path):
    artifact, capacity = fixture(tmp_path, capacity_rate=100)
    result = evaluate_readiness(artifact, capacity)
    assert "forecast_training_range_does_not_cover_overload_rate" in result["blockers"]


def test_test_only_target_range_does_not_count_as_training_support(tmp_path):
    artifact, capacity = fixture(tmp_path, capacity_rate=50)
    result = evaluate_readiness(artifact, capacity)
    assert result["forecast"]["training_support"]["max"] == 40
    assert "forecast_training_range_does_not_cover_overload_rate" in result["blockers"]


def test_hash_mismatch_and_cli_block_without_integrating(tmp_path):
    artifact, capacity = fixture(tmp_path)
    artifact.write_bytes(b"changed")
    output = tmp_path / "readiness.json"
    assert main(["--artifact", str(artifact), "--capacity-evidence", str(capacity),
                 "--output", str(output)]) == 2
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert "forecast_artifact_hash_mismatch" in saved["blockers"]
    assert saved["production_controller_integrated"] is False


def test_support_and_split_must_match_hashes_bound_to_forecast(tmp_path):
    artifact, capacity = fixture(tmp_path)
    directory = artifact.parent
    (directory / "supervised.csv").write_text("requested_rate\n999\n", encoding="utf-8")
    result = evaluate_readiness(artifact, capacity)
    assert "forecast_support_hash_mismatch" in result["blockers"]

    artifact, capacity = fixture(tmp_path / "second")
    (artifact.parent / "split.json").write_text(json.dumps({"train_indices": [1]}), encoding="utf-8")
    result = evaluate_readiness(artifact, capacity)
    assert "forecast_split_hash_mismatch" in result["blockers"]


def test_legacy_forecast_without_bound_inputs_fails_closed(tmp_path):
    artifact, capacity = fixture(tmp_path)
    manifest_path = artifact.with_suffix(".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("support_sha256")
    manifest.pop("split_sha256")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = evaluate_readiness(artifact, capacity)
    assert set(result["blockers"]) >= {
        "forecast_support_hash_missing",
        "forecast_split_hash_missing",
    }
