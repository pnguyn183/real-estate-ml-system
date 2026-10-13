"""Experiment orchestration tests with fake Kafka/Docker, never live evidence."""
from copy import deepcopy
import json
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace

import pytest

from agents.model_router import RoutedResponse
from agents.runtime_policy import RuntimePolicy
from research import llm_experiment as experiment


def config():
    return json.loads(Path("research/llm_experiment.json").read_text(encoding="utf-8"))


def sample(**updates):
    row = dict(timestamp=time.time(), interval_seconds=.1, incoming_rate=0.0, throughput=0.0,
               cpu_percent=30.0, ram_percent=40.0, kafka_lag=0,
               latency_p95_seconds=None, error_rate=None,
               vm_memory_available_percent=60.0, instrumentation_ready=True, errors=[],
               partitions={str(i): {"high_offset": 0} for i in range(3)})
    row.update(updates)
    return row


def test_default_config_is_bounded_and_calibration_is_separate():
    cfg = experiment.validate_config(config())
    assert cfg["pairs"] == 3
    assert [row["rate"] for row in experiment.calibration_profile(cfg)] == cfg["calibration_rates"]
    assert experiment.calibrated_profile({"status": "completed"}, cfg) is None


@pytest.mark.parametrize("changes", [
    {"seed": True}, {"max_queue": 1.5}, {"baseline_rate": float("nan")},
    {"max_records": 5}, {"calibration_rates": [30, 10]}, {"calibration_rates": [10, 10]},
    {"calibration_rates": [10001]}, {"lease_seconds": 40}, {"max_sample_age_seconds": 20},
    {"sample_seconds": 20, "calibration_stage_seconds": 30}, {"overload_seconds": 30},
    {"min_memory_available_percent": 9},
])
def test_invalid_config_rejected_before_runtime(changes):
    cfg = config()
    cfg.update(changes)
    with pytest.raises(ValueError):
        experiment.validate_config(cfg)


def overload_rows():
    return [sample(timestamp=1000 + i * 5, interval_seconds=5, stage="calibration_1",
                   offered_rate=100, incoming_rate=100, throughput=70,
                   kafka_lag=i * 15, latency_p95_seconds=3, error_rate=0)
            for i in range(3)]


@pytest.mark.parametrize("change", [
    lambda rows: rows[1].update(incoming_rate=20),
    lambda rows: rows[1].update(instrumentation_ready=False),
    lambda rows: rows[1].update(errors=["missing_worker"]),
    lambda rows: rows[1].update(incoming_rate=float("nan")),
    lambda rows: rows[1].update(offered_rate=200),
    lambda rows: rows[1].update(stage="another_stage"),
    lambda rows: rows[1].update(timestamp=1010),
    lambda rows: rows[1].update(timestamp=float("nan")),
])
def test_calibration_rejects_missing_or_mismatched_measurements(change):
    rows = overload_rows()
    change(rows)
    assert experiment.overload_evidence(rows, 2) is None


def test_calibration_requires_three_sustained_samples_and_successful_run():
    rows = overload_rows()
    assert experiment.overload_evidence(rows[:2], 2) is None
    evidence = experiment.overload_evidence(rows, 2)
    assert evidence["rate"] == 100
    assert evidence["reason"] == "sustained_latency"
    for row in rows:
        row["latency_p95_seconds"] = .3
    assert experiment.overload_evidence(rows, 2)["reason"] == "growing_backlog"
    run = {"status": "calibration_complete", "calibration_evidence": evidence}
    assert [s["rate"] for s in experiment.calibrated_profile(run, config())] == [10, 100, 10]
    run["status"] = "guard_stopped"
    assert experiment.calibrated_profile(run, config()) is None


