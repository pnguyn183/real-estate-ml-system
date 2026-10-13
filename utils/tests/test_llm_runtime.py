import copy
import json

import pytest

from research import llm_runtime


class Docker:
    def __init__(self):
        self.calls, self.containers = [], {}
        self.fail_stop, self.fail_start = None, None
        services = {"processor": "running", "processor-2": "running", "processor-3": "running",
                    "kafka": "running", "trainer": "running", "scraper": "running",
                    "stress-agent": "exited", "airflow": "paused", "frontend": "running"}
        for number, (service, state) in enumerate(services.items(), start=1):
            identifier = f"{number:064x}"
            self.containers[identifier] = {
                "Id": identifier, "Name": "/test-" + service, "Image": "sha256:image",
                "RestartCount": 0, "Config": {"Env": ["GEMINI_API_KEY=never-write-me"], "Labels": {
                    "com.docker.compose.project": "test", "com.docker.compose.service": service,
                    "com.docker.compose.config-hash": "config", "com.docker.compose.oneoff": "False",
                }},
                "HostConfig": {"NanoCpus": 1_000_000_000, "CpuPeriod": 0, "CpuQuota": 0,
                               "Memory": 768 * 1024**2, "MemorySwap": 1536 * 1024**2},
                "State": {"Status": state, "Running": state in {"running", "paused"},
                          "Paused": state == "paused", "StartedAt": "initial-start"},
            }

    def item(self, service):
        return next(item for item in self.containers.values()
                    if item["Config"]["Labels"]["com.docker.compose.service"] == service)

    def state(self, service):
        return self.item(service)["State"]["Status"]

    def mutate_state(self, service, state):
        self.item(service)["State"].update(
            Status=state, Running=state in {"running", "paused"}, Paused=state == "paused")

    def mutations(self, command):
        return [args for args in self.calls if args[0] == command]

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        if args[0] == "info":
            return json.dumps({"NCPU": 16, "MemTotal": 8 * 1024**3, "OSType": "linux"})
        if args[0] == "ps":
            return "\n".join(self.containers)
        if args[0] == "inspect":
            return json.dumps([self.containers[key] for key in args[1:]])
        item = self.containers[args[-1]]
        service = item["Config"]["Labels"]["com.docker.compose.service"]
        if args[0] == "stop":
            self.mutate_state(service, "exited")
            if service == self.fail_stop:
                raise RuntimeError("Docker stop timed out after applying")
        elif args[0] == "start":
            if service == self.fail_start:
                raise RuntimeError("Docker start failed")
            self.mutate_state(service, "running")
            item["State"]["StartedAt"] = "resumed-start"
        else:
            raise AssertionError(args)
        return item["Id"]


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    docker = Docker()
    monkeypatch.setattr(llm_runtime, "_docker", docker)
    monkeypatch.setattr(llm_runtime, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(llm_runtime, "host_memory", lambda: {"memory_available_percent": 20.0})
    (tmp_path / "docker-compose.yml").write_text("unchanged")
    (tmp_path / ".env").write_text("PRIVATE=never-write-me")
    return docker, tmp_path / "recovery.json"


def test_actual_limits_required_before_any_container_stop(runtime):
    docker, path = runtime
    docker.item("processor")["HostConfig"]["Memory"] = 0
    with pytest.raises(RuntimeError, match="explicit finite RAM"):
        with llm_runtime.isolated_runtime(path, project_name="test"):
            pytest.fail("Unbounded resource allocation accepted")
    assert not docker.mutations("stop")
    assert not path.exists()


def test_cpu_quota_period_is_a_valid_explicit_limit(runtime):
    docker, _ = runtime
    docker.item("processor")["HostConfig"].update(NanoCpus=0, CpuQuota=50_000, CpuPeriod=100_000)
    assert llm_runtime.snapshot_runtime("test")["processors"]["processor"]["cpus"] == 0.5
    docker.item("processor")["HostConfig"].update(CpuQuota=-1)
    with pytest.raises(RuntimeError, match="explicit finite CPU"):
        llm_runtime.snapshot_runtime("test")


def test_exact_running_optional_set_restored_without_recreation_or_secrets(runtime):
    docker, path = runtime
    before = {item["Id"]: copy.deepcopy(item["HostConfig"]) for item in docker.containers.values()}
    with llm_runtime.isolated_runtime(path, project_name="test") as receipt:
        assert docker.state("trainer") == docker.state("scraper") == docker.state("frontend") == "exited"
        assert docker.state("airflow") == "paused"
        assert docker.state("stress-agent") == "exited"
        assert llm_runtime.verify_runtime(receipt)["models_unchanged"] is True
    assert docker.state("trainer") == docker.state("scraper") == docker.state("frontend") == "running"
    assert docker.state("airflow") == "paused"
    assert docker.state("stress-agent") == "exited"
    assert {args[0] for args in docker.calls} <= {"ps", "inspect", "info", "stop", "start"}
    assert before == {item["Id"]: item["HostConfig"] for item in docker.containers.values()}
    assert "never-write-me" not in path.read_text()
    assert "GEMINI_API_KEY" not in path.read_text()
    assert json.loads(path.read_text())["status"] == "restored"


def test_durable_recovery_plan_precedes_every_stop(runtime, monkeypatch):
    docker, path = runtime
    def checked(*args, **kwargs):
        if args[0] == "stop":
            saved = json.loads(path.read_text())
            original = next(item for item in saved["containers"] if item["id"] == args[-1])
            assert original["running"] and original["stop_attempted"]
        return docker(*args, **kwargs)
    monkeypatch.setattr(llm_runtime, "_docker", checked)
    with llm_runtime.isolated_runtime(path, project_name="test"):
        pass


def test_uncertain_stop_failure_reconciles_and_restores(runtime):
    docker, path = runtime
    docker.fail_stop = "scraper"
    with pytest.raises(RuntimeError, match="stop timed out"):
        with llm_runtime.isolated_runtime(path, project_name="test"):
            pytest.fail("Started despite isolation failure")
    assert docker.state("trainer") == docker.state("scraper") == "running"
    assert json.loads(path.read_text())["status"] == "restored_after_error"


@pytest.mark.parametrize("available", [0, 9.99, 10, float("nan"), None])
def test_memory_floor_fails_closed_and_restores(runtime, monkeypatch, available):
    docker, path = runtime
    monkeypatch.setattr(llm_runtime, "host_memory", lambda: {"memory_available_percent": available})
    with pytest.raises(RuntimeError, match="safety floor"):
        with llm_runtime.isolated_runtime(path, project_name="test", memory_wait_seconds=0):
            pytest.fail("Load allowed while host RAM unsafe or unknown")
    assert docker.state("trainer") == docker.state("scraper") == "running"


def test_floor_cannot_be_relaxed_for_a_successful_trial(runtime):
    docker, path = runtime
    with pytest.raises(ValueError, match="at least 10%"):
        with llm_runtime.isolated_runtime(path, project_name="test", min_memory_available_percent=5):
            pass
    assert not docker.calls


@pytest.mark.parametrize("change", ["allocation", "restart", "publisher", "env", "model"])
def test_changed_experiment_conditions_invalidate_comparison(runtime, change):
    docker, path = runtime
    with llm_runtime.isolated_runtime(path, project_name="test") as receipt:
        if change == "allocation":
            docker.item("processor")["HostConfig"]["NanoCpus"] = 500_000_000
        elif change == "restart":
            docker.item("processor")["RestartCount"] += 1
        elif change == "publisher":
            docker.mutate_state("stress-agent", "running")
        elif change == "env":
            (path.parent / ".env").write_text("changed")
        else:
            (path.parent / "artifacts").mkdir()
            (path.parent / "artifacts" / "price_model.joblib").write_bytes(b"new model")
        with pytest.raises(RuntimeError, match="changed|resumed"):
            llm_runtime.verify_runtime(receipt)
    # Restoration never edits the operator's allocation or starts an originally
    # stopped service; it only reverses this context's own stops.
    assert docker.state("trainer") == "running"


def test_manual_recovery_retries_failed_id_without_starting_originally_stopped(runtime):
    docker, path = runtime
    docker.fail_start = "trainer"
    with pytest.raises(RuntimeError, match="could not restore: trainer"):
        with llm_runtime.isolated_runtime(path, project_name="test"):
            pass
    assert docker.state("trainer") == "exited"
    assert docker.state("scraper") == "running"
    docker.fail_start = None
    restored = llm_runtime.restore_runtime(path)
    assert restored["restore_errors"] == []
    assert docker.state("trainer") == "running"
    assert docker.state("stress-agent") == "exited"


def test_manual_recovery_does_not_replay_completed_restores(runtime):
    docker, path = runtime
    docker.fail_start = "trainer"
    with pytest.raises(RuntimeError, match="could not restore: trainer"):
        with llm_runtime.isolated_runtime(path, project_name="test"):
            pass
    # The prior cleanup restored the scraper successfully. Its later manual
    # stop is outside this experiment and must survive retrying the trainer.
    docker.mutate_state("scraper", "exited")
    before = len(docker.mutations("start"))
    docker.fail_start = None
    receipt = llm_runtime.restore_runtime(path)
    assert receipt["status"] == "restored_after_error"
    assert docker.state("trainer") == "running"
    assert docker.state("scraper") == "exited"
    assert docker.mutations("start")[before:] == [("start", docker.item("trainer")["Id"])]
    before = len(docker.mutations("start"))
    llm_runtime.restore_runtime(path)
    assert len(docker.mutations("start")) == before


def test_intermediate_check_avoids_hashing_archives_but_detects_artifact_changes(runtime, monkeypatch):
    _, path = runtime
    with llm_runtime.isolated_runtime(path, project_name="test") as receipt:
        monkeypatch.setattr(llm_runtime, "_model_hashes", lambda: pytest.fail("Full archive hashing"))
        assert llm_runtime.verify_runtime(receipt, deep_artifact_check=False)["artifact_verification"] == "file_metadata"
        (path.parent / "artifacts").mkdir()
        (path.parent / "artifacts" / "new.joblib").write_bytes(b"new version")
        with pytest.raises(RuntimeError, match="Model artifacts changed"):
            llm_runtime.verify_runtime(receipt, deep_artifact_check=False)


def test_recovery_refuses_changed_container_and_restores_other_ids(runtime):
    docker, path = runtime
    with pytest.raises(RuntimeError, match="could not restore: scraper"):
        with llm_runtime.isolated_runtime(path, project_name="test"):
            docker.item("scraper")["Config"]["Labels"]["com.docker.compose.config-hash"] = "changed"
    assert docker.state("scraper") == "exited"
    assert docker.state("trainer") == "running"
    assert ("start", docker.item("scraper")["Id"]) not in docker.mutations("start")


def test_manifest_cannot_authorize_originally_stopped_container(runtime):
    docker, path = runtime
    with llm_runtime.isolated_runtime(path, project_name="test"):
        pass
    receipt = json.loads(path.read_text())
    original = next(item for item in receipt["containers"] if item["service"] == "stress-agent")
    original["stop_attempted"] = True
    path.write_text(json.dumps(receipt))
    before = len(docker.mutations("start"))
    with pytest.raises(ValueError, match="original running set"):
        llm_runtime.restore_runtime(path)
    assert len(docker.mutations("start")) == before


def test_cleanup_continues_if_manifest_disk_write_fails(runtime, monkeypatch):
    docker, path = runtime
    write = llm_runtime._write
    def full_disk(path, receipt):
        if any(item.get("restore_status") for item in receipt["containers"]):
            raise OSError("Disk full")
        write(path, receipt)
    monkeypatch.setattr(llm_runtime, "_write", full_disk)
    with pytest.raises(RuntimeError, match="Services restored but recovery manifest"):
        with llm_runtime.isolated_runtime(path, project_name="test"):
            pass
    assert docker.state("trainer") == docker.state("scraper") == docker.state("frontend") == "running"


def test_body_exception_and_initial_manifest_failure_are_safe(runtime, monkeypatch):
    docker, path = runtime
    with pytest.raises(ValueError, match="sample failed"):
        with llm_runtime.isolated_runtime(path, project_name="test"):
            raise ValueError("sample failed")
    assert docker.state("trainer") == "running"
    before = len(docker.mutations("stop"))
    def cannot_write(*args):
        raise OSError("No space")
    monkeypatch.setattr(llm_runtime, "_write", cannot_write)
    with pytest.raises(OSError, match="No space"):
        with llm_runtime.isolated_runtime(path.with_name("new.json"), project_name="test"):
            pass
    assert len(docker.mutations("stop")) == before
