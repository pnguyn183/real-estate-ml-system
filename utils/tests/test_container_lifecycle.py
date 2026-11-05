"""Stateful Docker boundary tests: no Docker daemon or application workload is used."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from agents.container_lifecycle import ContainerLifecycleActuator, ContainerLifecycleConfig, MIB


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.on_sleep = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


class Docker:
    def __init__(self, root, clock):
        self.root, self.clock = root, clock
        self.rows, self.memory = {}, {}
        self.commands, self.mutations = [], []
        self.on_command = None
        self.fail_after_stop = False
        self.fail_after_start = False
        self.fail_inspect_after_mutation = False
        self.keep_running_on_stop = False
        self.keep_stopped_on_start = False
        self.start_health = "healthy"
        self.add("mongodb")
        self.add("prometheus")
        self.add("grafana")

    def add(self, service, *, running=True, health="healthy", memory_mb=100, dependencies=None):
        cid = f"{len(self.rows) + 1:064x}"
        labels = {"com.docker.compose.project": "test-stack", "com.docker.compose.service": service,
                  "com.docker.compose.config-hash": service + "-hash", "com.docker.compose.oneoff": "False"}
        if dependencies is not None:
            labels["com.docker.compose.depends_on"] = dependencies
        self.rows[cid] = {
            "Id": cid, "Name": "/test-" + service,
            "Config": {"Labels": labels, "Hostname": cid[:12],
                       "Env": ["CONTROL_ENABLED=true", "CONTROL_POLICY_PATH=/app/runtime/control/policy.json",
                               "CONTROL_SOURCE_TOPIC=real_estate_stress_raw", "SECRET_VALUE=must-never-leak"]},
            "Mounts": [{"Destination": "/app/runtime/control", "Source": str(self.root), "RW": True}],
            "State": {"Running": running, "Paused": False, "Restarting": False,
                      "Status": "running" if running else "exited", "ExitCode": 0,
                      "StartedAt": "2026-11-04T00:00:00Z", "FinishedAt": "0001-01-01T00:00:00Z" if running else "2026-11-04T00:01:00Z",
                      "Health": {"Status": health}}}
        self.memory[cid] = memory_mb
        return cid

    def row(self, service):
        return next(row for row in self.rows.values() if row["Config"]["Labels"]["com.docker.compose.service"] == service)

    def __call__(self, argv, **kwargs):
        assert argv[0] == "docker" and kwargs["check"]
        assert kwargs["timeout"] > 0
        self.commands.append(list(argv))
        if self.on_command:
            self.on_command(argv)
        verb = argv[1]
        if verb == "ps":
            assert "--all" in argv
            output = "\n".join(self.rows)
        elif verb == "inspect":
            if self.fail_inspect_after_mutation and self.mutations:
                raise subprocess.TimeoutExpired(argv, 15, stderr="must-never-leak")
            output = json.dumps([deepcopy(self.rows[cid]) for cid in argv[2:]])
        elif verb == "stats":
            targets = argv[argv.index("{{json .}}") + 1:]
            output = "\n".join(json.dumps({"ID": cid, "CPUPerc": "2.4%",
                "MemUsage": f"{self.memory[cid]}MiB / 1GiB"}) for cid in targets)
        elif verb in {"stop", "start"}:
            cid = argv[-1]
            # The plan must survive a process crash even before Docker responds.
            journal = json.loads((self.root / "containers.json").read_text())
            service = self.rows[cid]["Config"]["Labels"]["com.docker.compose.service"]
            assert journal["services"][service]["id"] == cid
            assert journal["services"][service]["phase"] == verb + "_requested"
            self.mutations.append((verb, cid))
            row = self.rows[cid]
            if verb == "stop" and not self.keep_running_on_stop:
                row["State"].update(Running=False, Status="exited", FinishedAt=f"2026-11-04T00:{len(self.mutations):02d}:30Z")
            if verb == "start" and not self.keep_stopped_on_start:
                row["State"].update(Running=True, Status="running", StartedAt=f"2026-11-04T01:{len(self.mutations):02d}:00Z")
                row["State"]["Health"] = {"Status": self.start_health}
            if verb == "stop" and self.fail_after_stop or verb == "start" and self.fail_after_start:
                raise subprocess.TimeoutExpired(argv, 15, stderr="must-never-leak")
            output = cid
        else:
            pytest.fail("Unexpected Docker operation: " + str(argv))
        return SimpleNamespace(stdout=output)


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    docker = Docker(tmp_path, clock)
    cfg = ContainerLifecycleConfig(project_name="test-stack", state_path=tmp_path / "containers.json",
        policy_path=tmp_path / "policy.json", enabled=True, allowed_services=("grafana",))
    actuator = ContainerLifecycleActuator(cfg, runner=docker, clock=clock, sleep=clock.sleep)
    return actuator, docker, clock


def sample(clock, **changes):
    result = {"timestamp": clock(), "instrumentation_ready": True, "errors": [],
              "source_timestamps": {"docker": clock(), "kafka": clock(), "workers": {"one": clock()}},
              "host": {"memory_bytes": 16 * 1024 * MIB, "memory_available_bytes": 8 * 1024 * MIB,
                       "memory_available_percent": 50.0},
              "vm_memory_available_percent": 50.0,
              "vm_memory": {"total_bytes": 8 * 1024 * MIB, "available_bytes": 4 * 1024 * MIB}}
    result.update(changes)
    return result


def action(rig, name="grafana", operation="stop", **kwargs):
    tool, _, clock = rig
    tool.observe()
    return tool.apply({name: operation}, dry_run=False, sample=sample(clock), decision_id="decision-1", **kwargs)


def recreate(rig):
    tool, docker, clock = rig
    return ContainerLifecycleActuator(tool.config, runner=docker, clock=clock, sleep=clock.sleep), docker, clock


def test_observation_contains_numeric_footprint_but_no_environment(rig):
    tool, docker, _ = rig
    result = tool.observe()
    assert result["status"] == "observed"
    assert result["services"]["grafana"]["memory_usage_bytes"] == 100 * MIB
    assert result["services"]["grafana"]["eligible_actions"] == ["stop"]
    assert "SECRET_VALUE" not in json.dumps(result) and "must-never-leak" not in json.dumps(result)
    assert not docker.mutations and not tool.config.state_path.exists()


def test_dry_run_has_no_files_and_no_docker_mutations(rig):
    tool, docker, clock = rig
    before = list(tool.config.state_path.parent.iterdir())
    tool.observe()
    result = tool.apply({"grafana": "stop"}, dry_run=True, sample=sample(clock), decision_id="dry")
    assert result["status"] == "dry_run"
    assert not docker.mutations and list(tool.config.state_path.parent.iterdir()) == before


@pytest.mark.parametrize("actions", [{"kafka": "stop"}, {"grafana": "pause"}, {"grafana": "rm"},
                                    {"grafana": "stop", "trainer": "stop"}])
def test_unapproved_or_multi_action_never_mutates(rig, actions):
    tool, docker, clock = rig
    result = tool.apply(actions, dry_run=False, sample=sample(clock), decision_id="invalid")
    assert result["status"] == "failed" and not docker.mutations


def test_disabled_lifecycle_cannot_mutate(rig):
    tool, docker, clock = rig
    tool = ContainerLifecycleActuator(replace(tool.config, enabled=False), docker, clock, clock.sleep)
    result = tool.apply({"grafana": "stop"}, dry_run=False, sample=sample(clock), decision_id="disabled")
    assert result["status"] == "failed" and not docker.mutations


def test_stop_journal_survives_new_controller_and_restores_same_id(rig):
    tool, docker, clock = rig
    stopped = action(rig)
    assert stopped["status"] == "applied" and stopped["owned_stop"]
    assert stopped["last_running_memory_bytes"] == 100 * MIB
    assert "freed_memory_bytes" not in stopped
    clock.now += 61
    resumed = action(recreate(rig), operation="start")
    assert resumed["status"] == "applied" and resumed["ready"] and not resumed["owned_stop"]
    assert docker.mutations == [("stop", docker.row("grafana")["Id"]), ("start", docker.row("grafana")["Id"])]
    audit = tool.config.state_path.with_name("lifecycle_actions.jsonl").read_text()
    assert len(audit.splitlines()) == 2 and "must-never-leak" not in audit


def test_initially_stopped_service_is_not_owned(rig):
    _, docker, _ = rig
    docker.row("grafana")["State"].update(Running=False, Status="exited")
    result = action(rig, operation="start")
    assert result["status"] == "deferred" and not docker.mutations


def test_cooldown_prevents_immediate_restore(rig):
    _, docker, clock = rig
    action(rig)
    clock.now += 5
    result = action(rig, operation="start")
    assert result["status"] == "deferred" and "cooldown" in result["error"].lower()
    assert len(docker.mutations) == 1


@pytest.mark.parametrize("field", ["Paused", "Restarting"])
def test_external_pause_and_restart_are_protected(rig, field):
    _, docker, _ = rig
    docker.row("grafana")["State"][field] = True
    assert action(rig)["status"] == "deferred" and not docker.mutations


def test_missing_identity_hash_is_protected(rig):
    _, docker, _ = rig
    del docker.row("grafana")["Config"]["Labels"]["com.docker.compose.config-hash"]
    assert action(rig)["status"] == "deferred" and not docker.mutations


def test_duplicate_service_inventory_is_rejected(rig):
    tool, docker, _ = rig
    docker.add("grafana")
    assert tool.observe()["status"] == "failed"
    assert action(rig)["status"] in {"failed", "deferred"} and not docker.mutations


def test_replaced_owned_container_is_never_started(rig):
    tool, docker, clock = rig
    action(rig)
    clock.now += 61
    old = docker.row("grafana")
    del docker.rows[old["Id"]]
    replacement = deepcopy(old)
    replacement["Id"] = "a" * 64
    docker.rows[replacement["Id"]] = replacement
    result = action(recreate(rig), operation="start")
    assert result["status"] in {"failed", "deferred"} and len(docker.mutations) == 1


def test_configuration_change_blocks_old_recovery(rig):
    _, docker, clock = rig
    action(rig)
    clock.now += 61
    docker.row("grafana")["Config"]["Labels"]["com.docker.compose.config-hash"] = "different"
    assert action(recreate(rig), operation="start")["status"] in {"failed", "deferred"}
    assert len(docker.mutations) == 1


def test_live_dependent_blocks_stop_and_preserves_service(rig):
    _, docker, _ = rig
    docker.add("dashboard-client", dependencies="grafana:service_started:false")
    result = action(rig)
    assert result["status"] == "deferred" and "depends" in result["error"]
    assert not docker.mutations


def test_dependency_must_be_healthy_before_restore(rig):
    _, docker, clock = rig
    action(rig)
    clock.now += 61
    docker.row("prometheus")["State"]["Health"]["Status"] = "unhealthy"
    assert action(rig, operation="start")["status"] == "deferred"
    assert len(docker.mutations) == 1


@pytest.mark.parametrize("changes", [
    {"timestamp": 900.0}, {"instrumentation_ready": False}, {"errors": ["lost-kafka"]},
    {"source_timestamps": {"docker": 950.0, "kafka": 1000.0, "workers": {"one": 1000.0}}},
    {"host": {"memory_available_percent": None}}, {"vm_memory_available_percent": float("nan")},
])
def test_missing_or_stale_telemetry_cannot_stop_container(rig, changes):
    tool, docker, clock = rig
    tool.observe()
    result = tool.apply({"grafana": "stop"}, dry_run=False, sample=sample(clock, **changes), decision_id="bad-sample")
    assert result["status"] == "deferred" and not docker.mutations


def test_emergency_memory_allows_shedding_but_not_restore(rig):
    tool, docker, clock = rig
    row = sample(clock)
    row["host"]["memory_available_percent"] = 3.0
    tool.observe()
    stopped = tool.apply({"grafana": "stop"}, dry_run=False, sample=row, decision_id="low-memory")
    assert stopped["status"] == "applied"
    clock.now += 61
    row["timestamp"] = clock()
    row["source_timestamps"] = sample(clock)["source_timestamps"]
    tool.observe()
    restored = tool.apply({"grafana": "start"}, dry_run=False, sample=row, decision_id="unsafe-restore")
    assert restored["status"] == "deferred" and len(docker.mutations) == 1


@pytest.mark.parametrize("domain", ["host", "vm_memory"])
def test_restore_keeps_byte_reserve_even_when_percent_looks_healthy(rig, domain):
    tool, docker, clock = rig
    action(rig)
    clock.now += 61
    row = sample(clock)
    key = "memory_available_bytes" if domain == "host" else "available_bytes"
    total_key = "memory_bytes" if domain == "host" else "total_bytes"
    row[domain][key] = int(row[domain][total_key] * .1) + 128 * MIB
    tool.observe()
    result = tool.apply({"grafana": "start"}, dry_run=False, sample=row, decision_id="headroom")
    assert result["status"] == "deferred" and len(docker.mutations) == 1


def test_restore_requires_byte_measurements(rig):
    tool, docker, clock = rig
    action(rig)
    clock.now += 61
    row = sample(clock)
    del row["host"]["memory_available_bytes"]
    tool.observe()
    result = tool.apply({"grafana": "start"}, dry_run=False, sample=row, decision_id="bytes-missing")
    assert result["status"] == "deferred" and len(docker.mutations) == 1


def test_command_timeout_after_success_is_reconciled_without_losing_ownership(rig):
    _, docker, clock = rig
    docker.fail_after_stop = True
    stopped = action(rig)
    assert stopped["status"] == "applied" and stopped["outcome_verified"]
    assert "TimeoutExpired" in stopped["command_error"] and "must-never-leak" not in json.dumps(stopped)
    clock.now += 61
    assert action(recreate(rig), operation="start")["status"] == "applied"


def test_failed_start_without_lifetime_change_remains_recoverable(rig):
    tool, docker, clock = rig
    assert action(rig)["status"] == "applied"
    clock.now += 61
    docker.keep_stopped_on_start = True
    docker.fail_after_start = True
    failed = action(rig, operation="start")
    assert failed["status"] == "failed" and failed["owned_stop"]
    entry = json.loads(tool.config.state_path.read_text())["services"]["grafana"]
    assert entry["phase"] == "stopped" and entry["owned_stop"]
    clock.now += 61
    docker.keep_stopped_on_start = docker.fail_after_start = False
    assert action(recreate(rig), operation="start")["status"] == "applied"


def test_start_timeout_after_success_does_not_retain_stale_restore_rights(rig):
    _, docker, clock = rig
    action(rig)
    clock.now += 61
    docker.fail_after_start = True
    result = action(rig, operation="start")
    assert result["status"] == "applied" and not result["owned_stop"]
    assert "TimeoutExpired" in result["command_error"]


def test_unknown_outcome_retains_journal_but_does_not_grant_unconfirmed_recovery(rig):
    tool, docker, clock = rig
    docker.fail_inspect_after_mutation = True
    result = action(rig)
    assert result["status"] == "failed"
    state = json.loads(tool.config.state_path.read_text())["services"]["grafana"]
    assert not state["owned_stop"] and state["phase"] == "stop_requested"
    docker.fail_inspect_after_mutation = False
    clock.now += 61
    assert action(recreate(rig), operation="start")["status"] in {"failed", "deferred"}
    assert len(docker.mutations) == 1


def test_stop_that_did_not_stop_is_not_claimed_success(rig):
    _, docker, _ = rig
    docker.keep_running_on_stop = True
    result = action(rig)
    assert result["status"] == "failed" and not result["outcome_verified"]


def test_health_starting_is_reported_as_not_ready(rig):
    _, docker, clock = rig
    action(rig)
    clock.now += 61
    docker.start_health = "starting"
    result = action(rig, operation="start")
    assert result["status"] == "applied" and not result["ready"]
    assert result["phase"] == "starting"
    assert not result["owned_stop"]


def test_external_start_then_stop_does_not_inherit_old_restore_permission(rig):
    tool, docker, clock = rig
    assert action(rig)["status"] == "applied"
    clock.now += 61
    docker.row("grafana")["State"].update(StartedAt="2026-11-04T05:00:00Z", FinishedAt="2026-11-04T05:01:00Z")
    view = tool.observe()
    assert "start" not in view["services"]["grafana"]["eligible_actions"]
    result = action(recreate(rig), operation="start")
    assert result["status"] in {"failed", "deferred"} and len(docker.mutations) == 1


def test_starting_health_does_not_grant_permission_for_later_external_stop(rig):
    tool, docker, clock = rig
    action(rig)
    clock.now += 61
    docker.start_health = "starting"
    action(rig, operation="start")
    docker.row("grafana")["State"]["Health"]["Status"] = "healthy"
    tool.observe()
    clock.now += 61
    docker.row("grafana")["State"].update(Running=False, Status="exited", FinishedAt="2026-11-04T06:00:00Z")
    assert action(recreate(rig), operation="start")["status"] in {"failed", "deferred"}
    assert len(docker.mutations) == 2


def test_model_cannot_act_on_replacement_it_never_observed(rig):
    tool, docker, clock = rig
    tool.observe()
    old = docker.row("grafana")
    replacement = deepcopy(old)
    replacement["Id"] = "b" * 64
    del docker.rows[old["Id"]]
    docker.rows[replacement["Id"]] = replacement
    docker.memory[replacement["Id"]] = 100
    result = tool.apply({"grafana": "stop"}, dry_run=False, sample=sample(clock), decision_id="stale-identity")
    assert result["status"] in {"failed", "deferred"} and not docker.mutations


def test_model_observation_has_its_own_deadline(rig):
    tool, docker, clock = rig
    tool.observe()
    clock.now += 31
    result = tool.apply({"grafana": "stop"}, dry_run=False, sample=sample(clock), decision_id="stale-view")
    assert result["status"] == "deferred" and not docker.mutations


def test_slow_final_discovery_cannot_bypass_sample_deadline(rig):
    tool, docker, clock = rig
    inspections = [0]
    def delay_final_discovery(argv):
        if argv[1] == "inspect":
            inspections[0] += 1
            if inspections[0] == 3:
                clock.now += 31
    docker.on_command = delay_final_discovery
    result = action(rig)
    assert result["status"] == "deferred" and not docker.mutations


def test_restart_during_final_discovery_invalidates_model_target(rig):
    _, docker, _ = rig
    inspections = [0]
    def restart_before_final_inspect(argv):
        if argv[1] == "inspect":
            inspections[0] += 1
            if inspections[0] == 3:
                docker.row("grafana")["State"]["StartedAt"] = "2026-11-04T08:00:00Z"
    docker.on_command = restart_before_final_inspect
    result = action(rig)
    assert result["status"] == "deferred" and not docker.mutations


def test_crash_before_command_never_claims_an_operator_stop(rig):
    tool, docker, clock = rig
    def crash(argv):
        if argv[1] == "stop":
            raise KeyboardInterrupt("simulated crash before Docker processes command")
    docker.on_command = crash
    with pytest.raises(KeyboardInterrupt):
        action(rig)
    assert not docker.mutations
    state = json.loads(tool.config.state_path.read_text())["services"]["grafana"]
    assert state["phase"] == "stop_requested" and not state["owned_stop"]
    docker.on_command = None
    docker.row("grafana")["State"].update(Running=False, Status="exited", FinishedAt="2026-11-04T07:00:00Z")
    clock.now += 61
    assert action(recreate(rig), operation="start")["status"] in {"failed", "deferred"}
    assert not docker.mutations


def test_corrupt_journal_blocks_actions_without_guessing_ownership(rig):
    tool, docker, _ = rig
    tool.config.state_path.write_text('{"schema_version": 1, "services": []}')
    assert tool.observe()["status"] == "failed"
    assert action(rig)["status"] in {"failed", "deferred"} and not docker.mutations


def trainer_rig(rig):
    tool, docker, clock = rig
    docker.add("trainer")
    cfg = replace(tool.config, allowed_services=("trainer",), drain_timeout_seconds=2)
    tool = ContainerLifecycleActuator(cfg, docker, clock, clock.sleep)
    cfg.policy_path.write_text(json.dumps({"training_allowed": False, "expires_at": clock() + 90,
                                         "source_topic": "real_estate_stress_raw"}))
    return tool, docker, clock


def acknowledge(rig, *, wrong_host=False, wrong_request=False, old_timestamp=False):
    tool, docker, clock = rig
    def ack():
        path = tool.config.policy_path.with_name("trainer-drain.json")
        if path.exists():
            request = json.loads(path.read_text())
            data = {"request_id": "wrong" if wrong_request else request["request_id"],
                    "hostname": "wrong" if wrong_host else docker.row("trainer")["Config"]["Hostname"],
                    "timestamp": clock() - 10 if old_timestamp else clock()}
            path.with_name("trainer-drain-ack.json").write_text(json.dumps(data))
    clock.on_sleep = ack


def test_trainer_without_ack_remains_running_and_request_is_cleared(rig):
    rig = trainer_rig(rig)
    tool, docker, _ = rig
    result = action(rig, name="trainer")
    assert result["status"] == "deferred" and not docker.mutations
    assert not tool.config.policy_path.with_name("trainer-drain.json").exists()


def test_trainer_requires_matching_idle_ack_before_stop(rig):
    rig = trainer_rig(rig)
    tool, docker, _ = rig
    acknowledge(rig)
    result = action(rig, name="trainer")
    assert result["status"] == "applied"
    assert docker.mutations == [("stop", docker.row("trainer")["Id"])]
    assert not tool.config.policy_path.with_name("trainer-drain.json").exists()


def test_trainer_drain_expiring_during_final_discovery_cannot_stop(rig):
    rig = trainer_rig(rig)
    tool, docker, clock = rig
    acknowledge(rig)
    def delay_after_ack(argv):
        if argv[1] == "ps" and tool.config.policy_path.with_name("trainer-drain.json").exists():
            clock.now += 23
    docker.on_command = delay_after_ack
    result = action(rig, name="trainer")
    assert result["status"] == "deferred" and not docker.mutations
    assert not tool.config.policy_path.with_name("trainer-drain.json").exists()


@pytest.mark.parametrize("wrong", ["wrong_host", "wrong_request", "old_timestamp"])
def test_invalid_trainer_ack_does_not_authorize_stop(rig, wrong):
    rig = trainer_rig(rig)
    acknowledge(rig, **{wrong: True})
    assert action(rig, name="trainer")["status"] == "deferred"
    assert not rig[1].mutations


def test_trainer_must_use_shared_control_mount_and_disabled_training(rig):
    rig = trainer_rig(rig)
    tool, docker, clock = rig
    tool.config.policy_path.write_text(json.dumps({"training_allowed": True, "expires_at": clock() + 90,
                                                 "source_topic": "real_estate_stress_raw"}))
    acknowledge(rig)
    assert action(rig, name="trainer")["status"] == "deferred"
    docker.row("trainer")["Mounts"][0]["RW"] = False
    assert action(rig, name="trainer")["status"] == "deferred" and not docker.mutations


def test_trainer_dry_run_never_creates_drain_request(rig):
    tool, docker, clock = trainer_rig(rig)
    tool.observe()
    result = tool.apply({"trainer": "stop"}, dry_run=True, sample=sample(clock), decision_id="drain-dry")
    assert result["status"] == "dry_run" and result["requires_trainer_drain"]
    assert not tool.config.policy_path.with_name("trainer-drain.json").exists()
    assert not docker.mutations


@pytest.mark.parametrize("field,value", [("allowed_services", ("kafka",)), ("allowed_services", ("grafana", "grafana")),
    ("min_memory_available_percent", 0), ("cooldown_seconds", float("inf")), ("drain_timeout_seconds", 1000),
    ("project_name", "bad project"), ("enabled", "true")])
def test_invalid_configuration_is_rejected(rig, field, value):
    with pytest.raises(ValueError):
        replace(rig[0].config, **{field: value})
