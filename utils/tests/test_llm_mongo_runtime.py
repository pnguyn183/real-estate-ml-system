import json
from pathlib import Path

import pytest

from research import llm_mongo_runtime as subject


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
        self.generation = 1
        self.mode = "base"
        self.fail_base = False
        self.cache_bytes = 1024 ** 3
        self.volume = "test_mongo_data"
        self.current = self._container()

    def _container(self):
        identifier = f"{self.generation:064x}"
        experiment = self.mode == "experiment"
        return {
            "Id": identifier, "Name": "/real_estate_mongodb", "Image": "sha256:mongo",
            "Config": {
                "Env": ["MONGO_INITDB_ROOT_PASSWORD=never-store-me"],
                "Cmd": ["mongod", "--wiredTigerCacheSizeGB", "1"] if experiment else ["mongod"],
                "Labels": {
                    "com.docker.compose.project": "real-estate-ml-system",
                    "com.docker.compose.service": "mongodb",
                    "com.docker.compose.config-hash": "experiment-hash" if experiment else "base-hash",
                    "com.docker.compose.oneoff": "False",
                },
            },
            "HostConfig": {
                "NanoCpus": 0, "CpuPeriod": 0, "CpuQuota": 0,
                "Memory": 2560 * 1024 ** 2 if experiment else 0,
                "MemorySwap": 2560 * 1024 ** 2 if experiment else 0,
            },
            "Mounts": [{"Type": "volume", "Name": self.volume, "Source": "private",
                        "Destination": "/data/db", "RW": True}],
            "State": {"Status": "running", "Running": True, "Paused": False,
                      "Health": {"Status": "healthy"}},
        }

    def __call__(self, *args, timeout=30):
        self.calls.append(args)
        if args[0] == "ps":
            return self.current["Id"]
        if args[0] == "inspect":
            return json.dumps([self.current])
        if args[0] == "exec":
            return str(self.cache_bytes) + "\n"
        if args[0] == "compose":
            experiment = args.count("--file") == 2
            if not experiment and self.fail_base:
                raise RuntimeError("base recreation failed")
            self.mode = "experiment" if experiment else "base"
            self.generation += 1
            self.current = self._container()
            return self.current["Id"]
        raise AssertionError(args)


@pytest.fixture
def configured(tmp_path):
    base, override = tmp_path / "docker-compose.yml", tmp_path / "experiment.yml"
    base.write_text("services: {mongodb: {image: 'mongo:7'}}", encoding="utf-8")
    override.write_text("services: {mongodb: {command: [mongod]}}", encoding="utf-8")
    config = subject.MongoExperimentConfig(base_compose=base, override_compose=override)
    return Docker(), Clock(), config, tmp_path / "mongo-runtime.json"


def test_profile_is_verified_and_production_restored_without_secret_or_volume_change(configured):
    docker, clock, config, manifest = configured
    original_id = docker.current["Id"]
    with subject.experiment_mongodb(manifest, config, runner=docker, clock=clock,
                                    sleeper=clock.sleep) as receipt:
        assert receipt["status"] == "active"
        assert docker.mode == "experiment"
        assert docker.current["Id"] != original_id
        assert docker.current["HostConfig"]["Memory"] == 2560 * 1024 ** 2
        assert receipt["wiredtiger_cache_bytes"] == 1024 ** 3
        saved = manifest.read_text(encoding="utf-8")
        assert "never-store-me" not in saved and "MONGO_INITDB_ROOT_PASSWORD" not in saved
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["status"] == saved["restore_status"] == "restored"
    assert saved["original"]["data_volume"] == saved["restored"]["data_volume"]
    assert docker.mode == "base"
    assert len([call for call in docker.calls if call[0] == "compose"]) == 2


