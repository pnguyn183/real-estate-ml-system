"""Runner admission/cleanup contract; no Docker or Kafka measurements are mocked as evidence."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zlib

import pytest

import research.run_experiment as runner


@pytest.mark.parametrize("limit", [2, 10, 40, 100, 500])
def test_rate_gate_sustains_cap_under_noninteger_offered_ratio(limit):
    gate = runner.RateGate(limit, 0)
    offered, seconds = limit * 1.5, 10
    accepted = sum(gate.accept(index / offered) for index in range(int(offered * seconds)))
    assert limit * seconds * .99 <= accepted <= limit * seconds + max(2, limit * .02)


def test_limit_reduction_discards_old_high_rate_burst_budget():
    gate = runner.RateGate(1000, 0)
    gate.set_limit(10, 1)
    assert sum(gate.accept(1) for _ in range(100)) <= 2
    assert sum(gate.accept(1 + i / 1000) for i in range(1, 1001)) <= 11


def test_observation_passes_offered_demand_not_broker_admitted_rate_to_agent():
    sample = {"timestamp": 123, "incoming_rate": 20, "throughput": 19, "cpu_percent": 20,
              "ram_percent": 30, "kafka_lag": 1, "latency_p95_seconds": .1, "error_rate": 0}
    assert runner.observation(sample, 100).incoming_rate == 100
    assert sample["incoming_rate"] == 20


class Clock:
    def __init__(self):
        self.current = 0

    def monotonic(self):
        return self.current

    def time(self):
        return self.current + 1_000_000

    def sleep(self, seconds):
        self.current += max(.0001, seconds)


class ImmediateExecutor:
    def __init__(self, **kwargs):
        self.closed = False

    def submit(self, function):
        value = function()
        return SimpleNamespace(done=lambda: True, result=lambda: value)

    def shutdown(self, wait):
        self.closed = True


@pytest.fixture
def fake_runtime(monkeypatch):
    clock = Clock()
    collectors, producers, executors = [], [], []

    class Collector:
        def __init__(self, **kwargs):
            self.closed = False
            collectors.append(self)

        def sample(self):
            clock.sleep(.005)
            return {"timestamp": clock.time(), "interval_seconds": 1, "kafka_lag": 0,
                    "instrumentation_ready": True, "incoming_rate": 0,
                    "errors": [], "throughput": 20, "cpu_percent": 20, "ram_percent": 30,
                    "latency_p95_seconds": .1, "error_rate": 0}

        def close(self):
            self.closed = True

    class Producer:
        def __init__(self, config):
            self.config, self.messages, self.pending = config, [], []
            self.fail_enqueue = False
            self.fail_delivery = False
            self.flush_count = 0
            producers.append(self)

        def produce(self, topic, key, value, callback):
            if self.fail_enqueue:
                raise BufferError("fixture full")
            self.messages.append((topic, key, json.loads(value)))
            self.pending.append((key, callback))

        def poll(self, timeout):
            for key, callback in self.pending:
                callback(RuntimeError("fixture delivery") if self.fail_delivery else None,
                         SimpleNamespace(partition=lambda key=key: zlib.crc32(key.encode()) % 3))
            self.pending.clear()

        def flush(self, timeout):
            self.flush_count += 1
            self.poll(0)
            return 0

    def executor(**kwargs):
        instance = ImmediateExecutor(**kwargs)
        executors.append(instance)
        return instance

    monkeypatch.setattr(runner, "time", clock)
    monkeypatch.setattr(runner, "TelemetryCollector", Collector)
    monkeypatch.setattr(runner, "Producer", Producer)
    monkeypatch.setattr(runner, "ThreadPoolExecutor", executor)
    monkeypatch.setattr(runner, "preflight", lambda *args: [])
    monkeypatch.setattr(runner, "code_identity", lambda: {"fixture": True})
    config = json.loads((Path(__file__).resolve().parents[2] / "research/experiment.json").read_text())
    config.update(profile=[{"rate": 10, "seconds": 2}, {"rate": 20, "seconds": 2}],
                  sample_seconds=1, decision_seconds=1, drain_seconds=1)
    config["control"]["recovery_window_seconds"] = .1
    return SimpleNamespace(clock=clock, collectors=collectors, producers=producers,
                           executors=executors, config=config, Producer=Producer, Collector=Collector)


def test_baseline_and_safe_adaptive_replay_same_logical_keys_and_facts(fake_runtime, tmp_path):
    reports = [runner.run_mode(mode, deepcopy(fake_runtime.config), tmp_path / mode,
                              "fixture", "real_estate_stress_raw", "fixture")
               for mode in ("baseline", "adaptive")]
    assert all(r["status"] == "completed" for r in reports)
    assert all(r["offered"] == r["admitted"] == r["acknowledged"] == 60 for r in reports)
    assert all(s["scheduler_shortfall"] == 0 for r in reports for s in r["stages"])
    left, right = fake_runtime.producers
    for a, b in zip(left.messages, right.messages):
        assert a[1] == b[1]
        assert {key: a[2][key] for key in ("price_text", "area_text", "property_type")} == {
            key: b[2][key] for key in ("price_text", "area_text", "property_type")}
        assert a[2]["url"] != b[2]["url"]  # Separate Mongo identities for each policy.
    baseline_actions = [json.loads(line) for line in (tmp_path / "baseline/actions.jsonl").read_text().splitlines()]
    assert baseline_actions and all(a["action"] == "hold" and a["applied_at"] is None for a in baseline_actions)
    assert all(c.closed for c in fake_runtime.collectors)
    assert all(e.closed for e in fake_runtime.executors)


@pytest.mark.parametrize("failure", ["enqueue", "delivery"])
def test_publication_failure_is_persisted_and_resources_close(fake_runtime, monkeypatch, tmp_path, failure):
    def producer(config):
        instance = fake_runtime.Producer(config)
        instance.fail_enqueue = failure == "enqueue"
        instance.fail_delivery = failure == "delivery"
        return instance

    monkeypatch.setattr(runner, "Producer", producer)
    output = tmp_path / "baseline"
    result = runner.run_mode("baseline", fake_runtime.config, output, "fixture", "real_estate_stress_raw", "fixture")
    assert result["status"] == "failed"
    assert result["acknowledged"] == 0
    assert result["enqueue_failed" if failure == "enqueue" else "delivery_failed"] == 1
    assert all(c.closed for c in fake_runtime.collectors)
    assert all(e.closed for e in fake_runtime.executors)
    assert json.loads((output / "report.json").read_text())["status"] == "failed"


def test_failed_topology_preflight_persists_failure_without_publishing(fake_runtime, monkeypatch, tmp_path):
    def fail(*args):
        raise RuntimeError("fixture: third broker unavailable")

    monkeypatch.setattr(runner, "preflight", fail)
    output = tmp_path / "baseline"
    result = runner.run_mode("baseline", fake_runtime.config, output, "fixture", "real_estate_stress_raw", "fixture")
    assert result["status"] == "failed" and "third broker" in result["error"]
    assert result["offered"] == result["acknowledged"] == 0
    assert all(c.closed for c in fake_runtime.collectors)
    assert all(e.closed for e in fake_runtime.executors)
    assert json.loads((output / "report.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("incoming_rate", [1, None])
def test_idle_preflight_rejects_concurrent_or_unmeasured_traffic(fake_runtime, monkeypatch, tmp_path, incoming_rate):
    original_sample = fake_runtime.Collector.sample

    def sample(collector):
        return {**original_sample(collector), "incoming_rate": incoming_rate}

    monkeypatch.setattr(fake_runtime.Collector, "sample", sample)
    output = tmp_path / "baseline"
    result = runner.run_mode("baseline", fake_runtime.config, output, "fixture", "real_estate_stress_raw", "fixture")
    assert result["status"] == "failed"
    assert "concurrent traffic or missing telemetry" in result["error"]
    assert result["offered"] == result["admitted"] == result["acknowledged"] == 0
    assert not fake_runtime.producers[0].messages
    assert all(c.closed for c in fake_runtime.collectors)
    assert all(e.closed for e in fake_runtime.executors)
    assert json.loads((output / "report.json").read_text())["status"] == "failed"


def test_experiment_lock_excludes_other_process_and_releases_after_exception(tmp_path):
    lock_path = tmp_path / "experiment.lock"
    probe = """
