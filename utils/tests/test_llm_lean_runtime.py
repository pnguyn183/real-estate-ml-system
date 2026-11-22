import copy
import json

import pytest

from research import llm_lean_runtime as subject


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Docker:
    def __init__(self):
        self.calls = []
        self.fail_base_start = None
        self.base, self.lean = {}, {}
        for number, (service, state) in enumerate({
            "kafka": "running", "mongodb": "running", "processor": "running",
            "frontend": "running", "trainer": "exited",
        }.items(), 1):
            item = self._item(number, "base", service, state)
            self.base[item["Id"]] = item

    @staticmethod
    def _item(number, project, service, state, *, completed=False):
        identifier = f"{number:064x}"
        running = state == "running"
        return {
            "Id": identifier, "Name": f"/{project}-{service}", "Image": "sha256:image",
            "RestartCount": 0,
            "Config": {"Env": ["SECRET=never-store-me"], "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
                "com.docker.compose.config-hash": "hash-" + service,
                "com.docker.compose.oneoff": "False",
            }},
            "HostConfig": {"NanoCpus": 1_000_000_000, "CpuPeriod": 0, "CpuQuota": 0,
                           "Memory": 512 * 1024**2, "MemorySwap": 512 * 1024**2,
                           "RestartPolicy": {
                               "Name": "always" if project == "base" else "no",
                               "MaximumRetryCount": 0,
                           }},
            "State": {"Status": state, "Running": running, "Paused": False,
                      "ExitCode": 0 if completed else None,
                      "StartedAt": "started" if running else "",
                      "Health": {"Status": "healthy"} if running else None},
        }

    def containers(self):
        return {**self.base, **self.lean}

    def service(self, project, service):
        values = self.base if project == "base" else self.lean
        return next(item for item in values.values()
                    if item["Config"]["Labels"]["com.docker.compose.service"] == service)

    @staticmethod
    def set_state(item, value, *, completed=False):
        item["State"].update(Status=value, Running=value == "running", Paused=False,
                             ExitCode=0 if completed else None,
                             Health={"Status": "healthy"} if value == "running" else None)

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        if args[0] == "info":
            return json.dumps({"NCPU": 8, "MemTotal": 6 * 1024**3, "OSType": "linux"})
        if args[0] == "ps":
            project = args[-1].rsplit("=", 1)[1]
            values = self.base if project == "base" else self.lean
            return "\n".join(values)
        if args[0] == "inspect":
            values = self.containers()
            return json.dumps([values[value] for value in args[1:]])
        if args[0] == "compose":
            if "build" in args:
                return ""
            command = "create" if "create" in args else "up"
            if command == "create" and not self.lean:
                for index, service in enumerate(
                        ("zookeeper", "kafka", "mongodb", "processor", "kafka-init"), 100):
                    item = self._item(index, "lean", service, "created")
                    self.lean[item["Id"]] = item
            elif command == "up":
                for item in self.lean.values():
                    service = item["Config"]["Labels"]["com.docker.compose.service"]
                    self.set_state(item, "exited" if service == "kafka-init" else "running",
                                   completed=service == "kafka-init")
            return ""
        if args[0] == "update":
            identifier = args[-1]
            value = next(arg for arg in args if arg.startswith("--restart="))
            policy = value.split("=", 1)[1]
            name, _, retries = policy.partition(":")
            self.containers()[identifier]["HostConfig"]["RestartPolicy"] = {
                "Name": name, "MaximumRetryCount": int(retries or 0),
            }
            return identifier
        identifier = args[-1]
        item = self.containers()[identifier]
        service = item["Config"]["Labels"]["com.docker.compose.service"]
        if args[0] == "stop":
            self.set_state(item, "exited", completed=service == "kafka-init")
            return identifier
        if args[0] == "start":
            if service == self.fail_base_start:
                raise RuntimeError("start failed")
            self.set_state(item, "running")
            return identifier
        raise AssertionError(args)


@pytest.fixture
def configured(tmp_path):
    compose = tmp_path / "lean.yml"
    compose.write_text("services: {}", encoding="utf-8")
    (tmp_path / ".env").write_text("PRIVATE=never-store-me", encoding="utf-8")
    config = subject.LeanRuntimeConfig(
        base_project="base", lean_project="lean", compose_path=compose,
        project_directory=tmp_path, ready_timeout_seconds=20,
    )
    return Docker(), Clock(), config, tmp_path / "runtime.json"


def run_context(configured, **kwargs):
    docker, clock, config, manifest = configured
    return subject.lean_runtime(
        manifest, config, runner=docker, clock=clock, sleeper=clock.sleep,
        memory_reader=lambda: {"memory_available_percent": 50,
                               "memory_available_bytes": 8, "memory_total_bytes": 16},
        **kwargs,
    )


