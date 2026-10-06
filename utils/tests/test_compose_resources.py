"""Offline resource transactions: Docker identity, budgets and rollback."""
import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from ruamel.yaml import YAML

from agents.compose_resources import ComposeResourceActuator, ComposeResourceConfig, MIB, NANO


COMPOSE = """# report settings must survive agent changes
services:
  processor:
    image: worker:local # retain comment
    environment:
      SECRET: ${SECRET:-not-a-real-key}
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1024m
    memswap_limit: 2048m
  trainer:
    image: trainer:local
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1024m
    memswap_limit: 2048m
  kafka:
    image: kafka:local
"""


class FakeDocker:
    def __init__(self):
        self.commands = []
        self.engine = {"NCPU": 8, "MemTotal": 8 * 1024 * MIB, "OSType": "linux",
                       "MemoryLimit": True, "SwapLimit": True, "CpuCfsQuota": True}
        self.containers = {
            service: {"Id": format(index, "064x"),
                      "State": {"Running": True, "Paused": False},
                      "Config": {"Labels": {"com.docker.compose.project": "real-estate",
                                            "com.docker.compose.service": service,
                                            "com.docker.compose.oneoff": "False"}},
                      "HostConfig": {"NanoCpus": NANO, "CpuPeriod": 0, "CpuQuota": 0,
                                     "Memory": 1024 * MIB, "MemorySwap": 2048 * MIB,
                                     "MemoryReservation": 0}}
            for index, service in enumerate(("processor", "trainer"), start=1)}
        self.original = copy.deepcopy(self.containers)
        self.usage = {service: 200 * MIB for service in self.containers}
        self.fail_update = None
        self.fail_restore = None
        self.ignore_updates = False
        self.invalid_compose = False
        self.on_validate = None
        self.after_update = None
        self.memory_reads = []
        self.cgroup_v1 = False

    def __call__(self, args, **kwargs):
        assert kwargs == {"check": True, "capture_output": True, "text": True, "timeout": 15.0}
        assert isinstance(args, list) and args[0] == "docker"
        self.commands.append(args)
        if args[1] == "info":
            return SimpleNamespace(stdout=json.dumps(self.engine))
        if args[1] == "ps":
            label = next(a for a in args if a.startswith("label=com.docker.compose.service="))
            service = label.split("=", 2)[-1]
            container = self.containers.get(service)
            return SimpleNamespace(stdout=container["Id"] if container and container["State"]["Running"] else "")
        if args[1] == "compose":
            assert args[-2:] == ["config", "--quiet"]
            assert "--project-directory" in args and "--project-name" in args
            assert Path(args[args.index("--file") + 1]).exists()
            if self.on_validate:
                self.on_validate()
            if self.invalid_compose:
                raise subprocess.CalledProcessError(1, args, output="SECRET leaked?", stderr="PRIVATE_TOKEN")
            return SimpleNamespace(stdout="")
        identifier = args[2] if args[1] in {"inspect", "exec"} else args[-1]
        service, target = next((k, v) for k, v in self.containers.items() if v["Id"] == identifier)
        if args[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps([target]))
        if args[1] == "exec":
            assert args[3] == "cat"
            if self.cgroup_v1 and args[4] == "/sys/fs/cgroup/memory.current":
                raise subprocess.CalledProcessError(1, args)
            self.memory_reads.append(service)
            return SimpleNamespace(stdout=str(self.usage[service]))
        assert args[1] == "update"
        nanos = round(float(args[args.index("--cpus") + 1]) * NANO)
        memory = int(args[args.index("--memory") + 1])
        swap = int(args[args.index("--memory-swap") + 1])
        if self.fail_restore == (service, nanos):
            raise subprocess.CalledProcessError(1, args)
        if not self.ignore_updates:
            target["HostConfig"].update(NanoCpus=nanos, Memory=memory, MemorySwap=swap)
        if self.after_update:
            self.after_update(service, nanos)
        if self.fail_update == (service, nanos):
            self.fail_update = None
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return SimpleNamespace(stdout=identifier)


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "docker-compose.yml"
    path.write_bytes(COMPOSE.encode())
    docker = FakeDocker()
    actuator = ComposeResourceActuator(ComposeResourceConfig(path, "real-estate", enabled=True), runner=docker)
    return path, docker, actuator


