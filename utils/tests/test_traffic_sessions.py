"""Finite session controls and history provenance; fixtures are not runtime evidence."""
from copy import deepcopy
from contextlib import contextmanager, nullcontext
import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import research.collect_session as sessions


@pytest.mark.parametrize("duration", [60, 3600, 7200, 7199.5, 8 * 3600])
def test_requested_duration_is_preserved_without_flat_or_out_of_bounds_traffic(duration):
    profile = sessions.build_profile(duration, min_rate=5, max_rate=60, seed=7)
    assert sum(stage["seconds"] for stage in profile) == pytest.approx(duration)
    assert all(stage["seconds"] > 0 and 5 <= stage["rate"] <= 60 for stage in profile)
    if duration >= 3600:
        assert len({stage["rate"] for stage in profile}) >= 4


def test_workload_seed_is_replayable_and_changes_the_profile():
    first = sessions.build_profile(7200, seed=42)
    assert first == sessions.build_profile(7200, seed=42)
    assert first != sessions.build_profile(7200, seed=43)


@pytest.mark.parametrize("duration", [0, -1, 59, 8 * 3600 + 1, float("nan"), float("inf")])
def test_invalid_or_unbounded_duration_is_rejected(duration):
    with pytest.raises(ValueError):
        sessions.build_config(duration)


@pytest.mark.parametrize("minimum,maximum", [(0, 60), (-1, 60), (60, 5), (5, 5), (5, float("inf"))])
def test_invalid_or_constant_rate_range_is_rejected(minimum, maximum):
    with pytest.raises(ValueError):
        sessions.build_config(3600, min_rate=minimum, max_rate=maximum)


def test_long_session_retains_record_budget_and_disables_resource_scaling():
    config = sessions.build_config(7200, min_rate=5, max_rate=60, seed=7)
    requested = sum(math.ceil(stage["rate"] * stage["seconds"]) for stage in config["profile"])
    assert requested <= config["max_records"] <= 1_000_000
    assert config["resource_scaling"]["enabled"] is False
    assert config["protocol"]["forecast_target"] == "requested_rate"
    assert config["sample_seconds"] == 5
    assert config["control"]["initial_rate"] >= max(stage["rate"] for stage in config["profile"])


def test_over_budget_session_is_rejected_instead_of_silently_truncated():
    with pytest.raises(ValueError):
        sessions.build_config(8 * 3600, min_rate=100, max_rate=200, seed=7)


@pytest.mark.parametrize("interval", [0, -5, 61, float("nan")])
def test_invalid_sampling_interval_is_rejected(interval):
    with pytest.raises(ValueError):
        sessions.build_config(3600, sample_seconds=interval)


