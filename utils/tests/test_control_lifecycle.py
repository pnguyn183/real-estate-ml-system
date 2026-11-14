"""Decisions, safety guards and post-action evidence share one real policy lease."""
import json
from types import SimpleNamespace

import pytest

from utils.tests.test_control_agent import setup, sample, decision
from agents.control_agent import verify_participants, ControlSettings


class Lifecycle:
    def __init__(self, status="applied", services=None, effect=None):
        self.config = SimpleNamespace(enabled=True)
        self.status, self.effect = status, effect
        self.calls = []
        self.services = services or {"grafana": {"running": True, "ready": True}}

    def observe(self):
        return {"status": "observed", "timestamp": 1000.0, "services": self.services}

    def apply(self, actions, *, dry_run, sample, decision_id):
        self.calls.append((actions, dry_run, sample, decision_id))
        if self.effect:
            self.effect()
        return {"status": "dry_run" if dry_run else self.status, "actions": actions}


def test_stop_is_model_selected_and_waits_for_new_measurement_before_resume(tmp_path):
    agent, policy, router, _, clock = setup(tmp_path, output=decision(containers={"grafana": "stop"}))
    during_action = []
    agent.lifecycle = Lifecycle(effect=lambda: during_action.append(policy.read().rate_per_second))
    receipt = agent.tick(sample())
    assert during_action == [0.0]
    assert receipt["action_origin"] == "llm" and receipt["awaiting_fresh_telemetry"]
    assert policy.read().rate_per_second == 0
    assert agent.tick(sample())["reason"] == "awaiting_post_container_telemetry"
    assert len(router.calls) == 1
    clock.value += 5
    router.output = decision(rate_per_second=9.0)
    assert agent.tick(sample(clock()))["status"] == "applied"
    assert policy.read().rate_per_second == 9.0
    assert len(agent.lifecycle.calls) == 1
    context = json.loads(router.calls[-1][0][-1]["content"])
    assert context["container_changes_enabled"]
    assert context["recent_actions"][0]["containers"]["actions"] == {"grafana": "stop"}


def test_owned_stopped_service_is_an_explicit_recovery_goal(tmp_path):
    agent, _, router, _, _ = setup(tmp_path, output=decision(rate_per_second=3.0))
    agent.lifecycle = Lifecycle(services={"grafana": {
        "running": False, "ready": False, "owned_stop": True, "eligible_actions": ["start"]}})
    assert agent.tick(sample())["status"] == "applied"
    context = json.loads(router.calls[0][0][-1]["content"])
    assert context["goals"]["restore_controller_owned_optional_services"] is True
    assert context["goals"]["controller_owned_start_candidates"] == ["grafana"]


@pytest.mark.parametrize("where", ["host", "vm"])
def test_memory_emergency_keeps_model_stop_path_but_clamps_admission(tmp_path, where):
    agent, policy, router, _, _ = setup(tmp_path, output=decision(
        rate_per_second=50.0, training_allowed=True, containers={"grafana": "stop"}))
    agent.lifecycle = Lifecycle()
    row = sample(host={"memory_available_percent": 5 if where == "host" else 30})
    if where == "vm":
        row["vm_memory_available_percent"] = 5
    receipt = agent.tick(row)
    assert len(router.calls) == 1 and len(agent.lifecycle.calls) == 1
    assert policy.read().rate_per_second == 0 and not policy.read().training_allowed
    context = json.loads(router.calls[0][0][-1]["content"])
    assert context["emergency_memory"] == f"emergency_{where}_memory"
    assert receipt["effective_policy"] == {"rate_per_second": 0.0, "training_allowed": False}


def test_emergency_forbids_model_start(tmp_path):
    agent, policy, _, _, _ = setup(tmp_path, output=decision(containers={"grafana": "start"}))
    agent.lifecycle = Lifecycle()
    result = agent.tick(sample(host={"memory_available_percent": 4}))
    assert result["reason"] == "emergency_allows_only_container_stop"
    assert not agent.lifecycle.calls and policy.read().rate_per_second == 0


def test_stale_source_cannot_be_used_for_emergency_shedding(tmp_path):
    agent, _, router, _, _ = setup(tmp_path, output=decision(containers={"grafana": "stop"}))
    agent.lifecycle = Lifecycle()
    agent.tick(sample(host={"memory_available_percent": 4},
                      source_timestamps={"docker": 800, "kafka": 1000, "workers": {"one": 1000}}))
    assert not router.calls and not agent.lifecycle.calls


@pytest.mark.parametrize("status", ["failed", "deferred"])
def test_unconfirmed_action_keeps_admission_closed(tmp_path, status):
    agent, policy, _, _, _ = setup(tmp_path, output=decision(containers={"grafana": "stop"}))
    agent.lifecycle = Lifecycle(status=status)
    receipt = agent.tick(sample())
    assert receipt["reason"] == "container_action_not_confirmed"
    assert receipt["containers"]["status"] == status
    assert policy.read().rate_per_second == 0 and not policy.read().training_allowed
    assert (tmp_path / "container_failures.jsonl").exists()


def test_container_dry_run_does_not_publish_or_mark_actual_change(tmp_path):
    agent, _, _, _, _ = setup(tmp_path, apply=False, output=decision(containers={"grafana": "stop"}))
    agent.lifecycle = Lifecycle()
    receipt = agent.tick(sample())
    assert receipt["status"] == "proposed" and not receipt["awaiting_fresh_telemetry"]
    assert agent.lifecycle.calls[0][1] is True and not agent.policy_path.exists()
    assert agent.lifecycle_changed_at is None