def requested(cpus=1.5, memory_mb=1536):
    return {"processor": {"cpus": cpus, "memory_mb": memory_mb}}


def mutations(docker):
    return [command for command in docker.commands if command[1] == "update"]


def test_dry_run_is_default_and_preserves_file_and_docker(setup):
    path, docker, actuator = setup
    receipt = actuator.apply(requested())
    assert receipt["status"] == "dry_run", receipt
    assert not mutations(docker)
    assert docker.containers == docker.original
    assert path.read_text() == COMPOSE
    assert list(path.parent.iterdir()) == [path]
    assert receipt["budgets"]["cpus"] == 6


def test_live_apply_persists_comments_swap_allowance_and_exact_ids(setup):
    path, docker, actuator = setup
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "applied", receipt
    assert receipt["runtime_changed"] and receipt["compose_changed"]
    assert Path(receipt["backup_path"]).read_bytes() == COMPOSE.encode()
    worker = docker.containers["processor"]["HostConfig"]
    assert worker["NanoCpus"] == 1.5 * NANO
    assert worker["Memory"] == 1536 * MIB
    assert worker["MemorySwap"] == (1536 + 1024) * MIB
    document = YAML().load(path.read_text())
    limits = document["services"]["processor"]["deploy"]["resources"]["limits"]
    assert float(limits["cpus"]) == 1.5
    assert int(limits["memory"]) == 1536 * MIB
    assert document["services"]["processor"]["memswap_limit"] == worker["MemorySwap"]
    assert "# report settings" in path.read_text() and "# retain comment" in path.read_text()
    assert "${SECRET:-not-a-real-key}" in path.read_text()
    assert docker.containers["trainer"] == docker.original["trainer"]
    assert all(command[-1] == docker.containers["processor"]["Id"] for command in mutations(docker))


def test_observe_reports_engine_capacity_raw_cgroup_usage_and_ids(setup):
    _, docker, actuator = setup
    docker.cgroup_v1 = True
    receipt = actuator.observe()
    assert receipt["status"] == "observed", receipt
    assert receipt["timestamp"] > 0 and receipt["engine"]["memory_bytes"] == 8 * 1024 * MIB
    assert receipt["services"]["processor"]["memory_usage_bytes"] == 200 * MIB
    assert receipt["services"]["processor"]["memory_headroom_bytes"] == 824 * MIB
    assert not mutations(docker)


@pytest.mark.parametrize("engine,proposal,error", [
    ({"NCPU": 2}, requested(cpus=1), "Total CPU"),
    ({"MemTotal": 3 * 1024 * MIB}, requested(memory_mb=1536), "Total RAM"),
    ({"OSType": "windows"}, requested(), "Linux"),
    ({"SwapLimit": False}, requested(), "support"),
    ({"MemoryLimit": False}, requested(), "support"),
    ({"CpuCfsQuota": False}, requested(), "support"),
])
def test_budget_counts_unchanged_services_and_uses_docker_capacity(setup, engine, proposal, error):
    path, docker, actuator = setup
    docker.engine.update(engine)
    receipt = actuator.apply(proposal, dry_run=False)
    assert receipt["status"] == "failed" and error in receipt["error"], receipt
    assert not mutations(docker) and path.read_text() == COMPOSE


@pytest.mark.parametrize("field,value", [
    ("NanoCpus", 0), ("CpuQuota", 1000), ("CpuPeriod", 100000),
    ("Memory", 0), ("MemorySwap", 0), ("MemorySwap", -2),
    ("MemorySwap", 100 * MIB), ("MemoryReservation", 2048 * MIB),
])
def test_unsupported_or_unrestorable_originals_never_mutate(setup, field, value):
    _, docker, actuator = setup
    docker.containers["processor"]["HostConfig"][field] = value
    assert actuator.apply(requested(), dry_run=False)["status"] == "failed"
    assert not mutations(docker)


