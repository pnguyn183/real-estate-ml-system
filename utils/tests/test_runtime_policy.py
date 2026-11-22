"""Control integration without Kafka, Mongo, model calls, or real sleeps."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.runtime_policy import (RateGate, RuntimePolicy, TrainingDeferred,
    mark_training_fitting, require_training_permission, training_lease)


class Clock:
    now = 100.0
    stopped = False

    def __init__(self):
        self.on_wait = lambda: None
        self.delays = []

    def __call__(self):
        return self.now

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        self.delays.append(delay)
        self.now += delay
        self.on_wait()
        return self.stopped


def write_policy(path, **changes):
    value = dict(schema_version=1, decision_id="decision-1", issued_at=100,
                 expires_at=200, rate_per_second=2, training_allowed=True,
                 source_topic="real_estate_stress_raw")
    value.update(changes)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def controlled(tmp_path):
    clock = Clock()
    path = tmp_path / "policy.json"
    write_policy(path)
    return RuntimePolicy(True, path, wall_clock=clock), clock


def test_default_control_topic_matches_stress_and_can_select_real_ingestion():
    policy = RuntimePolicy.from_env({"CONTROL_ENABLED": "true"})
    assert policy.applies("real_estate_stress_raw")
    assert not policy.applies("real_estate_raw")
    assert RuntimePolicy.from_env({"CONTROL_ENABLED": "true", "CONTROL_SOURCE_TOPIC": "real_estate_raw"}).applies("real_estate_raw")


def test_disabled_has_no_files_and_unrelated_topic_bypasses(controlled):
    policy, clock = controlled
    policy.path.unlink()
    assert replace(policy, enabled=False).read().training_allowed
    assert policy.read("real_estate_raw").valid
    assert RateGate(policy, "other", clock=clock).wait(clock)
    assert not clock.delays


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 2}, {"decision_id": ""},
    {"source_topic": "other"}, {"rate_per_second": -1}, {"rate_per_second": float("nan")},
    {"rate_per_second": True}, {"training_allowed": "true"}, {"issued_at": 106},
    {"expires_at": 100}, {"expires_at": 4000},
])
def test_invalid_policies_pause_publication_and_training(controlled, changes):
    policy, _ = controlled
    write_policy(policy.path, **changes)
    result = policy.read()
    assert not result.valid and result.rate_per_second == 0 and not result.training_allowed


def test_missing_corrupt_and_expired_policy_recover_after_atomic_replacement(controlled):
    policy, clock = controlled
    policy.path.unlink()
    assert not policy.read().valid
    policy.path.write_text("partial{", encoding="utf-8")
    assert not policy.read().valid
    write_policy(policy.path, expires_at=101)
    assert policy.read().valid
    clock.now = 101
    assert not policy.read().valid
    replacement = policy.path.with_suffix(".new")
    write_policy(replacement, issued_at=101, expires_at=120)
    replacement.replace(policy.path)
    assert policy.read().valid


def test_policy_read_retries_only_transient_permission_errors(controlled, monkeypatch):
    policy, _ = controlled
    original_open = Path.open
    calls, sleeps = [], []
    def transient(path, *args, **kwargs):
        calls.append(path)
        if len(calls) <= 2:
            raise PermissionError("replacement in progress")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", transient)
    monkeypatch.setattr("agents.runtime_policy.time.sleep", sleeps.append)
    assert policy.read().valid
    assert len(calls) == 3 and sleeps == [.01, .01]


def test_policy_read_permanent_permission_failure_is_bounded_and_fails_closed(controlled, monkeypatch):
    policy, _ = controlled
    calls, sleeps = [], []
    def denied(path, *args, **kwargs):
        calls.append(path)
        raise PermissionError("access denied")
    monkeypatch.setattr(Path, "open", denied)
    monkeypatch.setattr("agents.runtime_policy.time.sleep", sleeps.append)
    result = policy.read()
    assert not result.valid and result.rate_per_second == 0 and not result.training_allowed
    assert len(calls) == 4 and sleeps == [.01] * 3


@pytest.mark.parametrize("kind", ["missing", "corrupt", "expired"])
def test_policy_read_does_not_retry_missing_bad_or_expired_data(controlled, monkeypatch, kind):
    policy, _ = controlled
    if kind == "missing":
        policy.path.unlink()
    elif kind == "corrupt":
        policy.path.write_text("broken{")
    else:
        write_policy(policy.path, expires_at=100)
    monkeypatch.setattr("agents.runtime_policy.time.sleep", lambda delay: pytest.fail("unexpected retry"))
    assert not policy.read().valid


def test_gate_rereads_pause_caps_requested_rate_and_polls(controlled):
    policy, clock = controlled
    write_policy(policy.path, rate_per_second=0)
    polls = []
    clock.on_wait = lambda: write_policy(policy.path, rate_per_second=10) if clock.now >= 101 else None
    gate = RateGate(policy, policy.source_topic, clock=clock)
    assert gate.wait(clock, requested_rate=2, poll=lambda timeout: polls.append(clock.now))
    assert clock.now == 101
    assert len(polls) >= 5
    assert gate.wait(clock, requested_rate=2)
    assert clock.now == 101.5


def test_no_catchup_after_slow_publish_or_increase(controlled):
    policy, clock = controlled
    gate = RateGate(policy, policy.source_topic, clock=clock)
    assert gate.wait(clock)
    clock.now += 5
    gate.published()
    assert gate.wait(clock)
    assert clock.now == 105.5
    write_policy(policy.path, rate_per_second=4)
    assert gate.wait(clock)
    assert clock.now == 105.75


def test_revocation_while_waiting_and_finite_deadline(controlled):
    policy, clock = controlled
    gate = RateGate(policy, policy.source_topic, clock=clock)
    assert gate.wait(clock)
    clock.on_wait = policy.path.unlink if policy.path.exists() else lambda: None

    def revoke():
        policy.path.unlink(missing_ok=True)

    clock.on_wait = revoke
    assert not gate.wait(clock, deadline=102)
    assert clock.now == 102
    assert max(clock.delays) <= 0.25


def test_paused_gate_stops_promptly(controlled):
    policy, clock = controlled
    policy.path.unlink()
    clock.on_wait = lambda: setattr(clock, "stopped", True)
    assert not RateGate(policy, policy.source_topic, clock=clock).wait(clock)
    assert clock.now == 100.25


def test_training_lock_excludes_other_process_and_recovers(controlled):
    policy, _ = controlled
    source = (
        "import sys; from pathlib import Path; "
        "from agents.runtime_policy import RuntimePolicy, training_lease; "
        "p=RuntimePolicy(True, Path(sys.argv[1]), wall_clock=lambda:100); "
        "ctx=training_lease(p); allowed=ctx.__enter__(); "
        "print(allowed); ctx.__exit__(None,None,None)"
    )
    with training_lease(policy) as allowed:
        assert allowed
        assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "running"
        child = subprocess.run([sys.executable, "-c", source, str(policy.path)],
                               cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "False"
        assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "running"
        mark_training_fitting(policy)
    assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "completed"
    with training_lease(policy) as allowed:
        assert allowed
        mark_training_fitting(policy)


def test_training_deferred_and_failure_release_lock(controlled):
    policy, _ = controlled
    write_policy(policy.path, training_allowed=False)
    with training_lease(policy) as allowed:
        assert not allowed
    assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "deferred"
    write_policy(policy.path)
    with pytest.raises(RuntimeError, match="fit failed"):
        with training_lease(policy) as allowed:
            assert allowed
            raise RuntimeError("fit failed")
    assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "failed"
    with pytest.raises(TrainingDeferred):
        with training_lease(policy):
            write_policy(policy.path, training_allowed=False)
            require_training_permission(policy)
    assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "deferred"


def test_training_state_machine_persists_run_identity_and_fit_boundary(controlled):
    policy, _ = controlled
    with training_lease(policy) as allowed:
        assert allowed
        preparing = json.loads(policy.path.with_name("training.json").read_text())
        assert preparing["status"] == "running" and preparing["phase"] == "preparing"
        mark_training_fitting(policy, sample_count=17)
        fitting = json.loads(policy.path.with_name("training.json").read_text())
        assert fitting["status"] == "running" and fitting["phase"] == "fitting"
        assert fitting["run_id"] == preparing["run_id"] and fitting["sample_count"] == 17
    completed = json.loads(policy.path.with_name("training.json").read_text())
    events = [json.loads(line) for line in
              policy.path.with_name("training_events.jsonl").read_text().splitlines()]
    assert completed["status"] == completed["phase"] == "completed"
    assert [event["phase"] for event in events] == ["preparing", "fitting", "completed"]
    assert {event["run_id"] for event in events} == {preparing["run_id"]}
    assert all(event["schema_version"] == 1 for event in events)


def test_fit_transition_requires_the_active_controlled_lease(controlled):
    policy, _ = controlled
    with pytest.raises(RuntimeError, match="active controlled training lease"):
        mark_training_fitting(policy, sample_count=1)


def test_training_lock_is_released_after_process_crashes(controlled):
    policy, _ = controlled
    source = (
        "import os, sys; from pathlib import Path; "
        "from agents.runtime_policy import RuntimePolicy, mark_training_fitting, training_lease; "
        "p=RuntimePolicy(True, Path(sys.argv[1]), wall_clock=lambda:100); "
        "ctx=training_lease(p); allowed=ctx.__enter__(); "
        "mark_training_fitting(p) if allowed else None; "
        "os._exit(0 if allowed else 1)"
    )
    child = subprocess.run([sys.executable, "-c", source, str(policy.path)],
                           cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stderr
    # The process did not execute the context manager's unlock/finalizer.
    assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "running"
    with training_lease(policy) as allowed:
        assert allowed
        mark_training_fitting(policy)
    assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "completed"
    events = [json.loads(line) for line in
              policy.path.with_name("training_events.jsonl").read_text().splitlines()]
    assert [event["phase"] for event in events] == [
        "preparing", "fitting", "interrupted", "preparing", "fitting", "completed"]
    assert events[2]["run_id"] == events[0]["run_id"]
    assert events[2]["reason"] == "stale_state_after_lock_recovery"


def test_reconcile_uses_terminal_event_if_snapshot_replacement_was_interrupted(controlled):
    policy, _ = controlled
    run_id = "a" * 32
    active = {"schema_version": 1, "run_id": run_id, "decision_id": "decision-1",
              "source_topic": policy.source_topic, "status": "running", "phase": "fitting",
              "pid": 1, "hostname": "old", "updated_at": 99.0, "started_at": 90.0}
    completed = {**active, "status": "completed", "phase": "completed", "updated_at": 100.0}
    policy.path.with_name("training.json").write_text(json.dumps(active))
    policy.path.with_name("training_events.jsonl").write_text(json.dumps(completed) + "\n")

    with training_lease(policy) as allowed:
        assert allowed
        mark_training_fitting(policy)

    events = [json.loads(line) for line in
              policy.path.with_name("training_events.jsonl").read_text().splitlines()]
    assert [event["phase"] for event in events] == ["completed", "preparing", "fitting", "completed"]
    assert not any(event.get("reason") == "stale_state_after_lock_recovery" for event in events)


def test_scraper_pause_stop_closes_iterator_without_ack_checkpoint(controlled):
    from scraper.kafka_producer import publish_records
    policy, clock = controlled
    policy.path.unlink()
    clock.on_wait = lambda: setattr(clock, "stopped", True)
    state = {"checkpoint": False, "closed": False}

    def records():
        try:
            yield {"url": "https://example.test/listing"}
            state["checkpoint"] = True
        finally:
            state["closed"] = True

    producer = SimpleNamespace(produce=lambda *a, **k: pytest.fail("must not publish"), poll=lambda _: None)
    assert publish_records(producer, records(), policy.source_topic, 1, stop=clock,
                           gate=RateGate(policy, policy.source_topic, clock=clock)) == 0
    assert state == {"checkpoint": False, "closed": True}


def test_stress_pause_respects_run_duration_and_never_generates(controlled, tmp_path, monkeypatch):
    from agents import stress
    from agents.stress_metrics import StressMetrics
    policy, clock = controlled
    policy = replace(policy, source_topic="real_estate_stress_raw")
    policy.path.unlink()
    monkeypatch.setattr(stress.RuntimePolicy, "from_env", lambda: policy)
    monkeypatch.setattr(stress.time, "monotonic", clock)
    monkeypatch.setattr(stress, "RateGate", lambda p, t: RateGate(p, t, clock=clock))
    calls = []
    producer = SimpleNamespace(
        list_topics=lambda topic, timeout: SimpleNamespace(topics={topic: SimpleNamespace(error=None, partitions={0: None})}),
        poll=lambda _: calls.append(clock.now), flush=lambda _: 0,
        produce=lambda *a, **k: pytest.fail("must not publish"),
    )
    config = stress.StressConfig(enabled=True, state_root=str(tmp_path / "runs"), duration=1)
    report = stress.run_once(config, StressMetrics(), clock, lambda _: producer)
    assert report["status"] == "completed" and report["generated"] == 0
    assert clock.now == 101 and len(calls) >= 4


def test_stress_lease_expiry_stops_sends_but_keeps_polling_until_deadline(controlled, tmp_path, monkeypatch):
    from agents import stress
    from agents.stress_metrics import StressMetrics
    policy, clock = controlled
    write_policy(policy.path, expires_at=101)
    monkeypatch.setattr(stress.RuntimePolicy, "from_env", lambda: policy)
    monkeypatch.setattr(stress.time, "monotonic", clock)
    monkeypatch.setattr(stress, "RateGate", lambda p, t: RateGate(p, t, clock=clock))
    sends, polls = [], []

    def produce(*args, **kwargs):
        sends.append(clock.now)
        kwargs["callback"](None, SimpleNamespace(partition=lambda: 0))

    producer = SimpleNamespace(
        list_topics=lambda topic, timeout: SimpleNamespace(topics={topic: SimpleNamespace(error=None, partitions={0: None})}),
        poll=lambda _: polls.append(clock.now), flush=lambda _: 0, produce=produce,
    )
    report = stress.run_once(stress.StressConfig(enabled=True, state_root=str(tmp_path / "runs"), rate=2, duration=2),
                             StressMetrics(), clock, lambda _: producer)
    assert sends == [100, 100.5]
    assert report["status"] == "completed" and report["generated"] == report["delivered"] == 2
    assert clock.now == 102 and max(polls) >= 101.75


@pytest.mark.parametrize("enabled", [False, True])
def test_stress_slow_publish_does_not_catch_up_in_a_burst(controlled, tmp_path, monkeypatch, enabled):
    from agents import stress
    from agents.stress_metrics import StressMetrics
    policy, clock = controlled
    policy = replace(policy, enabled=enabled)
    monkeypatch.setattr(stress.RuntimePolicy, "from_env", lambda: policy)
    monkeypatch.setattr(stress.time, "monotonic", clock)
    monkeypatch.setattr(stress, "RateGate", lambda p, t: RateGate(p, t, clock=clock))
    sends = []

    def produce(*args, **kwargs):
        sends.append(clock.now)
        clock.now += 0.8
        kwargs["callback"](None, SimpleNamespace(partition=lambda: 0))

    producer = SimpleNamespace(
        list_topics=lambda topic, timeout: SimpleNamespace(topics={topic: SimpleNamespace(error=None, partitions={0: None})}),
        poll=lambda _: None, flush=lambda _: 0, produce=produce,
    )
    report = stress.run_once(stress.StressConfig(enabled=True, state_root=str(tmp_path / "runs"), rate=2, duration=5, max_records=3),
                             StressMetrics(), clock, lambda _: producer)
    assert report["delivered"] == 3
    assert sends == pytest.approx([100, 101.3, 102.6])


def test_crawler_cli_shares_rate_gate_between_sources(controlled, monkeypatch):
    from scraper import kafka_producer
    policy, clock = controlled
    policy = replace(policy, source_topic="real_estate_raw")
    write_policy(policy.path, source_topic=policy.source_topic)
    monkeypatch.setattr(kafka_producer.RuntimePolicy, "from_env", lambda: policy)
    monkeypatch.setattr(kafka_producer, "RateGate", lambda p, t: RateGate(p, t, clock=clock))
    monkeypatch.setattr(kafka_producer, "Event", lambda: clock)
    monkeypatch.setattr(kafka_producer, "iter_source_records", lambda source, *a, **k: iter([{"url": f"https://example.test/{source}"}]))
    sends = []

    def produce(*args, **kwargs):
        sends.append((kwargs["key"], clock.now))
        kwargs["callback"](None, None)

    producer = SimpleNamespace(produce=produce, poll=lambda _: None, flush=lambda _: 0)
    monkeypatch.setattr(kafka_producer, "Producer", lambda _: producer)
    assert kafka_producer.main(["--crawl-enabled", "--sources", "alonhadat,homedy", "--topic", policy.source_topic]) == 0
    assert sends == [("https://example.test/alonhadat", 100), ("https://example.test/homedy", 100.5)]


def test_auto_trainer_defers_before_mongo(controlled, monkeypatch):
    from scripts import auto_train
    policy, _ = controlled
    write_policy(policy.path, training_allowed=False)
    monkeypatch.setattr(auto_train.RuntimePolicy, "from_env", lambda: policy)
    monkeypatch.setattr(auto_train, "MongoClient", lambda *a, **k: pytest.fail("must not fetch data"))
    assert auto_train.run_trainer() is None


@pytest.mark.parametrize("enabled,revoked", [(False, False), (True, True), (True, False)])
def test_auto_trainer_rechecks_permission_after_data_load_and_preserves_legacy(controlled, monkeypatch, enabled, revoked):
    from scripts import auto_train
    policy, _ = controlled
    policy = replace(policy, enabled=enabled)
    monkeypatch.setattr(auto_train.RuntimePolicy, "from_env", lambda: policy)
    mongo = MagicMock()

    def find(*args):
        if revoked:
            write_policy(policy.path, training_allowed=False)
        return [{"price_vnd": 100}]

    mongo.__getitem__.return_value.__getitem__.return_value.find.side_effect = find
    monkeypatch.setattr(auto_train, "MongoClient", lambda *a, **k: mongo)
    model = MagicMock()
    model.train.return_value = SimpleNamespace(sample_count=1)
    monkeypatch.setattr(auto_train, "RealEstatePriceModel", lambda: model)
    monkeypatch.setattr(auto_train, "update_metrics_from_result", lambda _: None)
    if revoked:
        assert auto_train.run_trainer() is None
        model.train.assert_not_called()
        assert json.loads(policy.path.with_name("training.json").read_text())["status"] == "deferred"
    else:
        assert auto_train.run_trainer() is True
        model.train.assert_called_once()
        if enabled:
            state = json.loads(policy.path.with_name("training.json").read_text())
            events = [json.loads(line) for line in
                      policy.path.with_name("training_events.jsonl").read_text().splitlines()]
            assert state["status"] == "completed"
            assert [event["phase"] for event in events] == ["preparing", "fitting", "completed"]
        else:
            assert not policy.path.with_name("training.json").exists()
    mongo.close.assert_called_once()


@pytest.mark.parametrize("enabled,training_allowed,expected", [(True, False, 99), (True, True, 0), (False, False, 0)])
def test_training_cli_obeys_lease_and_keeps_disabled_behavior(controlled, monkeypatch, enabled, training_allowed, expected):
    from modeling import train_model
    policy, _ = controlled
    policy = replace(policy, enabled=enabled)
    write_policy(policy.path, training_allowed=training_allowed)
    monkeypatch.setattr(train_model.RuntimePolicy, "from_env", lambda: policy)
    monkeypatch.setattr(sys, "argv", ["train_model.py"])
    fit = MagicMock()
    monkeypatch.setattr(train_model, "_train", fit)
    assert train_model.main() == expected
    assert fit.call_count == int(expected == 0)


def test_training_disabled_does_not_create_control_directory(tmp_path):
    policy = RuntimePolicy(False, tmp_path / "absent" / "policy.json")
    with training_lease(policy) as allowed:
        assert allowed
    assert not policy.path.parent.exists()