def test_completion_observer_records_constructor_failure(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("sensitive connection string should never be logged")
    monkeypatch.setattr("confluent_kafka.Consumer", broken)
    observer = experiment.CompletionObserver("fake", None, .01)
    observer.start()
    observer.close()
    assert observer.errors[0]["error_type"] == "RuntimeError"
    assert "sensitive" not in json.dumps(observer.errors)


def test_completion_observer_records_close_failure(monkeypatch):
    class Consumer:
        def __init__(self, *args):
            pass
        def close(self):
            raise OSError("close failed")
    monkeypatch.setattr("confluent_kafka.Consumer", Consumer)
    observer = experiment.CompletionObserver("fake", None, .01)
    observer.stop_event.set()
    observer.start()
    observer.close()
    assert observer.errors[0]["error_type"] == "OSError"


def test_completion_observer_records_partial_partition_errors_without_crediting_failed_offsets(monkeypatch):
    observed = []
    class Consumer:
        def __init__(self, *args):
            pass
        def committed(self, partitions, timeout):
            observer.stop_event.set()
            return [SimpleNamespace(partition=0, offset=10, error=None),
                    SimpleNamespace(partition=1, offset=20, error=RuntimeError("private broker details")),
                    SimpleNamespace(partition=2, offset=-1001, error=None)]
        def close(self):
            pass
    monkeypatch.setattr("confluent_kafka.Consumer", Consumer)
    driver = SimpleNamespace(observe_committed=lambda offsets, now: observed.append(offsets))
    observer = experiment.CompletionObserver("fake", driver, .01)
    observer._run()
    assert observed == [{0: 10}]
    assert len(observer.errors) == 1
    assert observer.errors[0]["error_type"] == "KafkaPartitionError"
    assert observer.errors[0]["partition"] == 1
    assert "private" not in json.dumps(observer.errors)


@pytest.fixture
def fake_pipeline(monkeypatch):
    state = SimpleNamespace(offsets={i: 0 for i in range(3)}, collectors=[], calls=[],
                            producer_error=False, observation_error=False, low_memory=False,
                            corrupt_policy=None, model_delay=0, model_rate=5.0)

    class Collector:
        def __init__(self, **kwargs):
            self.closed = False
            self.last_total = 0
            self.last_time = time.time()
            state.collectors.append(self)
        def sample(self):
            now = time.time()
            total = sum(state.offsets.values())
            interval = max(.001, now - self.last_time)
            rate = (total - self.last_total) / interval
            self.last_total, self.last_time = total, now
            return sample(timestamp=now, interval_seconds=interval, incoming_rate=rate,
                          throughput=rate, latency_p95_seconds=.2 if rate else None,
                          error_rate=0.0 if rate else None,
                          partitions={str(i): {"high_offset": n} for i, n in state.offsets.items()})
        def close(self):
            self.closed = True

    class Producer:
        def __init__(self, *args):
            if state.producer_error:
                raise RuntimeError("private transport details")
        def produce(self, topic, key, value, callback):
            # The actual LoadDriver and ledger remain real. Only transport and
            # observed processing commits are simulated in this test fixture.
            partition = int(json.loads(value)["listing_id"].split("-")[-1]) % 3
            offset = state.offsets[partition]
            state.offsets[partition] += 1
            callback(None, SimpleNamespace(partition=lambda: partition, offset=lambda: offset))
        def poll(self, timeout):
            pass
        def flush(self, timeout):
            return 0

    class Consumer:
        def __init__(self, *args):
            if state.observation_error:
                raise ValueError("private consumer details")
        def committed(self, partitions, timeout):
            return [SimpleNamespace(partition=i, offset=n) for i, n in state.offsets.items()]
        def close(self):
            pass

    class Router:
        def extract_result(self, messages, timeout):
            state.calls.append(json.loads(messages[-1]["content"]))
            if state.model_delay:
                time.sleep(state.model_delay)
            return RoutedResponse(json.dumps({"state": "normal", "reason": "Test-only measured rate choice",
                "rate_per_second": state.model_rate, "training_allowed": False, "resources": {}}),
                "fake-provider", "fake-model", 1)

    class Actuator:
        config = SimpleNamespace(enabled=False)
        def observe(self):
            return {"status": "observed", "services": {}}

    monkeypatch.setattr(experiment, "TelemetryCollector", Collector)
    monkeypatch.setattr("confluent_kafka.Producer", Producer)
    monkeypatch.setattr("confluent_kafka.Consumer", Consumer)
    monkeypatch.setattr(experiment, "host_snapshot", lambda: {"memory_available_percent": 50.0})
    monkeypatch.setattr("psutil.virtual_memory", lambda: SimpleNamespace(total=100, available=5 if state.low_memory else 50))
    monkeypatch.setattr("research.llm_runtime.verify_runtime", lambda *args, **kwargs: {"fingerprint": "fixed-fake-allocation"})
    monkeypatch.setattr(experiment, "idle_preflight", lambda collector, *args: collector.sample())
    state.router, state.actuator = Router(), Actuator()
    state.config = config()
    state.config.update(sample_seconds=.02, decision_seconds=.06, timeout_seconds=.1,
                        lease_seconds=2, max_sample_age_seconds=5, max_wait_seconds=.25,
                        drain_seconds=1, completion_poll_seconds=.01, baseline_rate=100)
    state.profile = [{"name": "light", "rate": 20, "seconds": .1},
                     {"name": "overload", "rate": 40, "seconds": .3},
                     {"name": "recovery", "rate": 20, "seconds": .1}]
    return state


def trial(state, tmp_path, mode="baseline"):
    return experiment.run_trial(mode, 1, state.config, state.profile, tmp_path / mode,
                                state.router, state.actuator, {}, Event())


def test_fixed_baseline_accounts_every_offer_and_never_calls_model(fake_pipeline, tmp_path):
    state = fake_pipeline
    result = trial(state, tmp_path)
    assert result["status"] == "completed", result
    counts = result["accounting"]
    assert counts["offered"] == counts["planned"] == counts["committed"] == 16
    assert counts["pending"] == counts["enqueue_failed"] == 0
    assert result["offset_growth"] == counts["acknowledged"]
    assert not state.calls
    assert all(c.closed for c in state.collectors)
    assert result["actions"][-1]["action_origin"] == "shutdown"
    assert RuntimePolicy(True, tmp_path / "baseline/control/policy.json", experiment.TOPIC).read().rate_per_second == 0
    saved = json.loads((tmp_path / "baseline/run.json").read_text())
    assert saved == result


def test_real_feedback_agent_applies_model_with_offered_demand_context(fake_pipeline, tmp_path):
    result = trial(fake_pipeline, tmp_path, "llm")
    assert result["status"] == "completed", result
    assert result["accounting"]["offered"] == result["accounting"]["planned"] == 16
    model = [a for a in result["actions"] if a["action_origin"] == "llm"]
    assert model and all(a["decision"]["rate_per_second"] == 5 for a in model)
    assert model[0]["model"] == "fake-model"
    assert any(row["offered_rate"] is not None for call in fake_pipeline.calls for row in call["history"])
    assert all(call["training"]["status"] == "experiment_disabled" for call in fake_pipeline.calls)
    assert result["accounting"]["deferred"] > 0
    assert all(c.closed for c in fake_pipeline.collectors)


def test_producer_constructor_failure_still_closes_collector_and_revokes(fake_pipeline, tmp_path):
    fake_pipeline.producer_error = True
    result = trial(fake_pipeline, tmp_path)
    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
    assert all(c.closed for c in fake_pipeline.collectors)
    assert result["actions"][-1]["reason"] == "controller_stopped"
    assert "private" not in json.dumps(result)


def test_observer_failure_invalidates_instead_of_silently_claiming_completion(fake_pipeline, tmp_path):
    fake_pipeline.observation_error = True
    result = trial(fake_pipeline, tmp_path)
    assert result["status"] == "invalid"
    assert result["stop_reason"] == "completion_observation_errors"
    assert result["completion_observer"]["errors"]
    assert not result["cleanup_errors"]


def test_host_memory_guard_is_distinct_from_model_action(fake_pipeline, tmp_path):
    fake_pipeline.low_memory = True
    result = trial(fake_pipeline, tmp_path, "llm")
    assert result["status"] == "guard_stopped"
    assert result["stop_reason"] == "emergency_host_memory"
    assert not fake_pipeline.calls
    assert any(a.get("action_origin") == "guard" for a in result["actions"])


def test_expired_initial_policy_is_recorded_as_guard(fake_pipeline, tmp_path):
    # Delay the first call beyond the initial lease. No hidden gate pause may
    # later be credited to a successful model response.
    fake_pipeline.config.update(decision_seconds=.06, timeout_seconds=.1, lease_seconds=.17)
    result = trial(fake_pipeline, tmp_path, "llm")
    assert result["status"] == "guard_stopped"
    assert result["stop_reason"] == "policy_unavailable"
    assert any(a.get("reason") == "policy_unavailable" for a in result["actions"])


def test_driver_failures_invalidate_finished_run(fake_pipeline, tmp_path, monkeypatch):
    from research.llm_load import LoadDriver
    monkeypatch.setattr(LoadDriver, "health", lambda self: {"errors": ["scheduler:RuntimeError"], "rate_read_errors": 0})
    result = trial(fake_pipeline, tmp_path)
    assert result["status"] == "invalid"
    assert result["stop_reason"] == "load_generator_errors"


def test_inflight_model_cannot_restart_guarded_load_or_outlive_controller_lock(fake_pipeline, tmp_path):
    from agents.control_agent import controller_lock
    state = fake_pipeline
    state.config["timeout_seconds"] = .3
    original = state.router.extract_result
    state.lock_was_held = False
    def delayed(messages, timeout):
        state.low_memory = True
        time.sleep(.2)
        try:
            with controller_lock(tmp_path / "llm/control/controller.lock"):
                pass
        except OSError:
            state.lock_was_held = True
        return original(messages, timeout)
    state.router.extract_result = delayed
    result = trial(state, tmp_path, "llm")
    assert result["status"] == "guard_stopped"
    assert state.lock_was_held
    actions = result["actions"]
    guard = next(a for a in actions if a.get("reason") == "emergency_host_memory")
    late = next(a for a in actions if a.get("action_origin") == "llm")
    assert late["timestamp"] >= guard["timestamp"]
    assert actions[-1]["action_origin"] == "shutdown"
    assert RuntimePolicy(True, tmp_path / "llm/control/policy.json", experiment.TOPIC).read().rate_per_second == 0
    events = [json.loads(line) for line in (tmp_path / "llm/events.jsonl").read_text().splitlines()]
    assert all(event.get("admitted_at", 0) <= guard["timestamp"] for event in events)
    assert not result["cleanup_errors"]


def test_unusable_final_telemetry_invalidates_otherwise_completed_trial(fake_pipeline, tmp_path, monkeypatch):
    original = experiment.collect
    def collect(collector, runtime=None):
        row = original(collector, runtime)
        if runtime is None:
            row["errors"] = ["worker_disappeared"]
        return row
    monkeypatch.setattr(experiment, "collect", collect)
    result = trial(fake_pipeline, tmp_path)
    assert result["status"] == "invalid"
    assert result["stop_reason"] == "final_telemetry_unavailable"


def test_cleanup_failure_preserves_result_and_closes_remaining_resources(fake_pipeline, tmp_path, monkeypatch):
    original = experiment.CompletionObserver.close
    def broken_close(self):
        original(self)
        raise OSError("close reporting failed")
    monkeypatch.setattr(experiment.CompletionObserver, "close", broken_close)
    result = trial(fake_pipeline, tmp_path)
    assert result["status"] == "failed"
    assert result["cleanup_errors"] == [{"step": "completion_observer", "error_type": "OSError"}]
    assert all(c.closed for c in fake_pipeline.collectors)
    assert (tmp_path / "baseline/run.json").exists()


def test_config_validation_has_no_docker_side_effect(monkeypatch, tmp_path):
    cfg = config()
    cfg["max_records"] = 1
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(cfg))
    monkeypatch.setattr("research.llm_runtime.isolated_runtime", lambda *args, **kwargs: pytest.fail("Docker must not be touched"))
    with pytest.raises(ValueError):
        experiment.main(["--config", str(path), "--output", str(tmp_path / "evidence")])
    assert not (tmp_path / "evidence").exists()