def test_unlimited_swap_is_retained_explicitly(setup):
    path, docker, actuator = setup
    docker.containers["processor"]["HostConfig"]["MemorySwap"] = -1
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "applied", receipt
    assert docker.containers["processor"]["HostConfig"]["MemorySwap"] == -1
    assert YAML().load(path.read_text())["services"]["processor"]["memswap_limit"] == -1


@pytest.mark.parametrize("usage,memory", [(800, 800), (800, 900), (100, 200)])
def test_ram_reduction_requires_measured_headroom(setup, usage, memory):
    _, docker, actuator = setup
    docker.usage["processor"] = usage * MIB
    receipt = actuator.apply(requested(memory_mb=memory), dry_run=False)
    assert receipt["status"] == "failed", receipt
    assert not mutations(docker)


def test_ram_reduction_with_headroom_succeeds(setup):
    _, docker, actuator = setup
    receipt = actuator.apply(requested(memory_mb=512), dry_run=False)
    assert receipt["status"] == "applied", receipt
    assert docker.containers["processor"]["HostConfig"]["Memory"] == 512 * MIB


def test_usage_is_rechecked_after_compose_validation(setup):
    _, docker, actuator = setup
    docker.on_validate = lambda: docker.usage.update(processor=500 * MIB)
    receipt = actuator.apply(requested(memory_mb=512), dry_run=False)
    assert receipt["status"] == "failed" and "headroom" in receipt["error"], receipt
    assert not mutations(docker)


def test_timeout_after_mutation_restores_original_runtime_and_file(setup):
    path, docker, actuator = setup
    docker.fail_update = ("trainer", 2 * NANO)
    proposal = {**requested(), "trainer": {"cpus": 2, "memory_mb": 1536}}
    receipt = actuator.apply(proposal, dry_run=False)
    assert receipt["status"] == "failed", receipt
    assert receipt["rollback"]["status"] == "restored"
    assert docker.containers == docker.original and path.read_text() == COMPOSE
    assert not receipt["runtime_changed"] and not receipt["compose_changed"]


def test_failed_runtime_rollback_is_truthful(setup):
    path, docker, actuator = setup
    docker.fail_update = ("trainer", 2 * NANO)
    docker.fail_restore = ("processor", NANO)
    receipt = actuator.apply({**requested(), "trainer": {"cpus": 2, "memory_mb": 1536}}, dry_run=False)
    assert receipt["status"] == "failed" and receipt["rollback"]["status"] == "failed"
    assert receipt["rollback"]["outstanding_services"] == ["processor"]
    assert receipt["runtime_changed"] and not receipt["compose_changed"]
    assert path.read_text() == COMPOSE


def test_rollback_refuses_unsafe_memory_shrink(setup):
    _, docker, actuator = setup
    docker.fail_update = ("processor", round(1.5 * NANO))
    docker.after_update = lambda service, nanos: docker.usage.update(processor=1400 * MIB)
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["rollback"]["outstanding_services"] == ["processor"]
    assert "headroom" in receipt["rollback"]["failures"]["processor"]
    assert len(mutations(docker)) == 1


def test_verification_does_not_trust_zero_exit_code(setup):
    path, docker, actuator = setup
    docker.ignore_updates = True
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed" and "verification" in receipt["error"]
    assert receipt["rollback"]["status"] == "restored"
    assert docker.containers == docker.original and path.read_text() == COMPOSE


def test_compare_and_swap_preserves_edits_during_planning(setup):
    path, docker, actuator = setup
    edited = COMPOSE + "# edited by user\n"
    docker.on_validate = lambda: path.write_text(edited)
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed" and "concurrently" in receipt["error"]
    assert path.read_text() == edited and not mutations(docker)