from pathlib import Path
import sys
from research.run_experiment import experiment_lock
try:
    with experiment_lock(Path(sys.argv[1])):
        print("acquired")
except RuntimeError as exc:
    print(str(exc))
    sys.exit(9)
"""

    def attempt():
        return subprocess.run([sys.executable, "-c", probe, str(lock_path)],
                              cwd=Path(__file__).resolve().parents[2],
                              capture_output=True, text=True, timeout=30)

    with pytest.raises(ValueError, match="fixture failure"):
        with runner.experiment_lock(lock_path):
            contender = attempt()
            assert contender.returncode == 9, contender.stderr
            assert "another traffic experiment holds the lock" in contender.stdout
            raise ValueError("fixture failure")
    next_run = attempt()
    assert next_run.returncode == 0, next_run.stderr
    assert next_run.stdout.strip() == "acquired"


def test_manual_stop_drains_and_preserves_observations(fake_runtime, tmp_path):
    samples = []
    result = runner.run_mode(
        "baseline", fake_runtime.config, tmp_path / "run", "fixture", "real_estate_stress_raw", "fixture",
        stop_requested=lambda: fake_runtime.clock.monotonic() >= 1,
        on_observation=samples.append,
    )
    assert result["status"] == "stopped"
    assert result["stop_reason"] == "user requested stop"
    assert 0 < result["offered"] < 60
    assert result["offered"] == result["acknowledged"]
    assert result["final_lag"] == 0
    assert not result["stages"][-1]["completed"]
    assert {sample["phase"] for sample in samples} == {"load", "drain"}
    assert all(sample["topic"] == "real_estate_stress_raw" and sample["group_id"] == "fixture" for sample in samples)
    saved = [json.loads(line) for line in (tmp_path / "run/observations.jsonl").read_text().splitlines()]
    assert saved == samples
    assert all(c.closed for c in fake_runtime.collectors)
    assert all(e.closed for e in fake_runtime.executors)


def test_stop_before_load_sends_no_records(fake_runtime, tmp_path):
    result = runner.run_mode("baseline", fake_runtime.config, tmp_path / "run", "fixture",
                             "real_estate_stress_raw", "fixture", stop_requested=lambda: True)
    assert result["status"] == "stopped"
    assert result["offered"] == result["acknowledged"] == 0
    # Cancellation now precedes readiness I/O. An unobserved queue is unknown,
    # not measured as empty, and no drain is needed for this zero-admission run.
    assert result["final_lag"] is None
    assert result["observation_count"] == 0
    assert result["preflight_attempts"][-1]["status"] == "cancelled"
    assert not fake_runtime.producers[0].messages


def test_session_duration_override_keeps_paired_runner_default_guard(fake_runtime):
    config = deepcopy(fake_runtime.config)
    config["profile"] = [{"rate": 1, "seconds": 7200}]
    with pytest.raises(ValueError, match="1 hour"):
        runner.validate_config(config)
    runner.validate_config(config, max_duration_seconds=8 * 3600)
    config["max_records"] = 100
    with pytest.raises(ValueError, match="profile exceeds max_records"):
        runner.validate_config(config, max_duration_seconds=8 * 3600)


def test_manual_stop_does_not_hide_drain_failure(fake_runtime, monkeypatch, tmp_path):
    original = fake_runtime.Collector.sample
    calls = 0

    def sample(collector):
        nonlocal calls
        calls += 1
        row = original(collector)
        if calls > 2:
            row["kafka_lag"] = 10
        return row

    monkeypatch.setattr(fake_runtime.Collector, "sample", sample)
    result = runner.run_mode("baseline", fake_runtime.config, tmp_path / "run", "fixture",
                             "real_estate_stress_raw", "fixture",
                             stop_requested=lambda: bool(fake_runtime.producers[0].messages))
    assert result["acknowledged"] > 0
    assert result["status"] == "drain_timeout"
    assert result["final_lag"] == 10


def _worker_timeout(row, port=8004):
    source = f"http://localhost:{port}/metrics"
    return {**row, "errors": [f"{source}: ReadTimeout: fixture timeout"],
            "worker_transport_errors": [{"source": source, "error_type": "ReadTimeout"}],
            "instrumentation_ready": False, "error_rate": None, "latency_p95_seconds": None}


def test_transient_worker_timeout_pauses_publication_then_resumes_with_error_evidence(fake_runtime, monkeypatch, tmp_path):
    config = deepcopy(fake_runtime.config)
    config.update(profile=[{"rate": 10, "seconds": 8}], telemetry_recovery={
        "max_consecutive_errors": 3, "max_recovery_seconds": 10, "healthy_samples_to_resume": 2})
    original = fake_runtime.Collector.sample
    calls = 0
    snapshots = []

    def sample(collector):
        nonlocal calls
        calls += 1
        row = original(collector)
        return _worker_timeout(row) if calls == 4 else row

    def observed(row):
        snapshots.append((row, len(fake_runtime.producers[0].messages)))

    monkeypatch.setattr(fake_runtime.Collector, "sample", sample)
    result = runner.run_mode("baseline", config, tmp_path / "run", "fixture", "real_estate_stress_raw",
                             "fixture", on_observation=observed)
    assert result["status"] == "completed"
    assert result["telemetry_recovery"]["withheld_total"] > 0
    assert result["offered"] == result["admitted"] + result["rejected"] == 80
    assert result["acknowledged"] == result["admitted"]
    paused = [(row, count) for row, count in snapshots if row["admission_paused"]]
    resumed = [(row, count) for row, count in snapshots if row["telemetry_recovery_state"] == "resumed"]
    assert len(paused) == 2 and len(resumed) == 1
    assert paused[0][1] == paused[1][1] == resumed[0][1]  # No Kafka writes during pause/warmup.
    assert snapshots[-1][1] > resumed[0][1]
    assert paused[0][0]["errors"] and paused[0][0]["latency_p95_seconds"] is None
    assert not paused[1][0]["telemetry_usable"]  # First good scrape is still recovery warmup.
    assert all(row["effective_admission_limit"] == 0 for row, _ in paused)
    saved = [json.loads(line) for line in (tmp_path / "run/observations.jsonl").read_text().splitlines()]
    assert any(row["worker_transport_errors"] for row in saved if row.get("worker_transport_errors"))
    assert [event["state"] for event in result["telemetry_recovery"]["events"]] == ["paused", "resumed"]


def test_persistent_worker_timeouts_exhaust_retry_budget(fake_runtime, monkeypatch, tmp_path):
    config = deepcopy(fake_runtime.config)
    config.update(profile=[{"rate": 10, "seconds": 10}], telemetry_recovery={
        "max_consecutive_errors": 3, "max_recovery_seconds": 10, "healthy_samples_to_resume": 2})
    calls = 0
    original = fake_runtime.Collector.sample

    def sample(collector):
        nonlocal calls
        calls += 1
        row = original(collector)
        return _worker_timeout(row) if calls > 3 else row

    monkeypatch.setattr(fake_runtime.Collector, "sample", sample)
    result = runner.run_mode("baseline", config, tmp_path / "run", "fixture", "real_estate_stress_raw", "fixture")
    assert result["status"] == "safety_stopped"
    assert result["load_seconds"] < 10
    assert result["telemetry_recovery"]["withheld_total"] > 0
    assert result["telemetry_recovery"]["events"][-1]["state"] == "stopped"


def test_paired_default_stops_first_error_and_drain_cannot_replace_stop_reason(fake_runtime, monkeypatch, tmp_path):
    original = fake_runtime.Collector.sample
    calls = 0

    def sample(collector):
        nonlocal calls
        calls += 1
        row = original(collector)
        if calls == 3:
            return {**row, "cpu_percent": 99}
        if calls > 3:
            return _worker_timeout(row)
        return row

    monkeypatch.setattr(fake_runtime.Collector, "sample", sample)
    result = runner.run_mode("baseline", fake_runtime.config, tmp_path / "run", "fixture", "real_estate_stress_raw", "fixture")
    assert result["status"] == "safety_stopped"
    assert result["stop_reason"].startswith("hard safety threshold: cpu_percent")
    assert result["load_seconds"] < 1


def test_manual_stop_works_while_waiting_for_telemetry_recovery(fake_runtime, monkeypatch, tmp_path):
    config = deepcopy(fake_runtime.config)
    config["telemetry_recovery"] = {"max_consecutive_errors": 3, "max_recovery_seconds": 10, "healthy_samples_to_resume": 2}
    original = fake_runtime.Collector.sample
    calls = 0
    stop = False

    def sample(collector):
        nonlocal calls
        calls += 1
        row = original(collector)
        return _worker_timeout(row) if calls == 3 else row

    def observe(row):
        nonlocal stop
        stop = stop or row["admission_paused"]

    monkeypatch.setattr(fake_runtime.Collector, "sample", sample)
    result = runner.run_mode("baseline", config, tmp_path / "run", "fixture", "real_estate_stress_raw", "fixture",
                             stop_requested=lambda: stop, on_observation=observe)
    assert result["status"] == "stopped"
    assert result["stop_reason"] == "user requested stop"
    assert result["final_lag"] == 0


def test_vm_pressure_stops_even_if_selected_container_ram_looks_safe(fake_runtime, monkeypatch, tmp_path):
    config = deepcopy(fake_runtime.config)
    config["vm_memory_safety"] = {"min_available_percent": 10, "min_free_percent_when_swap_full": 5, "max_swap_used_percent": 95}
    original = fake_runtime.Collector.sample
    calls = 0

    def sample(collector):
        nonlocal calls
        calls += 1
        row = original(collector)
        row.update(vm_memory_available_percent=50 if calls <= 2 else 6.25,
                   vm_memory_free_percent=40 if calls <= 2 else .8, vm_swap_used_percent=100 if calls > 2 else 0)
        return row

    monkeypatch.setattr(fake_runtime.Collector, "sample", sample)
    result = runner.run_mode("baseline", config, tmp_path / "run", "fixture", "real_estate_stress_raw", "fixture")
    assert result["status"] == "safety_stopped"
    assert "VM memory pressure" in result["stop_reason"]
    assert result["stop_observation"]["ram_percent"] == 30  # Old subset-only guard would miss this.
    assert result["stop_observation"]["vm_memory_available_percent"] == 6.25


def test_vm_full_swap_and_low_free_memory_are_visible_even_with_reclaimable_cache():
    config = {"vm_memory_safety": {"min_available_percent": 10, "min_free_percent_when_swap_full": 5, "max_swap_used_percent": 95}}
    sample = {"vm_memory_available_percent": 25, "vm_memory_free_percent": .8, "vm_swap_used_percent": 100}
    assert "swap used=100" in runner.vm_memory_stop_reason(sample, config)
    sample["vm_swap_used_percent"] = 0
    assert runner.vm_memory_stop_reason(sample, config) is None


def test_core_transport_loss_pauses_without_publishing_and_resumes(fake_runtime, monkeypatch, tmp_path):
    config = deepcopy(fake_runtime.config)
    config.update(profile=[{"rate": 10, "seconds": 8}], telemetry_recovery={
        "max_consecutive_errors": 5, "max_recovery_seconds": 60, "healthy_samples_to_resume": 2,
        "allow_core_transport_recovery": True})
    original = fake_runtime.Collector.sample
    calls = 0
    snapshots = []

    def sample(collector):
        nonlocal calls
        calls += 1
        row = original(collector)
        if calls == 4:
            row.update(cpu_percent=None, ram_percent=None, kafka_lag=None,
                       errors=["docker: TimeoutExpired: fixture", "kafka: KafkaException: fixture"],
                       collection_transport_errors=[{"source": "docker", "error_type": "TimeoutExpired"},
                                                    {"source": "kafka", "error_type": "KafkaException", "error_code": -185}])
        return row

    monkeypatch.setattr(fake_runtime.Collector, "sample", sample)
    result = runner.run_mode("baseline", config, tmp_path / "run", "fixture", "real_estate_stress_raw", "fixture",
                             on_observation=lambda row: snapshots.append((row, len(fake_runtime.producers[0].messages))))
    assert result["status"] == "completed"
    held = [(row, count) for row, count in snapshots if row["admission_paused"] or row["telemetry_recovery_state"] == "resumed"]
    assert len(held) == 3
    assert len({count for _, count in held}) == 1
    assert held[0][0]["kafka_lag"] is None
    assert result["telemetry_recovery"]["withheld_total"] > 0