def test_body_failure_still_restores_base_profile(configured):
    docker, clock, config, manifest = configured
    with pytest.raises(ValueError, match="body failed"):
        with subject.experiment_mongodb(manifest, config, runner=docker, clock=clock,
                                        sleeper=clock.sleep):
            raise ValueError("body failed")
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["status"] == "restored_after_error"
    assert saved["failure_type"] == "ValueError"
    assert docker.mode == "base"


def test_cache_mismatch_fails_closed_and_restores(configured):
    docker, clock, config, manifest = configured
    docker.cache_bytes = 256 * 1024 ** 2
    with pytest.raises(RuntimeError, match="cache verification"):
        with subject.experiment_mongodb(manifest, config, runner=docker, clock=clock,
                                        sleeper=clock.sleep):
            pytest.fail("Unverified cache profile reached the experiment")
    assert docker.mode == "base"
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "restored_after_error"


def test_failed_cleanup_can_be_retried_from_manifest(configured):
    docker, clock, config, manifest = configured
    with pytest.raises(RuntimeError, match="restore failed"):
        with subject.experiment_mongodb(manifest, config, runner=docker, clock=clock,
                                        sleeper=clock.sleep):
            docker.fail_base = True
    assert docker.mode == "experiment"
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "restore_failed"
    docker.fail_base = False
    restored = subject.restore_mongodb(manifest, runner=docker, clock=clock, sleeper=clock.sleep)
    assert restored["status"] == restored["restore_status"] == "restored"
    assert docker.mode == "base"


def test_manual_restore_refuses_changed_compose(configured):
    docker, clock, config, manifest = configured
    docker.fail_base = True
    with pytest.raises(RuntimeError, match="restore failed"):
        with subject.experiment_mongodb(manifest, config, runner=docker, clock=clock,
                                        sleeper=clock.sleep):
            pass
    config.override_compose.write_text("changed", encoding="utf-8")
    docker.fail_base = False
    with pytest.raises(RuntimeError, match="Compose files changed"):
        subject.restore_mongodb(manifest, runner=docker, clock=clock, sleeper=clock.sleep)


def test_manual_restore_refuses_externally_changed_container(configured):
    docker, clock, config, manifest = configured
    docker.fail_base = True
    with pytest.raises(RuntimeError, match="restore failed"):
        with subject.experiment_mongodb(manifest, config, runner=docker, clock=clock,
                                        sleeper=clock.sleep):
            pass
    docker.fail_base = False
    docker.current["Mounts"][0]["Name"] = "unexpected_external_volume"
    with pytest.raises(RuntimeError, match="outside the experiment"):
        subject.restore_mongodb(manifest, runner=docker, clock=clock, sleeper=clock.sleep)


@pytest.mark.parametrize("change", [
    lambda receipt: receipt.update(config=None),
    lambda receipt: receipt["config"].update(base_compose=None),
    lambda receipt: receipt["config"].update(memory_mb="2560"),
    lambda receipt: receipt.update(original={"id": None}),
])
def test_malformed_recovery_manifest_is_rejected_cleanly(configured, change):
    docker, clock, config, manifest = configured
    with subject.experiment_mongodb(manifest, config, runner=docker, clock=clock,
                                    sleeper=clock.sleep):
        pass
    receipt = json.loads(manifest.read_text(encoding="utf-8"))
    receipt.pop("restore_status")
    change(receipt)
    manifest.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        subject.restore_mongodb(manifest, runner=docker, clock=clock, sleeper=clock.sleep)


@pytest.mark.parametrize("changes", [
    {"cache_gb": .1}, {"cache_gb": 3}, {"memory_mb": 512},
    {"memory_mb": 5000}, {"cache_gb": 1, "memory_mb": 1024},
])
def test_invalid_resource_profiles_are_rejected_before_docker(tmp_path, changes):
    base, override = tmp_path / "base.yml", tmp_path / "override.yml"
    base.write_text("base", encoding="utf-8")
    override.write_text("override", encoding="utf-8")
    with pytest.raises(ValueError):
        subject.MongoExperimentConfig(base_compose=base, override_compose=override, **changes)
