"""Exercise verified CPU updates, rollback, and real Docker no-op semantics."""
import copy
import json
import subprocess
from types import SimpleNamespace

import pytest

from agents.resource_actuator import DockerResourceActuator, ResourceScalingConfig


class FakeDocker:
    def __init__(self, cpus=(2, 3), engine_cpus=8):
        self.commands = []
        self.fail_update = None
        self.fail_restore = None
        self.ignore_updates = False
        self.engine_cpus = engine_cpus
        self.containers = {
            f"worker-{index}": {"Id": f"immutable-{index}", "State": {"Running": True},
                               "HostConfig": {"NanoCpus": round(cpu * 1e9), "CpuPeriod": 0, "CpuQuota": 0,
                                              "Memory": (index + 1) * 1024 ** 3, "MemorySwap": -1}}
            for index, cpu in enumerate(cpus)
        }
        self.original = copy.deepcopy(self.containers)

    def __call__(self, args, **kwargs):
        assert kwargs["check"] and 0 < kwargs["timeout"] <= 15
        self.commands.append(args)
        if args[1] == "info":
            return SimpleNamespace(stdout=str(self.engine_cpus))
        target = next((v for k, v in self.containers.items() if k == args[-1] or v["Id"] == args[-1]), None)
        assert target is not None
        if args[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps([target]))
        assert args[1:3] == ["update", "--cpus"] and len(args) == 5
        nanos = round(float(args[3]) * 1e9)
        if self.fail_update == (target["Id"], nanos):
            self.fail_update = None
            raise subprocess.CalledProcessError(1, args)
        if self.fail_restore == (target["Id"], nanos):
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        # Real Moby ignores a zero NanoCPUs update, so the fake must also.
        if nanos and not self.ignore_updates:
            target["HostConfig"]["NanoCpus"] = nanos
        return SimpleNamespace(stdout=target["Id"])


def config(**changes):
    return ResourceScalingConfig(enabled=True, containers=("worker-0", "worker-1"),
                                 initial_cpus=.5, min_cpus=.5, max_cpus=2, cpu_step=.5, **changes)


def decision(timestamp=100, reason="congestion:kafka_lag", **changes):
    observation = dict(timestamp=timestamp, incoming_rate=600, throughput=300,
                       cpu_percent=40, ram_percent=40, kafka_lag=1000,
                       latency_p95_seconds=3, error_rate=0)
    observation.update(changes)
    return SimpleNamespace(action="decrease", reason=reason, timestamp=timestamp,
                           observation=observation)


def test_equal_initial_budget_verified_cpu_only_increase_and_exact_restore():
    docker = FakeDocker()
    actuator = DockerResourceActuator(config(), runner=docker)
    assert actuator.apply_for_decision(decision())["status"] == "failed"
    assert not docker.commands
    prepared = actuator.prepare()
    assert prepared["status"] == "applied" and prepared["cpus"] == .5
    assert prepared["original_allocations"]["worker-0"]["NanoCpus"] == 2e9
    for value in docker.containers.values():
        assert value["HostConfig"]["NanoCpus"] == .5e9
    assert actuator.apply_for_decision(decision(reason="fixed_limit_baseline"))["status"] == "no_change"
    applied = actuator.apply_for_decision(decision())
    assert applied["status"] == "applied" and applied["action"] == "scale_up"
    assert applied["cpus"] == 1 and not applied["memory_changed"]
    assert set(applied["verified_allocations"]) == {"worker-0", "worker-1"}
    assert applied["actuation_latency_seconds"] >= 0
    assert actuator.apply_for_decision(decision(timestamp=110))["reason"] == "resource_cooldown"
    # Admission increase must not remove CPU and re-create congestion.
    assert actuator.apply_for_decision(decision(reason="safe_additive_increase"))["status"] == "no_change"
    assert actuator.restore()["status"] == "restored"
    assert docker.containers == docker.original
    command_count = len(docker.commands)
    assert actuator.restore()["status"] == "restored" and len(docker.commands) == command_count
    assert all(command[-1].startswith("immutable-") for command in docker.commands if command[1] == "update")


def test_partial_update_failure_restores_all_original_allocations():
    docker = FakeDocker()
    actuator = DockerResourceActuator(config(), runner=docker)
    assert actuator.prepare()["status"] == "applied"
    docker.fail_update = ("immutable-1", int(1e9))
    result = actuator.apply_for_decision(decision())
    assert result["status"] == "failed" and result["rollback"]["status"] == "restored"
    assert docker.containers == docker.original
    assert actuator.apply_for_decision(decision())["status"] == "failed"


