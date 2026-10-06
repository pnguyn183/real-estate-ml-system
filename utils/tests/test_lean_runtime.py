import copy
import json
import subprocess

import pytest

from research import lean_runtime


class FakeDocker:
    def __init__(self, services):
        self.containers = {}
        self.calls = []
        self.fail_after_stop = None
        self.fail_before_stop = None
        self.fail_start = None
        for service, state in services.items():
            container_id = service + "-original-id"
            self.containers[container_id] = {
                "Id": container_id, "Name": "/project-" + service,
                "Config": {"Env": ["API_KEY=do-not-persist"], "Labels": {
                    "com.docker.compose.project": "project",
                    "com.docker.compose.service": service,
                    "com.docker.compose.config-hash": "initial-config",
                }},
                "State": {"Status": state, "Running": state in {"running", "paused"},
                          "Paused": state == "paused"},
            }

    def __call__(self, *arguments, timeout=30):
        self.calls.append((arguments, timeout))
        if arguments[:2] == ("compose", "ps"):
            return "\n".join(json.dumps({
                "ID": key, "Service": item["Config"]["Labels"]["com.docker.compose.service"],
                "State": item["State"]["Status"],
            }) for key, item in self.containers.items())
        container_id = arguments[-1]
        if container_id not in self.containers:
            raise RuntimeError("Container missing")
        item = self.containers[container_id]
        if arguments[0] == "inspect":
            return json.dumps([item])
        if arguments[0] == "stop":
            if container_id == self.fail_before_stop:
                raise RuntimeError("Docker stop failed")
            item["State"] = {"Status": "exited", "Running": False, "Paused": False}
            if container_id == self.fail_after_stop:
                raise RuntimeError("Docker stop timed out after container exited")
            return container_id
        if arguments[0] == "start":
            if container_id == self.fail_start:
                raise RuntimeError("Docker start failed")
            item["State"] = {"Status": "running", "Running": True, "Paused": False}
            return container_id
        raise AssertionError(arguments)

    def state(self, service):
        return self.containers[service + "-original-id"]["State"]["Status"]

    def mutations(self, command):
        return [arguments for arguments, _ in self.calls if arguments[0] == command]


def test_disabled_does_not_query_docker_or_create_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(lean_runtime, "_docker", lambda *a, **kw: pytest.fail("Docker queried"))
    receipt_path = tmp_path / "lean.json"
    with lean_runtime.lean_environment(False, receipt_path) as receipt:
        assert receipt == {"enabled": False}
    assert not receipt_path.exists()


def test_only_running_optional_containers_are_stopped_then_restored(monkeypatch, tmp_path):
    docker = FakeDocker({"airflow": "running", "trainer": "exited", "scraper": "paused",
                         "kafka": "running", "kafka2": "running", "kafka3": "running",
                         "mongodb": "running", "processor": "running", "prometheus": "running"})
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    path = tmp_path / "lean.json"
    with lean_runtime.lean_environment(True, path) as receipt:
        assert docker.state("airflow") == "exited"
        assert docker.state("trainer") == "exited"
        assert docker.state("scraper") == "paused"
        assert receipt["status"] == "active"
        assert json.loads(path.read_text())["status"] == "active"
        assert len(receipt["containers"]) == 3
    assert docker.state("airflow") == "running"
    assert docker.mutations("stop") == [("stop", "--time", "30", "airflow-original-id")]
    assert docker.mutations("start") == [("start", "airflow-original-id")]
    serialized = path.read_text()
    assert "API_KEY" not in serialized and "do-not-persist" not in serialized
    assert json.loads(serialized)["status"] == "restored"


@pytest.mark.parametrize("after_stop", [False, True])
def test_partial_stop_failure_restores_changed_containers(monkeypatch, tmp_path, after_stop):
    docker = FakeDocker({"airflow": "running", "trainer": "running", "scraper": "running"})
    setattr(docker, "fail_after_stop" if after_stop else "fail_before_stop", "trainer-original-id")
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    path = tmp_path / "lean.json"
    with pytest.raises(RuntimeError, match="Docker stop"):
        with lean_runtime.lean_environment(True, path):
            pytest.fail("Session started despite incomplete lean transition")
    assert all(docker.state(service) == "running" for service in ("airflow", "trainer", "scraper"))
    assert ("start", "scraper-original-id") not in docker.mutations("start")
    assert (("start", "trainer-original-id") in docker.mutations("start")) is after_stop
    assert json.loads(path.read_text())["status"] == "restored_after_error"


def test_body_failure_restores_original_services(monkeypatch, tmp_path):
    docker = FakeDocker({"airflow": "running"})
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    path = tmp_path / "lean.json"
    with pytest.raises(ValueError, match="collection failed"):
        with lean_runtime.lean_environment(True, path):
            raise ValueError("collection failed")
    assert docker.state("airflow") == "running"
    assert json.loads(path.read_text())["failure_type"] == "ValueError"


