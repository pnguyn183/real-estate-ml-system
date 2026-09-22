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