def test_partial_prepare_failure_does_not_leave_the_first_worker_limited():
    docker = FakeDocker()
    docker.fail_update = ("immutable-1", int(.5e9))
    actuator = DockerResourceActuator(config(), runner=docker)
    result = actuator.prepare()
    assert result["status"] == "failed"
    assert result["rollback"]["status"] == "restored" and docker.containers == docker.original


def test_restore_failure_is_reported_and_retryable():
    docker = FakeDocker()
    actuator = DockerResourceActuator(config(), runner=docker)
    actuator.prepare()
    docker.fail_restore = ("immutable-0", int(2e9))
    receipt = actuator.restore()
    assert receipt["status"] == "failed" and receipt["outstanding_containers"] == ["worker-0"]
    docker.fail_restore = None
    assert actuator.restore()["status"] == "restored" and docker.containers == docker.original


@pytest.mark.parametrize("field,value", [("NanoCpus", 0), ("CpuPeriod", 100000), ("CpuQuota", 50000)])
def test_unrestorable_originals_fail_before_any_mutation(field, value):
    docker = FakeDocker()
    docker.containers["worker-1"]["HostConfig"][field] = value
    actuator = DockerResourceActuator(config(), runner=docker)
    assert actuator.prepare()["status"] == "failed"
    assert not any(command[1] == "update" for command in docker.commands)


def test_success_exit_code_without_matching_inspect_is_failure():
    docker = FakeDocker()
    docker.ignore_updates = True
    actuator = DockerResourceActuator(config(), runner=docker)
    result = actuator.prepare()
    assert result["status"] == "failed" and "verification" in result["error"]
    assert docker.containers == docker.original


def test_budget_bounded_by_actual_engine_capacity():
    docker = FakeDocker(engine_cpus=2)
    actuator = DockerResourceActuator(config(max_total_cpu_fraction=.6), runner=docker)
    assert actuator.prepare()["effective_max_cpus"] == .6
    assert actuator.apply_for_decision(decision())["cpus"] == .6
    assert actuator.apply_for_decision(decision(timestamp=140))["reason"] == "maximum_cpu_budget"
    actuator.restore()
    docker = FakeDocker(engine_cpus=1)
    actuator = DockerResourceActuator(config(max_total_cpu_fraction=.6), runner=docker)
    assert actuator.prepare()["status"] == "failed"
    assert not any(command[1] == "update" for command in docker.commands)


@pytest.mark.parametrize("reason,changes", [
    ("telemetry_missing_or_invalid:cpu_percent", {}),
    ("telemetry_stale", {}),
    ("congestion:kafka_lag", {"throughput": None}),
    ("congestion:kafka_lag", {"cpu_percent": float("nan")}),
    ("congestion:kafka_lag", {"cpu_percent": 101}),
    ("congestion:ram_percent", {}),
    ("congestion:error_rate", {}),
])
def test_missing_invalid_or_unrelated_pressure_cannot_scale_cpu(reason, changes):
    docker = FakeDocker()
    actuator = DockerResourceActuator(config(), runner=docker)
    actuator.prepare()
    old_count = len(docker.commands)
    receipt = actuator.apply_for_decision(decision(reason=reason, **changes))
    assert receipt["status"] == "no_change" and len(docker.commands) == old_count
    actuator.restore()


def test_stale_measurements_cannot_scale_cpu():
    docker = FakeDocker()
    actuator = DockerResourceActuator(config(), runner=docker)
    actuator.prepare()
    stale = decision()
    stale.observation["timestamp"] = 10
    assert actuator.apply_for_decision(stale)["reason"] == "telemetry_stale"
    actuator.restore()


def test_disabled_actuator_never_contacts_docker():
    def no_calls(*args, **kwargs):
        pytest.fail("disabled actuator called Docker")
    actuator = DockerResourceActuator(ResourceScalingConfig(), runner=no_calls)
    assert actuator.prepare()["status"] == "disabled"
    assert actuator.apply_for_decision(decision())["status"] == "disabled"
    assert actuator.restore()["status"] == "disabled"


@pytest.mark.parametrize("changes", [
    {"initial_cpus": 5, "max_cpus": 4}, {"enabled": "false"},
    {"min_cpus": -.5}, {"cpu_step": float("nan")}, {"max_cpus": float("inf")},
    {"containers": ["worker-0", "worker-0"]}, {"containers": "--all"},
    {"containers": ["--all"]}, {"max_total_cpu_fraction": 1.1},
    {"max_total_cpu_fraction": True}, {"command_timeout_seconds": 0},
])
def test_invalid_bounds_and_boolean_coercion_are_rejected(changes):
    with pytest.raises((TypeError, ValueError)):
        ResourceScalingConfig.from_mapping(changes)