def test_compare_and_swap_rolls_back_runtime_on_edit_after_update(setup):
    path, docker, actuator = setup
    edited = COMPOSE + "# edited by user\n"
    docker.after_update = lambda *_: path.write_text(edited)
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed" and "concurrently" in receipt["error"]
    assert receipt["rollback"]["status"] == "restored"
    assert docker.containers == docker.original and path.read_text() == edited


def test_failure_after_file_write_restores_file_and_runtime(setup, monkeypatch):
    path, docker, actuator = setup
    original_verify = actuator._verify
    calls = 0

    def verify(value):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise subprocess.TimeoutExpired("simulated", 1)
        return original_verify(value)

    # A command failure is normalized before reaching the transaction handler.
    from agents.compose_resources import ResourceError

    def checked_verify(value):
        try:
            return verify(value)
        except subprocess.TimeoutExpired:
            raise ResourceError("Post-write inspect failed")

    monkeypatch.setattr(actuator, "_verify", checked_verify)
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed", receipt
    assert receipt["rollback"]["compose"] == "restored"
    assert docker.containers == docker.original and path.read_text() == COMPOSE


def test_compose_validation_error_does_not_leak_credentials(setup):
    path, docker, actuator = setup
    docker.invalid_compose = True
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed"
    assert "SECRET" not in str(receipt) and "PRIVATE_TOKEN" not in str(receipt)
    assert not mutations(docker) and path.read_text() == COMPOSE


@pytest.mark.parametrize("label,value", [("com.docker.compose.project", "wrong-project"),
                                         ("com.docker.compose.service", "kafka"),
                                         ("com.docker.compose.oneoff", "True")])
def test_running_targets_must_match_project_and_service(setup, label, value):
    _, docker, actuator = setup
    docker.containers["processor"]["Config"]["Labels"][label] = value
    assert actuator.apply(requested(), dry_run=False)["status"] == "failed"
    assert not mutations(docker)


def test_stopped_service_is_not_started_or_recreated(setup):
    _, docker, actuator = setup
    docker.containers["processor"]["State"]["Running"] = False
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed" and "not running" in receipt["error"]
    assert not mutations(docker)


def test_allocation_changes_during_planning_block_apply(setup):
    _, docker, actuator = setup
    docker.on_validate = lambda: docker.containers["trainer"]["HostConfig"].update(NanoCpus=2 * NANO)
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed" and "changed during planning" in receipt["error"]
    assert not mutations(docker)


def test_existing_lock_is_not_overwritten(setup):
    path, docker, actuator = setup
    lock = path.with_name(path.name + ".resource-agent.lock")
    lock.write_text("another process")
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed" and "lock" in receipt["error"]
    assert lock.read_text() == "another process" and not docker.commands


def test_live_apply_requires_explicit_enable_even_with_dry_run_false(setup):
    path, docker, _ = setup
    actuator = ComposeResourceActuator(ComposeResourceConfig(path, "real-estate"), runner=docker)
    assert actuator.apply(requested(), dry_run=False)["status"] == "disabled"
    assert not docker.commands and path.read_text() == COMPOSE


@pytest.mark.parametrize("proposal", [
    {}, {"kafka": {"cpus": 1, "memory_mb": 512}},
    {"processor": {"cpus": 1, "memory_mb": 512, "command": "arbitrary"}},
    requested(cpus=float("nan")), requested(cpus=float("inf")), requested(cpus=True),
    requested(memory_mb=True), requested(memory_mb=512.5), requested(memory_mb="512"),
    requested(cpus=5), requested(memory_mb=5000), requested(cpus=0),
])
def test_model_cannot_escape_whitelist_or_numeric_bounds(setup, proposal):
    _, docker, actuator = setup
    assert actuator.apply(proposal, dry_run=False)["status"] == "failed"
    assert not docker.commands


