"""Model decisions drive real lease semantics; fake tools never touch Docker."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.control_agent import (ControlSettings, FeedbackAgent, capacity_evidence,
                                 control_session, controller_lock, observation_problem, verify_participants)
from agents.runtime_policy import RuntimePolicy
from agents.model_router import RoutedResponse
from agents.providers import ProviderError


class Clock:
    value = 1000.0
    def __call__(self):
        return self.value


def sample(now=1000.0, **fields):
    return dict(timestamp=now, incoming_rate=10.0, throughput=10.0,
                cpu_percent=50.0, ram_percent=50.0, kafka_lag=0,
                latency_p95_seconds=.3, error_rate=0.0,
                vm_memory_available_percent=45.0, instrumentation_ready=True, errors=[], **fields)


def decision(**fields):
    result = dict(state="normal", reason="Queues drained; admit a measured probe.",
                  rate_per_second=12.0, training_allowed=False, resources={})
    result.update(fields)
    return result


class Router:
    def __init__(self, output=None, effect=None):
        self.output = output if output is not None else decision()
        self.effect, self.calls = effect, []

    def extract_result(self, messages, timeout):
        self.calls.append((messages, timeout))
        if self.effect:
            self.effect()
        if isinstance(self.output, Exception):
            raise self.output
        return RoutedResponse(json.dumps(self.output), "fake", "test-model", 1)


class Resources:
    def __init__(self, *, enabled=False, status="applied", effect=None):
        self.config = SimpleNamespace(enabled=enabled)
        self.calls, self.status, self.effect = [], status, effect

    def observe(self):
        return {"status": "observed", "engine": {"cpus": 8, "memory_bytes": 8 * 1024**3}, "services": {}}

    def apply(self, limits, dry_run=True):
        self.calls.append((limits, dry_run))
        if self.effect:
            self.effect()
        return {"status": "dry_run" if dry_run else self.status, "error": "budget" if self.status == "failed" else None}


def setup(tmp_path, *, output=None, apply=True, enabled=False):
    clock = Clock()
    router, resources = Router(output), Resources(enabled=enabled)
    agent = FeedbackAgent(ControlSettings(), router, resources, tmp_path / "policy.json", apply=apply, clock=clock)
    reader = RuntimePolicy(True, agent.policy_path, "real_estate_stress_raw", wall_clock=clock)
    return agent, reader, router, resources, clock


@pytest.mark.parametrize("state,rate,training", [("light", 80.0, True), ("normal", 17.0, False), ("heavy", 0.0, False)])
def test_model_selects_different_actions_from_same_metrics(tmp_path, state, rate, training):
    agent, reader, router, tools, _ = setup(tmp_path, output=decision(state=state, rate_per_second=rate, training_allowed=training))
    receipt = agent.tick(sample())
    assert receipt["status"] == "applied"
    assert receipt["decision"]["state"] == state
    assert reader.read().rate_per_second == rate
    assert reader.read().training_allowed is training
    assert len(router.calls) == 1 and tools.calls == []


def test_dry_run_cannot_publish_or_actuate(tmp_path):
    output = decision(resources={"processor": {"cpus": 2.0, "memory_mb": 1024}})
    agent, _, _, resources, _ = setup(tmp_path, apply=False, enabled=True, output=output)
    assert agent.tick(sample())["status"] == "proposed"
    assert not agent.policy_path.exists()
    assert resources.calls[0][1] is True


@pytest.mark.parametrize("fields", [dict(rate_per_second=101), dict(rate_per_second=float("nan")),
    dict(training_allowed="true"), dict(state="unknown"), dict(extra="docker rm"),
    dict(resources={"kafka": {"cpus": 1.0, "memory_mb": 1024}}),
    dict(resources={"processor": {"cpus": "2", "memory_mb": 1024}})])
def test_invalid_model_action_pauses_without_resource_changes(tmp_path, fields):
    agent, reader, _, resources, _ = setup(tmp_path, enabled=True, output=decision(**fields))
    assert agent.tick(sample())["status"] == "paused"
    assert reader.read().rate_per_second == 0 and not reader.read().training_allowed
    assert resources.calls == []


def test_stale_telemetry_never_calls_llm(tmp_path):
    agent, reader, router, _, _ = setup(tmp_path)
    assert agent.tick(sample(now=900))["reason"] == "telemetry_stale"
    assert not router.calls and not reader.read().training_allowed
    assert list(agent.history) == []


def test_idle_null_latency_is_allowed_but_busy_missing_latency_is_not(tmp_path):
    cfg = ControlSettings()
    row = sample()
    row.update(throughput=0.0, incoming_rate=0.0, latency_p95_seconds=None, error_rate=None)
    assert observation_problem(row, 1000, cfg) is None
    row["throughput"] = 2.0
    assert observation_problem(row, 1000, cfg) == "telemetry_incomplete"


def test_old_source_timestamp_cannot_hide_behind_new_collection_end():
    row = sample(source_timestamps={"kafka": 900, "docker": 1000, "workers": {"one": 1000}})
    assert observation_problem(row, 1000, ControlSettings()) == "telemetry_sources_stale"


def test_memory_emergency_vetoes_model_call(tmp_path):
    agent, reader, router, _, _ = setup(tmp_path)
    row = sample(host={"memory_available_percent": 5})
    assert agent.tick(row)["reason"] == "emergency_host_memory"
    assert not router.calls and reader.read().rate_per_second == 0


def test_model_timeout_or_all_routes_unavailable_pauses(tmp_path):
    agent, reader, _, _, _ = setup(tmp_path, output=ProviderError("provider_cooldown"))
    assert agent.tick(sample())["reason"] == "decision_failed:provider_cooldown"
    assert reader.read().rate_per_second == 0


def test_result_that_outlives_telemetry_is_not_applied(tmp_path):
    agent, reader, router, tools, clock = setup(tmp_path)
    router.effect = lambda: setattr(clock, "value", 1040)
    assert agent.tick(sample())["reason"] == "observation_expired_during_decision"
    assert reader.read().rate_per_second == 0 and not tools.calls


@pytest.mark.parametrize("status", ["applied", "failed"])
def test_resources_confirmed_before_admission_is_granted(tmp_path, status):
    agent, reader, _, tools, _ = setup(tmp_path, enabled=True,
        output=decision(resources={"processor": {"cpus": 2.0, "memory_mb": 1024}}))
    tools.status = status
    paused_during_change = []
    tools.effect = lambda: paused_during_change.append(reader.read().rate_per_second)
    receipt = agent.tick(sample())
    assert paused_during_change == [0.0]
    assert receipt["status"] == ("applied" if status == "applied" else "paused")
    assert reader.read().rate_per_second == (12.0 if status == "applied" else 0.0)


def test_sampling_does_not_repeat_llm_until_decision_cadence(tmp_path):
    agent, _, router, _, clock = setup(tmp_path)
    agent.tick(sample())
    clock.value += 5
    assert agent.tick(sample(clock()))["status"] == "observed"
    clock.value += 30
    assert agent.tick(sample(clock()))["status"] == "applied"
    context = json.loads(router.calls[-1][0][-1]["content"])
    assert len(router.calls) == 2 and len(context["history"]) == 3
    assert context["recent_actions"] and not context["capacity_evidence"]["saturation_established"]


def test_expired_published_decision_defers_training(tmp_path):
    agent, reader, _, _, clock = setup(tmp_path, output=decision(training_allowed=True))
    agent.tick(sample())
    assert reader.read().training_allowed
    clock.value += 91
    assert not reader.read().valid and not reader.read().training_allowed


def test_failed_lock_acquisition_does_not_revoke_owner(tmp_path):
    agent, reader, _, _, _ = setup(tmp_path)
    agent.tick(sample())
    with controller_lock(tmp_path / "controller.lock"):
        with pytest.raises(OSError):
            with control_session(agent):
                pytest.fail("second controller acquired lock")
    assert reader.read().rate_per_second == 12.0
    with control_session(agent):
        pass
    assert reader.read().rate_per_second == 0.0


@pytest.mark.parametrize("value", [3601, float("inf"), 30])
def test_lease_configuration_matches_reader_contract(value):
    with pytest.raises(ValueError):
        ControlSettings(lease_seconds=value)


def test_idle_measurements_do_not_establish_capacity():
    evidence = capacity_evidence([sample(), sample()])
    assert evidence["mean_backlogged_throughput"] is None
    assert evidence["backlogged_samples"] == 0 and not evidence["saturation_established"]


def test_running_airflow_without_control_blocks_apply(tmp_path, monkeypatch):
    root = tmp_path / "runtime" / "control"
    def run(args):
        if "compose" in args:
            return args[-1]
        service = args[-1]
        env = ["CONTROL_ENABLED=true", "CONTROL_SOURCE_TOPIC=real_estate_stress_raw",
               "CONTROL_POLICY_PATH=/app/runtime/control/policy.json", "STRESS_ENABLED=true"]
        if service == "airflow":
            env[0] = "CONTROL_ENABLED=false"
        return json.dumps([{"Config": {"Env": env}, "Mounts": [{"Destination": "/app/runtime/control", "Source": str(root)}]}])
    monkeypatch.setattr("agents.control_agent.command", run)
    with pytest.raises(ValueError, match="airflow"):
        verify_participants(tmp_path / "docker-compose.yml", "test", ControlSettings(), root / "policy.json")