def test_lean_topology_stops_all_running_base_and_restores_exact_ids(configured):
    docker, _, _, manifest = configured
    original_ids = set(docker.base)
    original_limits = {key: copy.deepcopy(item["HostConfig"])
                       for key, item in docker.base.items()}
    with run_context(configured) as receipt:
        assert all(not item["State"]["Running"] for item in docker.base.values())
        assert subject.verify_runtime(receipt, runner=docker)["base_stopped"]
        assert len(receipt["lean_snapshot"]["fingerprint"]) == 64
        assert receipt["host_before_trial"]["memory_available_percent"] == 50
    assert set(docker.base) == original_ids
    assert all(docker.service("base", service)["State"]["Running"]
               for service in ("kafka", "mongodb", "processor", "frontend"))
    assert not docker.service("base", "trainer")["State"]["Running"]
    assert original_limits == {key: item["HostConfig"] for key, item in docker.base.items()}
    assert all(not item["State"]["Running"] for item in docker.lean.values())
    saved = manifest.read_text(encoding="utf-8")
    assert "never-store-me" not in saved and "PRIVATE" not in saved and "SECRET" not in saved
    receipt = json.loads(saved)
    assert receipt["status"] == "restored"
    assert receipt["build_attempted"] is receipt["build_completed"] is True


def test_body_failure_stops_lean_before_restoring_base(configured):
    docker, _, _, manifest = configured
    with pytest.raises(ValueError, match="body failed"):
        with run_context(configured):
            raise ValueError("body failed")
    assert all(not item["State"]["Running"] for item in docker.lean.values())
    assert docker.service("base", "kafka")["State"]["Running"]
    assert json.loads(manifest.read_text())["status"] == "restored_after_error"
    mutations = [(index, call[0], call[-1]) for index, call in enumerate(docker.calls)
                 if call[0] in {"stop", "start"}]
    last_lean_stop = max(index for index, command, identifier in mutations
                         if command == "stop" and identifier in docker.lean)
    first_base_start = min(index for index, command, identifier in mutations
                           if command == "start" and identifier in docker.base)
    assert last_lean_stop < first_base_start


def test_daemon_reclaim_disables_then_restores_restart_policies(configured):
    docker, _, _, manifest = configured
    snapshots = []

    def reclaim(*, runner, clock, sleeper):
        del clock, sleeper
        assert runner is docker
        snapshots.append({
            item["Id"]: copy.deepcopy(item["HostConfig"]["RestartPolicy"])
            for item in docker.base.values()
        })
        assert all(not item["State"]["Running"] for item in docker.base.values())

    with run_context(
            configured, reclaim_docker_daemon=True, daemon_reclaimer=reclaim) as receipt:
        assert receipt["daemon_reclaim_completed"] is True
        assert len(snapshots) == 1
        assert all(policy == {"Name": "no", "MaximumRetryCount": 0}
                   for policy in snapshots[0].values())
        assert all(item["HostConfig"]["RestartPolicy"]
                   == {"Name": "no", "MaximumRetryCount": 0}
                   for item in docker.base.values())
        assert subject.verify_runtime(receipt, runner=docker)["base_stopped"]

    assert all(item["HostConfig"]["RestartPolicy"]
               == {"Name": "always", "MaximumRetryCount": 0}
               for item in docker.base.values())
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["status"] == "restored"
    assert all(item["policy_restore_status"] == "restored"
               for item in saved["base_containers"])


def test_body_failure_after_daemon_reclaim_restores_policy_before_start(configured):
    docker, _, _, manifest = configured

    def reclaim(**_):
        return None

    with pytest.raises(ValueError, match="body failed"):
        with run_context(
                configured, reclaim_docker_daemon=True, daemon_reclaimer=reclaim):
            raise ValueError("body failed")

    assert all(item["HostConfig"]["RestartPolicy"]
               == {"Name": "always", "MaximumRetryCount": 0}
               for item in docker.base.values())
    policy_restores = [index for index, call in enumerate(docker.calls)
                       if call[0] == "update"
                       and call[1] == "--restart=always"]
    base_starts = [index for index, call in enumerate(docker.calls)
                   if call[0] == "start" and call[-1] in docker.base]
    assert policy_restores and base_starts
    assert max(policy_restores) < min(base_starts)
    assert json.loads(manifest.read_text())["status"] == "restored_after_error"