@pytest.mark.parametrize("change", ["missing", "replaced", "config", "paused"])
def test_restore_rejects_changed_or_missing_container(monkeypatch, tmp_path, change):
    docker = FakeDocker({"airflow": "running", "trainer": "running"})
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    path = tmp_path / "lean.json"
    with pytest.raises(RuntimeError, match="could not restore: airflow"):
        with lean_runtime.lean_environment(True, path):
            item = docker.containers["airflow-original-id"]
            if change == "missing":
                del docker.containers["airflow-original-id"]
            elif change == "replaced":
                replacement = copy.deepcopy(item)
                replacement["Id"] = "replacement-id"
                docker.containers["airflow-original-id"] = replacement
            elif change == "config":
                item["Config"]["Labels"]["com.docker.compose.config-hash"] = "changed"
            else:
                item["State"] = {"Status": "paused", "Running": True, "Paused": True}
    assert docker.state("trainer") == "running"
    assert ("start", "airflow-original-id") not in docker.mutations("start")
    receipt = json.loads(path.read_text())
    assert receipt["status"] == "restore_failed"
    assert receipt["restore_errors"][0]["service"] == "airflow"


def test_failed_restore_still_restores_other_services(monkeypatch, tmp_path):
    docker = FakeDocker({"airflow": "running", "trainer": "running"})
    docker.fail_start = "trainer-original-id"
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    with pytest.raises(RuntimeError, match="could not restore: trainer"):
        with lean_runtime.lean_environment(True, tmp_path / "lean.json"):
            pass
    assert docker.state("airflow") == "running"


def test_restoration_does_not_restart_container_already_started_by_user(monkeypatch, tmp_path):
    docker = FakeDocker({"airflow": "running"})
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    with lean_runtime.lean_environment(True, tmp_path / "lean.json") as receipt:
        docker.containers["airflow-original-id"]["State"] = {
            "Status": "running", "Running": True, "Paused": False,
        }
    assert not docker.mutations("start")
    assert receipt["containers"][0]["restore_status"] == "already_running"


def test_plan_is_persisted_before_any_stop(monkeypatch, tmp_path):
    docker = FakeDocker({"airflow": "running"})
    path = tmp_path / "lean.json"
    def checked(*args, **kwargs):
        if args[0] == "stop":
            saved = json.loads(path.read_text())
            assert saved["containers"][0]["id"] == "airflow-original-id"
            assert saved["containers"][0]["stop_attempted"] is True
        return docker(*args, **kwargs)
    monkeypatch.setattr(lean_runtime, "_docker", checked)
    with lean_runtime.lean_environment(True, path):
        pass


def test_docker_errors_are_bounded_and_do_not_echo_sensitive_output(monkeypatch):
    def failed(*args, **kwargs):
        assert kwargs["timeout"] == 45
        return subprocess.CompletedProcess(args[0], 1, "API_KEY=secret", "token=secret")
    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(RuntimeError, match=r"^Docker stop failed \(exit 1\)$"):
        lean_runtime._docker("stop", "some-id", timeout=45)


def test_no_optional_services_still_yields_and_records_success(monkeypatch, tmp_path):
    docker = FakeDocker({"kafka": "running"})
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    with lean_runtime.lean_environment(True, tmp_path / "lean.json") as receipt:
        assert receipt["containers"] == []
    assert receipt["status"] == "restored"
    assert not docker.mutations("stop")


def test_receipt_write_failure_during_restore_does_not_leave_other_services_stopped(monkeypatch, tmp_path):
    docker = FakeDocker({"airflow": "running", "trainer": "running"})
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    real_write = lean_runtime._write
    def failing_write(path, receipt):
        if any(item.get("restore_status") for item in receipt["containers"]):
            raise OSError("Disk full")
        real_write(path, receipt)
    monkeypatch.setattr(lean_runtime, "_write", failing_write)
    with pytest.raises(RuntimeError, match="services restored but receipt update failed"):
        with lean_runtime.lean_environment(True, tmp_path / "lean.json"):
            pass
    assert docker.state("airflow") == docker.state("trainer") == "running"


def test_failed_initial_receipt_does_not_mutate_containers(monkeypatch, tmp_path):
    docker = FakeDocker({"airflow": "running"})
    monkeypatch.setattr(lean_runtime, "_docker", docker)
    def failed_write(*args):
        raise OSError("Cannot save plan")
    monkeypatch.setattr(lean_runtime, "_write", failed_write)
    with pytest.raises(OSError, match="Cannot save plan"):
        with lean_runtime.lean_environment(True, tmp_path / "lean.json"):
            pass
    assert not docker.mutations("stop")
