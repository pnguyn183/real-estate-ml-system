"""Reversible MongoDB memory profile for isolated LLM load experiments.

The named data volume is preserved.  MongoDB is recreated once with the
experiment-only Compose override before the fixed-allocation snapshot, then
recreated from the production Compose file during cleanup.  A durable manifest
allows an interrupted cleanup to be retried without storing container secrets.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Callable

from research.lean_runtime import PROJECT_ROOT, _docker, _now, _write


MIB = 1024 ** 2
IDENTITY_LABELS = (
    "com.docker.compose.project", "com.docker.compose.service",
    "com.docker.compose.config-hash", "com.docker.compose.oneoff",
)
LIMIT_KEYS = ("NanoCpus", "CpuPeriod", "CpuQuota", "Memory", "MemorySwap")


@dataclass(frozen=True)
class MongoExperimentConfig:
    project_name: str = "real-estate-ml-system"
    base_compose: Path = PROJECT_ROOT / "docker-compose.yml"
    override_compose: Path = PROJECT_ROOT / "research" / "llm_experiment.compose.yml"
    service: str = "mongodb"
    cache_gb: float = 1.0
    memory_mb: int = 2560
    ready_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.project_name):
            raise ValueError("Invalid Compose project name")
        if self.service != "mongodb":
            raise ValueError("Only the MongoDB experiment service is supported")
        if (not isinstance(self.cache_gb, (int, float)) or isinstance(self.cache_gb, bool)
                or not math.isfinite(self.cache_gb) or not .256 <= self.cache_gb <= 2):
            raise ValueError("MongoDB experiment cache must be between 0.256 and 2 GiB")
        if (type(self.memory_mb) is not int or not 1024 <= self.memory_mb <= 4096
                or self.cache_gb * 1024 >= self.memory_mb):
            raise ValueError("MongoDB memory must exceed cache and remain within experiment bounds")
        if (not isinstance(self.ready_timeout_seconds, (int, float))
                or isinstance(self.ready_timeout_seconds, bool)
                or not math.isfinite(self.ready_timeout_seconds)
                or not 10 <= self.ready_timeout_seconds <= 300):
            raise ValueError("MongoDB readiness timeout must be between 10 and 300 seconds")
        for path in (self.base_compose, self.override_compose):
            if not Path(path).is_file():
                raise ValueError("MongoDB experiment Compose file is unavailable")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_hashes(config: MongoExperimentConfig) -> dict[str, str]:
    return {str(Path(path).resolve()): _sha256(Path(path))
            for path in (config.base_compose, config.override_compose)}


def _snapshot(config: MongoExperimentConfig, runner: Callable = _docker) -> dict:
    identifiers = runner("ps", "--all", "--no-trunc", "--quiet",
        "--filter", f"label=com.docker.compose.project={config.project_name}",
        "--filter", f"label=com.docker.compose.service={config.service}",
        "--filter", "label=com.docker.compose.oneoff=False").split()
    if len(identifiers) != 1 or not re.fullmatch(r"[a-f0-9]{64}", identifiers[0]):
        raise RuntimeError("Exactly one MongoDB Compose container is required")
    values = json.loads(runner("inspect", identifiers[0]))
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError("Docker inspect returned an unexpected MongoDB container")
    value = values[0]
    labels = value.get("Config", {}).get("Labels") or {}
    state, host = value.get("State", {}), value.get("HostConfig", {})
    mounts = [{"type": item.get("Type"), "name": item.get("Name"),
               "destination": item.get("Destination"), "rw": item.get("RW")}
              for item in value.get("Mounts", []) if item.get("Destination") == "/data/db"]
    if (labels.get(IDENTITY_LABELS[0]) != config.project_name
            or labels.get(IDENTITY_LABELS[1]) != config.service
            or labels.get(IDENTITY_LABELS[3]) != "False"):
        raise RuntimeError("MongoDB Compose identity is invalid")
    if (len(mounts) != 1 or mounts[0]["type"] != "volume" or not mounts[0]["name"] or not mounts[0]["rw"]):
        raise RuntimeError("MongoDB must use one writable named volume at /data/db")
    health = state.get("Health", {}).get("Status")
    return {
        "id": identifiers[0], "name": value.get("Name", "").lstrip("/"),
        "image": value.get("Image"), "identity": {key: labels.get(key) for key in IDENTITY_LABELS},
        "command": value.get("Config", {}).get("Cmd"),
        "limits": {key: host.get(key) for key in LIMIT_KEYS}, "data_volume": mounts[0],
        "running": state.get("Running") is True, "paused": state.get("Paused") is True,
        "status": state.get("Status"), "health": health,
    }


def _wait_ready(config: MongoExperimentConfig, runner: Callable, clock: Callable,
                sleeper: Callable, stop_requested: Callable[[], bool] | None) -> dict:
    deadline = clock() + config.ready_timeout_seconds
    while True:
        if stop_requested is not None and stop_requested():
            raise RuntimeError("MongoDB experiment preparation cancelled")
        current = _snapshot(config, runner)
        if current["running"] and not current["paused"] and current["health"] == "healthy":
            return current
        remaining = deadline - clock()
        if remaining <= 0:
            raise RuntimeError("MongoDB did not become healthy within the bounded wait")
        sleeper(min(2.0, remaining))


def _compose(config: MongoExperimentConfig, runner: Callable, *, experiment: bool) -> None:
    args = ["compose", "--project-directory", str(PROJECT_ROOT),
            "--project-name", config.project_name, "--file", str(config.base_compose)]
    if experiment:
        args.extend(("--file", str(config.override_compose)))
    args.extend(("up", "-d", "--no-deps", "--force-recreate", config.service))
    runner(*args, timeout=180)


def _cache_bytes(identifier: str, runner: Callable) -> int:
    script = 'print(db.serverStatus().wiredTiger.cache["maximum bytes configured"])'
    output = runner("exec", identifier, "mongosh", "--quiet", "--eval", script, timeout=30)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    try:
        value = int(float(lines[-1]))
    except (IndexError, TypeError, ValueError):
        raise RuntimeError("MongoDB did not report a numeric WiredTiger cache limit") from None
    if value <= 0:
        raise RuntimeError("MongoDB reported an invalid WiredTiger cache limit")
    return value


def _same_storage(original: dict, current: dict) -> None:
    if (current["image"] != original["image"]
            or current["data_volume"] != original["data_volume"]
            or current["name"] != original["name"]):
        raise RuntimeError("MongoDB image, name or persistent volume changed")


def _is_experiment_profile(config: MongoExperimentConfig, original: dict, current: dict) -> bool:
    try:
        _same_storage(original, current)
        command = current["command"]
        index = command.index("--wiredTigerCacheSizeGB")
        command_cache = float(command[index + 1])
    except (RuntimeError, AttributeError, ValueError, IndexError, TypeError):
        return False
    expected = config.memory_mb * MIB
    return (math.isclose(command_cache, config.cache_gb)
            and current["limits"].get("Memory") == expected
            and current["limits"].get("MemorySwap") == expected)


def _is_original_profile(original: dict, current: dict) -> bool:
    try:
        _same_storage(original, current)
    except RuntimeError:
        return False
    return (current["identity"].get(IDENTITY_LABELS[2]) == original["identity"].get(IDENTITY_LABELS[2])
            and current["command"] == original["command"]
            and current["limits"] == original["limits"])


def _validate_manifest(receipt: dict) -> MongoExperimentConfig:
    if receipt.get("schema_version") != 1 or receipt.get("kind") != "llm-mongodb-memory-profile":
        raise ValueError("Not an LLM MongoDB experiment recovery manifest")
    values = receipt.get("config")
    if not isinstance(values, dict):
        raise ValueError("MongoDB recovery manifest has an invalid configuration")
    try:
        base = values["base_compose"]
        override = values["override_compose"]
        if not isinstance(base, str) or not base or not isinstance(override, str) or not override:
            raise ValueError
        config = MongoExperimentConfig(
            project_name=values.get("project_name"), base_compose=Path(base),
            override_compose=Path(override), service=values.get("service"),
            cache_gb=values.get("cache_gb"), memory_mb=values.get("memory_mb"),
            ready_timeout_seconds=values.get("ready_timeout_seconds"),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("MongoDB recovery manifest has an invalid configuration") from None
    original = receipt.get("original")
    original_id = original.get("id") if isinstance(original, dict) else None
    if not isinstance(original_id, str) or not re.fullmatch(r"[a-f0-9]{64}", original_id):
        raise ValueError("MongoDB recovery manifest lacks the original identity")
    if receipt.get("file_hashes") != _file_hashes(config):
        raise RuntimeError("MongoDB experiment Compose files changed; automatic recovery refused")
    return config


def _restore(receipt: dict, manifest_path: Path, runner: Callable, clock: Callable,
             sleeper: Callable, stop_requested: Callable[[], bool] | None = None) -> dict:
    config = _validate_manifest(receipt)
    if receipt.get("restore_status") in {"restored", "already_restored"}:
        return receipt
    current = _snapshot(config, runner)
    original = receipt["original"]
    if _is_original_profile(original, current):
        receipt["restore_status"], receipt["status"] = "already_restored", "restored"
        _write(manifest_path, receipt)
        return receipt
    if not _is_experiment_profile(config, original, current):
        raise RuntimeError("MongoDB changed outside the experiment; automatic recovery refused")
    receipt["restore_attempted"] = True
    _write(manifest_path, receipt)
    _compose(config, runner, experiment=False)
    restored = _wait_ready(config, runner, clock, sleeper, stop_requested)
    if not _is_original_profile(original, restored):
        raise RuntimeError("MongoDB production profile was not restored exactly")
    receipt.update(status="restored", restore_status="restored", restored=restored, ended_at=_now())
    _write(manifest_path, receipt)
    return receipt


def restore_mongodb(manifest_path: str | Path, *, runner: Callable = _docker,
                    clock: Callable = time.monotonic, sleeper: Callable = time.sleep) -> dict:
    """Retry production MongoDB recreation from a durable experiment manifest."""
    path = Path(manifest_path)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    return _restore(receipt, path, runner, clock, sleeper)


@contextmanager
def experiment_mongodb(manifest_path: str | Path, config: MongoExperimentConfig | None = None,
                       *, runner: Callable = _docker, clock: Callable = time.monotonic,
                       sleeper: Callable = time.sleep,
                       stop_requested: Callable[[], bool] | None = None):
    """Apply the bounded profile and always recreate the production profile."""
    config = config or MongoExperimentConfig()
    path = Path(manifest_path)
    if path.exists():
        raise FileExistsError("Use a new MongoDB recovery manifest")
    original = _wait_ready(config, runner, clock, sleeper, stop_requested)
    receipt = {
        "schema_version": 1, "kind": "llm-mongodb-memory-profile", "status": "preparing",
        "started_at": _now(), "config": {
            "project_name": config.project_name, "base_compose": str(Path(config.base_compose).resolve()),
            "override_compose": str(Path(config.override_compose).resolve()), "service": config.service,
            "cache_gb": config.cache_gb, "memory_mb": config.memory_mb,
            "ready_timeout_seconds": config.ready_timeout_seconds,
        }, "file_hashes": _file_hashes(config), "original": original,
        "apply_attempted": False, "restore_attempted": False,
    }
    _write(path, receipt)
    body_failed = False
    try:
        receipt["apply_attempted"] = True
        _write(path, receipt)
        _compose(config, runner, experiment=True)
        applied = _wait_ready(config, runner, clock, sleeper, stop_requested)
        if not _is_experiment_profile(config, original, applied):
            raise RuntimeError("MongoDB experiment memory profile was not applied exactly")
        cache_bytes = _cache_bytes(applied["id"], runner)
        expected_cache = config.cache_gb * 1024 ** 3
        if not .95 * expected_cache <= cache_bytes <= 1.05 * expected_cache:
            raise RuntimeError("MongoDB WiredTiger cache verification failed")
        receipt.update(status="active", applied=applied, wiredtiger_cache_bytes=cache_bytes)
        _write(path, receipt)
        yield receipt
    except BaseException as exc:
        body_failed = True
        receipt["failure_type"] = type(exc).__name__
        raise
    finally:
        if receipt.get("apply_attempted"):
            try:
                _restore(receipt, path, runner, clock, sleeper)
                if body_failed:
                    receipt["status"] = "restored_after_error"
                    _write(path, receipt)
            except Exception as exc:
                receipt.update(status="restore_failed", restore_error_type=type(exc).__name__)
                try:
                    _write(path, receipt)
                finally:
                    raise RuntimeError(f"MongoDB production profile restore failed; inspect {path}") from exc