@pytest.mark.parametrize("fields,reason", [
    ({"containers": {"grafana": "stop"}}, "container_changes_disabled"),
    ({"containers": {"grafana": "pause"}}, "decision_failed:ValidationError"),
    ({"containers": {"grafana": "stop", "trainer": "stop"}}, "decision_failed:ValidationError"),
])
def test_no_implicit_lifecycle_permission_or_unbounded_actions(tmp_path, fields, reason):
    agent, policy, _, _, _ = setup(tmp_path, output=decision(**fields))
    assert agent.tick(sample())["reason"] == reason
    assert policy.read().rate_per_second == 0


def test_resource_and_container_change_cannot_be_combined(tmp_path):
    agent, _, _, resources, _ = setup(tmp_path, enabled=True, output=decision(
        containers={"grafana": "stop"}, resources={"processor": {"cpus": 2.0, "memory_mb": 1024}}))
    agent.lifecycle = Lifecycle()
    assert agent.tick(sample())["reason"] == "mixed_resource_and_container_actions"
    assert not agent.lifecycle.calls and not resources.calls


def test_stopped_or_starting_trainer_cannot_receive_training_permission(tmp_path):
    agent, policy, _, _, _ = setup(tmp_path, output=decision(training_allowed=True))
    agent.lifecycle = Lifecycle(services={"trainer": {"running": True, "ready": False}})
    assert agent.tick(sample())["reason"] == "training_requires_ready_trainer"
    assert not policy.read().training_allowed


def test_post_action_source_data_must_also_be_new(tmp_path):
    agent, _, router, _, clock = setup(tmp_path, output=decision(containers={"grafana": "stop"}))
    agent.lifecycle = Lifecycle()
    agent.tick(sample())
    clock.value += 5
    row = sample(clock(), source_timestamps={"docker": 999, "kafka": 1005, "workers": {"one": 1005}})
    assert agent.tick(row)["reason"] == "awaiting_post_container_telemetry"
    assert len(router.calls) == 1


def test_optional_observation_failure_does_not_disable_healthy_pipeline_rate_control(tmp_path):
    agent, policy, router, _, _ = setup(tmp_path, output=decision(rate_per_second=7.0))
    agent.lifecycle = Lifecycle()
    agent.lifecycle.observe = lambda: {"status": "failed", "services": {}}
    assert agent.tick(sample())["status"] == "applied"
    assert policy.read().rate_per_second == 7.0
    context = json.loads(router.calls[0][0][-1]["content"])
    assert context["container_changes_enabled"] is False


def test_optional_observation_failure_does_not_allow_training(tmp_path):
    agent, policy, _, _, _ = setup(tmp_path, output=decision(training_allowed=True))
    agent.lifecycle = Lifecycle()
    agent.lifecycle.observe = lambda: {"status": "failed", "services": {}}
    assert agent.tick(sample())["reason"] == "training_requires_ready_trainer"
    assert not policy.read().training_allowed


def test_resource_observation_failure_allows_only_model_selected_shedding(tmp_path):
    agent, policy, router, resources, _ = setup(tmp_path, output=decision(containers={"grafana": "stop"}))
    resources.observe = lambda: {"status": "failed", "error": "cgroup read failed"}
    agent.lifecycle = Lifecycle()
    receipt = agent.tick(sample())
    assert receipt["status"] == "applied" and len(agent.lifecycle.calls) == 1
    assert policy.read().rate_per_second == 0
    context = json.loads(router.calls[0][0][-1]["content"])
    assert context["recovery_only"] is True and context["resource_changes_enabled"] is False


def test_resource_observation_failure_rejects_restart(tmp_path):
    agent, policy, _, resources, _ = setup(tmp_path, output=decision(containers={"grafana": "start"}))
    resources.observe = lambda: {"status": "failed"}
    agent.lifecycle = Lifecycle()
    assert agent.tick(sample())["reason"] == "emergency_allows_only_container_stop"
    assert not agent.lifecycle.calls and policy.read().rate_per_second == 0


def test_restart_controller_accepts_only_its_own_stopped_trainer(tmp_path, monkeypatch):
    root = tmp_path / "runtime" / "control"
    def run(args):
        if "compose" in args:
            return "producer-id" if args[-1] == "stress-agent" else ""
        return json.dumps([{"Config": {"Env": ["CONTROL_ENABLED=true",
            "CONTROL_SOURCE_TOPIC=real_estate_stress_raw", "CONTROL_POLICY_PATH=/app/runtime/control/policy.json",
            "STRESS_ENABLED=true"]}, "Mounts": [{"Destination": "/app/runtime/control", "Source": str(root)}]}])
    monkeypatch.setattr("agents.control_agent.command", run)
    lifecycle = Lifecycle(services={"trainer": {"id": "owned-trainer-id", "owned_stop": True}})
    verify_participants(tmp_path / "docker-compose.yml", "test", ControlSettings(), root / "policy.json", lifecycle)
    lifecycle.services["trainer"]["owned_stop"] = False
    with pytest.raises(ValueError, match="trainer"):
        verify_participants(tmp_path / "docker-compose.yml", "test", ControlSettings(), root / "policy.json", lifecycle)