def test_failed_policy_restore_can_be_retried_from_manifest(configured):
    docker, _, _, manifest = configured
    fail_policy_restore = False
    base_runner = docker

    def runner(*args, timeout=30):
        if (fail_policy_restore and args[0] == "update"
                and args[1] == "--restart=always"):
            raise RuntimeError("policy restore failed")
        return base_runner(*args, timeout=timeout)

    def reclaim(**_):
        nonlocal fail_policy_restore
        fail_policy_restore = True

    with pytest.raises(RuntimeError, match="base_policy_restore"):
        with subject.lean_runtime(
                manifest, configured[2], runner=runner, clock=configured[1],
                sleeper=configured[1].sleep,
                memory_reader=lambda: {"memory_available_percent": 50,
                                       "memory_available_bytes": 8,
                                       "memory_total_bytes": 16},
                reclaim_docker_daemon=True, daemon_reclaimer=reclaim):
            pass

    assert all(item["HostConfig"]["RestartPolicy"]["Name"] == "no"
               for item in docker.base.values())
    fail_policy_restore = False
    result = subject.restore_lean_runtime(manifest, runner=runner)
    assert result["restore_status"] == "restored"
    assert all(item["HostConfig"]["RestartPolicy"]["Name"] == "always"
               for item in docker.base.values())


def test_failed_base_restore_can_be_retried_from_manifest(configured):
    docker, _, _, manifest = configured
    docker.fail_base_start = "kafka"
    with pytest.raises(RuntimeError, match="base_restore"):
        with run_context(configured):
            pass
    assert not docker.service("base", "kafka")["State"]["Running"]
    docker.fail_base_start = None
    result = subject.restore_lean_runtime(manifest, runner=docker)
    assert result["restore_status"] == "restored"
    assert docker.service("base", "kafka")["State"]["Running"]


def test_external_lean_mutation_fails_closed_before_base_restore(configured):
    docker, _, _, manifest = configured
    with pytest.raises(RuntimeError, match="lean_stop"):
        with run_context(configured):
            docker.service("lean", "kafka")["HostConfig"]["Memory"] += 1
    assert docker.service("lean", "kafka")["State"]["Running"]
    assert not docker.service("base", "kafka")["State"]["Running"]
    assert json.loads(manifest.read_text())["base_restore_deferred"] is True


def test_changed_topology_fingerprint_invalidates_trial(configured):
    docker, _, _, _ = configured
    with run_context(configured) as receipt:
        docker.service("lean", "processor")["RestartCount"] += 1
        with pytest.raises(RuntimeError, match="topology"):
            subject.verify_runtime(receipt, runner=docker)


@pytest.mark.parametrize("available", [0, 10, float("nan"), None])
def test_memory_gate_fails_before_yield_and_restores(configured, available):
    docker, clock, config, manifest = configured
    with pytest.raises(RuntimeError, match="safety floor"):
        with subject.lean_runtime(
                manifest, config, runner=docker, clock=clock, sleeper=clock.sleep,
                memory_wait_seconds=0,
                memory_reader=lambda: {"memory_available_percent": available}):
            pytest.fail("Unsafe host memory reached workload")
    assert docker.service("base", "kafka")["State"]["Running"]
    assert all(not item["State"]["Running"] for item in docker.lean.values())


def test_paused_base_fails_before_mutation(configured):
    docker, _, _, manifest = configured
    docker.service("base", "trainer")["State"].update(
        Status="paused", Running=True, Paused=True)
    with pytest.raises(RuntimeError, match="Paused base"):
        with run_context(configured):
            pass
    assert not manifest.exists()
    assert not [call for call in docker.calls if call[0] in {"stop", "start", "compose"}]


@pytest.mark.parametrize("change", ["identity", "project"])
def test_manifest_cannot_authorize_foreign_container(configured, change):
    docker, _, _, manifest = configured
    with run_context(configured):
        pass
    receipt = json.loads(manifest.read_text())
    receipt.pop("restore_status")
    if change == "identity":
        receipt["base_containers"][0]["identity"]["com.docker.compose.project"] = "other"
    else:
        receipt["config"]["base_project"] = "other"
    manifest.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="foreign"):
        subject.restore_lean_runtime(manifest, runner=docker)


@pytest.mark.parametrize("floor,wait", [(9, 0), (100, 0), (25, -1), (25, 301)])
def test_invalid_memory_gate_rejected_before_docker(configured, floor, wait):
    docker, clock, config, manifest = configured
    with pytest.raises(ValueError):
        with subject.lean_runtime(manifest, config, runner=docker, clock=clock,
                                  min_memory_available_percent=floor,
                                  memory_wait_seconds=wait):
            pass
    assert not docker.calls