def test_cli_exposes_duration_and_control_commands_without_starting_docker():
    for command in ([], ["start"], ["status"], ["stop"], ["export"]):
        result = subprocess.run([sys.executable, "-m", "research.collect_session", *command, "--help"],
                                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        if command == ["start"]:
            assert "--minutes" in result.stdout and "--hours" in result.stdout


def _write_history(directory, *, run_id="fixture-run", timestamp=1_700_000_000,
                   topic="real_estate_stress_raw", status="completed"):
    directory.mkdir(parents=True)
    config = sessions.build_config(3600, seed=7)
    metadata = {"schema_version": 1, "session_path": str(directory), "status": status,
                "started_at": timestamp, "ended_at": timestamp + 3600,
                "forecast_target": "requested_rate", "topic": topic, "run_id": run_id,
                "workload_kind": "controlled_synthetic", "sample_seconds": 5}
    (directory / "session.json").write_text(json.dumps(metadata), encoding="utf-8")
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    records = [{"timestamp": timestamp + index * 5, "run_id": run_id, "topic": topic,
                "phase": "load", "requested_rate": index + 5, "incoming_rate": index + 4,
                "instrumentation_ready": index != 2,
                "errors": ["fixture telemetry unavailable"] if index == 2 else []}
               for index in range(5)]
    source = directory / "baseline" / "observations.jsonl"
    source.parent.mkdir()
    source.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    return records, source


def test_export_preserves_actual_timestamps_session_boundaries_and_errors(tmp_path):
    early = tmp_path / "early"
    late = tmp_path / "late"
    first, _ = _write_history(early, run_id="early", timestamp=1_700_000_000)
    second, _ = _write_history(late, run_id="late", timestamp=1_700_100_000)
    destination = tmp_path / "combined.jsonl"
    sessions.export_sessions([late, early], destination)
    actual = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert actual == first + second
    assert len(actual) == 10  # The overnight gap must not be filled with invented zero traffic.
    assert actual[5]["timestamp"] - actual[4]["timestamp"] == 99_980
    assert actual[2]["errors"] == ["fixture telemetry unavailable"]
    assert destination.with_suffix(".manifest.json").is_file()


@pytest.mark.parametrize("problem", ["duplicate_directory", "duplicate_run", "mixed_topic", "missing_run"])
def test_export_rejects_untrustworthy_provenance_before_writing_output(tmp_path, problem):
    early, late = tmp_path / "early", tmp_path / "late"
    _write_history(early, run_id="early")
    records, path = _write_history(late, run_id="early" if problem == "duplicate_run" else "late",
                                   timestamp=1_700_100_000,
                                   topic="different_topic" if problem == "mixed_topic" else "real_estate_stress_raw")
    if problem == "missing_run":
        records[0].pop("run_id")
        path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    destination = tmp_path / "combined.jsonl"
    with pytest.raises(ValueError):
        sessions.export_sessions([early, early if problem == "duplicate_directory" else late], destination)
    assert not destination.exists()


@pytest.mark.parametrize("existing", ["observations", "manifest"])
def test_export_does_not_overwrite_existing_evidence(tmp_path, existing):
    source = tmp_path / "source"
    _write_history(source)
    destination = tmp_path / "combined.jsonl"
    protected = destination if existing == "observations" else destination.with_suffix(".manifest.json")
    protected.write_text("original evidence", encoding="utf-8")
    with pytest.raises((ValueError, FileExistsError)):
        sessions.export_sessions([source], destination)
    assert protected.read_text(encoding="utf-8") == "original evidence"


def test_status_detects_stale_running_metadata_without_rewriting_history(tmp_path, monkeypatch):
    directory = tmp_path / "interrupted"
    _write_history(directory, status="running")
    path = directory / "session.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata.pop("ended_at")
    path.write_text(json.dumps(metadata), encoding="utf-8")
    previous = path.read_bytes()
    monkeypatch.setattr(sessions, "_lock_held", lambda path: False)
    result = sessions.session_status(directory)
    assert result["persisted_status"] == "running"
    assert result["status"] == "stale"
    assert result["active"] is False
    assert path.read_bytes() == previous


def test_stop_rejects_stale_session_instead_of_signalling_an_unrelated_pid(tmp_path, monkeypatch):
    directory = tmp_path / "interrupted"
    _write_history(directory, status="running")
    monkeypatch.setattr(sessions, "_lock_held", lambda path: False)
    with pytest.raises(ValueError):
        sessions.request_stop(directory)


def test_stop_rejects_already_completed_session(tmp_path, monkeypatch):
    directory = tmp_path / "complete"
    _write_history(directory)
    monkeypatch.setattr(sessions, "_lock_held", lambda path: False)
    with pytest.raises(ValueError):
        sessions.request_stop(directory)


def test_export_refuses_a_session_still_being_written(tmp_path, monkeypatch):
    directory = tmp_path / "running"
    _write_history(directory, status="running")
    monkeypatch.setattr(sessions, "_lock_held", lambda path: True)
    destination = tmp_path / "combined.jsonl"
    with pytest.raises(ValueError):
        sessions.export_sessions([directory], destination)
    assert not destination.exists()


@pytest.fixture
def isolated_session_lifecycle(tmp_path, monkeypatch):
    """Use real per-session OS locks without touching the shared experiment lock."""
    original_lock = sessions.experiment_lock
    monkeypatch.setattr(sessions, "experiment_lock", lambda path=None: original_lock(
        path if path is not None else tmp_path / "fixture-experiment.lock"))
    monkeypatch.setattr(sessions, "keep_system_awake", nullcontext)
    clock = SimpleNamespace(value=1000.)
    monkeypatch.setattr(sessions, "time", SimpleNamespace(monotonic=lambda: clock.value))
    return clock


@pytest.mark.parametrize("manual_stop", [False, True])
def test_cli_start_runs_callbacks_persists_real_audit_of_fixture_rows_and_releases_lock(
        tmp_path, monkeypatch, isolated_session_lifecycle, manual_stop):
    clock = isolated_session_lifecycle
    captured = {}

    def fixture_runner(mode, config, output, bootstrap, topic, group, *, stop_requested, on_observation):
        assert mode == "baseline" and config["resource_scaling"]["enabled"] is False
        assert sum(stage["seconds"] for stage in config["profile"]) == 60
        assert topic == "real_estate_stress_raw"
        output.mkdir()
        rows = []
        for index, phase in enumerate(("load", "load", "drain")):
            row = {"timestamp": 1_700_000_000 + index * 5, "run_id": "fixture-run",
                   "topic": topic, "phase": phase, "requested_rate": 5 + index if phase == "load" else 0,
                   "incoming_rate": index, "errors": [], "instrumentation_ready": True,
                   "throughput": index, "kafka_lag": 0, "cpu_percent": 10, "ram_percent": 20}
            rows.append(row)
            on_observation(row)
            current = sessions.session_status(output.parent)
            assert current["active"] is True
            assert current["status"] == ("draining" if phase == "drain" else "running")
            if manual_stop and index == 0:
                receipt = sessions.request_stop(output.parent)
                assert receipt["status"] == "stop_requested"
                clock.value += 1
            assert stop_requested() is manual_stop
        (output / "observations.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        captured["session"] = output.parent
        return {"status": "stopped" if manual_stop else "completed", "run_id": "fixture-run",
                "acknowledged": 2, "final_lag": 0}

    monkeypatch.setattr(sessions, "run_mode", fixture_runner)
    result = sessions.main(["start", "--minutes", "1", "--seed", "7", "--output-root", str(tmp_path)])
    assert result == 0
    selected = captured["session"]
    status = sessions.session_status(selected)
    assert status["status"] == ("stopped" if manual_stop else "completed")
    assert status["active"] is False
    assert status["stop_requested"] is manual_stop
    assert status["ended_at"]
    assert status["observations"] == 3
    report = json.loads((selected / "history_audit.json").read_text(encoding="utf-8"))
    assert report["rows"] == 3
    assert report["target"] == "requested_rate"
    assert report["workload_kind"] == "controlled_synthetic"
    assert report["forecast_readiness"].startswith("Not established")


def test_runner_failure_is_finalized_without_disclosing_arbitrary_exception_text(
        tmp_path, monkeypatch, isolated_session_lifecycle, capsys):
    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture private connection string that must not be logged")

    monkeypatch.setattr(sessions, "run_mode", unavailable)
    config = sessions.build_config(60, seed=7)
    original = deepcopy(config)
    result = sessions.start_session(config, output_root=tmp_path)
    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
    assert result["failure_phase"] == "running_workload"
    assert result["error_hint"] == sessions.FAILURE_HINTS["running_workload"]
    assert result["ended_at"]
    assert config == original
    selected = Path(result["session_path"])
    assert sessions.session_status(selected)["active"] is False
    assert "private connection" not in (selected / "session.json").read_text(encoding="utf-8")
    assert "private connection" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", [None, "prepare", "restore", "audit", "reported_workload"])
def test_lean_session_lifecycle_preserves_safe_failure_phase_and_workload_outcome(
        tmp_path, monkeypatch, isolated_session_lifecycle, capsys, failure):
    """Exercise session/context wiring; this fixture is not real Docker evidence."""
    events = []

    @contextmanager
    def fixture_lean(enabled, path):
        assert enabled is True
        events.append("prepare")
        if failure == "prepare":
            raise RuntimeError("private fixture preparation details")
        receipt = {"enabled": True, "status": "active"}
        sessions._atomic_json(path, receipt)
        try:
            yield receipt
        finally:
            events.append("restore")
            receipt["status"] = "restore_failed" if failure == "restore" else "restored"
            sessions._atomic_json(path, receipt)
            if failure == "restore":
                raise RuntimeError("private fixture restoration details")

    def fixture_runner(mode, config, output, *args, **kwargs):
        events.append("workload")
        output.mkdir()
        report = {"status": "failed" if failure == "reported_workload" else "completed",
                  "run_id": "fixture-run", "acknowledged": 17, "final_lag": 0}
        sessions._atomic_json(output / "report.json", report)
        return report

    def fixture_audit(*args):
        events.append("audit")
        if failure == "audit":
            raise RuntimeError("private fixture audit details")
        return {"status": "audited_fixture"}

    monkeypatch.setattr(sessions, "lean_environment", fixture_lean)
    monkeypatch.setattr(sessions, "run_mode", fixture_runner)
    monkeypatch.setattr(sessions, "_audit_session", fixture_audit)
    result = sessions.start_session(sessions.build_config(60, seed=7), output_root=tmp_path, lean=True)
    assert result["lean_mode"] is True and result["ended_at"]
    assert sessions.session_status(Path(result["session_path"]))["active"] is False
    if failure == "prepare":
        assert events == ["prepare"]
        assert result["failure_phase"] == "preparing_runtime"
        assert not Path(result["report_path"]).exists()
        assert not Path(result["environment_path"]).exists()
    else:
        assert events[:3] == ["prepare", "workload", "restore"]
        assert result["acknowledged"] == 17 and result["final_lag"] == 0
        assert result["experiment_status"] == ("failed" if failure == "reported_workload" else "completed")
        environment = json.loads(Path(result["environment_path"]).read_text(encoding="utf-8"))
        if failure == "restore":
            assert environment["status"] == "restore_failed"
            assert events == ["prepare", "workload", "restore"]
            assert result["failure_phase"] == "restoring_runtime"
        else:
            assert environment["status"] == "restored" and events[-1] == "audit"
    assert result["status"] == ("failed" if failure in {"prepare", "restore", "reported_workload"} else "completed")
    if failure == "reported_workload":
        assert result["failure_phase"] == "running_workload"
    elif failure == "audit":
        assert result["failure_phase"] == "auditing"
        assert result["quality_audit"] == {"status": "failed", "error_type": "RuntimeError"}
    elif failure is None:
        assert "failure_phase" not in result and "error_hint" not in result
    if failure:
        assert result["error_hint"] == sessions.FAILURE_HINTS[result["failure_phase"]]
    metadata_text = Path(result["session_path"], "session.json").read_text(encoding="utf-8")
    assert "private fixture" not in metadata_text
    assert "private fixture" not in capsys.readouterr().out


def test_dry_run_validates_profile_without_creating_a_session_or_calling_runner(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("a dry run must not start a collector or publish traffic")

    monkeypatch.setattr(sessions, "run_mode", forbidden)
    destination = tmp_path / "sessions"
    assert sessions.main(["start", "--hours", "1.5", "--seed", "42", "--dry-run",
                          "--output-root", str(destination)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["planned_load_seconds"] == 5400
    assert result["status"] == "dry_run"
    assert not destination.exists()


def test_status_keeps_keyboard_stop_evidence_without_stop_file(tmp_path):
    directory = tmp_path / "stopped"
    _write_history(directory, status="stopped")
    metadata_path = directory / "session.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["stop_requested"] = True
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert not (directory / "STOP").exists()
    assert sessions.session_status(directory)["stop_requested"] is True


def test_export_rejects_observation_run_changed_from_session_metadata(tmp_path):
    directory = tmp_path / "tampered"
    _write_history(directory)
    source = directory / "baseline/observations.jsonl"
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    rows[0]["run_id"] = "another-run"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="must match its session"):
        sessions.export_sessions([directory], tmp_path / "merged.jsonl")


def test_concurrent_session_is_rejected_before_creating_a_directory_or_running_workload(
        tmp_path, monkeypatch, isolated_session_lifecycle):
    def forbidden(*args, **kwargs):
        pytest.fail("the second collector must not publish traffic")

    monkeypatch.setattr(sessions, "run_mode", forbidden)
    root = tmp_path / "sessions"
    with sessions.experiment_lock():
        with pytest.raises(RuntimeError, match="another traffic experiment"):
            sessions.start_session(sessions.build_config(60), output_root=root)
    assert not root.exists()