@pytest.mark.parametrize("changes", [
    {"enabled": "false"}, {"project_name": "--all"}, {"allowed_services": ("kafka",)},
    {"allowed_services": ("processor", "processor")}, {"max_cpus": float("inf")},
    {"max_total_cpu_fraction": 1.1}, {"max_total_memory_fraction": 0},
    {"min_memory_mb": 2}, {"command_timeout_seconds": 0},
])
def test_operator_config_validates_bounds(tmp_path, changes):
    kwargs = {"compose_path": tmp_path / "docker-compose.yml", "project_name": "real-estate", **changes}
    with pytest.raises(ValueError):
        ComposeResourceConfig(**kwargs)


def test_compose_alias_cannot_edit_another_service(setup):
    path, docker, actuator = setup
    source = """services:
  processor: &shared
    image: worker:local
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1024m
  trainer: *shared
"""
    path.write_text(source)
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed" and "aliases" in receipt["error"]
    assert path.read_text() == source and not mutations(docker)


def test_rollback_preserves_concurrent_operator_allocation(setup):
    path, docker, actuator = setup

    def operator_change(service, nanos):
        docker.containers[service]["HostConfig"]["NanoCpus"] = 3 * NANO

    docker.after_update = operator_change
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed"
    assert receipt["rollback"]["status"] == "failed"
    assert receipt["rollback"]["outstanding_services"] == ["processor"]
    assert "concurrently" in receipt["rollback"]["failures"]["processor"]
    assert docker.containers["processor"]["HostConfig"]["NanoCpus"] == 3 * NANO
    assert len(mutations(docker)) == 1 and path.read_text() == COMPOSE


def test_final_verification_detects_changes_to_untouched_budget_member(setup):
    path, docker, actuator = setup

    def operator_change(service, nanos):
        docker.containers["trainer"]["HostConfig"]["NanoCpus"] = 3 * NANO

    docker.after_update = operator_change
    receipt = actuator.apply(requested(), dry_run=False)
    assert receipt["status"] == "failed" and "during apply" in receipt["error"]
    assert receipt["rollback"]["status"] == "restored"
    assert docker.containers["processor"] == docker.original["processor"]
    assert docker.containers["trainer"]["HostConfig"]["NanoCpus"] == 3 * NANO
    assert path.read_text() == COMPOSE


def test_reallocation_releases_cpu_and_ram_before_granting_them_elsewhere(setup):
    _, docker, actuator = setup
    allocations = []

    def measure(service, nanos):
        states = [c["HostConfig"] for c in docker.containers.values()]
        allocations.append((sum(c["NanoCpus"] for c in states), sum(c["Memory"] for c in states)))

    docker.after_update = measure
    # Both final totals equal the original. Each service exchanges one resource
    # for the other, so simply sorting complete updates cannot stay in budget.
    proposal = {"processor": {"cpus": 1.5, "memory_mb": 512},
                "trainer": {"cpus": .5, "memory_mb": 1536}}
    receipt = actuator.apply(proposal, dry_run=False)
    assert receipt["status"] == "applied", receipt
    assert len(mutations(docker)) == 4
    assert all(cpus <= 2 * NANO and memory <= 2048 * MIB for cpus, memory in allocations)


def test_mixed_reallocation_rollback_reverses_resource_grants_first(setup):
    path, docker, actuator = setup
    allocations = []

    def fail_last_grant(service, nanos):
        states = [c["HostConfig"] for c in docker.containers.values()]
        allocations.append((sum(c["NanoCpus"] for c in states), sum(c["Memory"] for c in states)))
        if (service == "trainer" and docker.containers[service]["HostConfig"]["Memory"] == 1536 * MIB):
            docker.fail_update = ("trainer", nanos)

    docker.after_update = fail_last_grant
    proposal = {"processor": {"cpus": 1.5, "memory_mb": 512},
                "trainer": {"cpus": .5, "memory_mb": 1536}}
    receipt = actuator.apply(proposal, dry_run=False)
    assert receipt["status"] == "failed", receipt
    assert receipt["rollback"]["status"] == "restored"
    assert docker.containers == docker.original and path.read_text() == COMPOSE
    assert all(cpus <= 2 * NANO and memory <= 2048 * MIB for cpus, memory in allocations)
