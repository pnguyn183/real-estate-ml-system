"""Durable isolation for the resource-constrained LLM rate-control topology.

The base Compose project is never recreated or removed. Every running base
container is stopped by exact ID, a separate lean project is started from its
own Compose file, and cleanup stops only recorded lean IDs before resuming the
exact base IDs. Recovery manifests omit environment values and mount sources.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Callable

import psutil

from research.lean_runtime import PROJECT_ROOT, _docker, _now, _write


IDENTITY_LABELS = (
    "com.docker.compose.project", "com.docker.compose.service",
    "com.docker.compose.config-hash", "com.docker.compose.oneoff",
)
LIMIT_KEYS = (
    "NanoCpus", "CpuPeriod", "CpuQuota", "Memory", "MemorySwap",
    "MemoryReservation", "CpuShares", "CpusetCpus", "CpusetMems", "PidsLimit",
)
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


@dataclass(frozen=True)
class LeanRuntimeConfig:
    base_project: str = "real-estate-ml-system"
    lean_project: str = "real-estate-llm-lean"
    compose_path: Path = PROJECT_ROOT / "research" / "llm_lean.compose.yml"
    project_directory: Path = PROJECT_ROOT
    running_services: tuple[str, ...] = ("zookeeper", "kafka", "mongodb", "processor")
    completed_services: tuple[str, ...] = ("kafka-init",)
    ready_timeout_seconds: float = 180.0

    def __post_init__(self) -> None:
        if (not isinstance(self.base_project, str) or not NAME_PATTERN.fullmatch(self.base_project)
                or not isinstance(self.lean_project, str) or not NAME_PATTERN.fullmatch(self.lean_project)
                or self.base_project == self.lean_project):
            raise ValueError("Lean and base Compose projects must be distinct valid names")
        running, completed = tuple(self.running_services), tuple(self.completed_services)
        if (not running or len(set(running)) != len(running)
                or len(set(completed)) != len(completed) or set(running) & set(completed)
                or any(not isinstance(value, str) or not NAME_PATTERN.fullmatch(value)
                       for value in (*running, *completed))):
            raise ValueError("Lean service sets are invalid")
        if (not isinstance(self.ready_timeout_seconds, (int, float))
                or isinstance(self.ready_timeout_seconds, bool)
                or not math.isfinite(self.ready_timeout_seconds)
                or not 10 <= self.ready_timeout_seconds <= 600):
            raise ValueError("Lean readiness timeout must be between 10 and 600 seconds")
        if not Path(self.compose_path).is_file() or not Path(self.project_directory).is_dir():
            raise ValueError("Lean Compose file or project directory is unavailable")

    @property
    def allowed_services(self) -> frozenset[str]:
        return frozenset((*self.running_services, *self.completed_services))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hashes(config: LeanRuntimeConfig) -> dict[str, str]:
    paths = [Path(config.compose_path)]
    environment = Path(config.project_directory) / ".env"
    if environment.is_file():
        paths.append(environment)
    return {str(path.resolve()): _sha256(path) for path in paths}


def _container(value: dict) -> dict:
    labels = value.get("Config", {}).get("Labels") or {}
    state, host = value.get("State", {}), value.get("HostConfig", {})
    identifier = value.get("Id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise RuntimeError("Docker did not return a complete container identity")
    return {
        "id": identifier, "name": value.get("Name", "").lstrip("/"),
        "service": labels.get("com.docker.compose.service"),
        "identity": {key: labels.get(key) for key in IDENTITY_LABELS},
        "image": value.get("Image"),
        "limits": {key: host.get(key) for key in LIMIT_KEYS},
        "restart_policy": {
            "name": (host.get("RestartPolicy") or {}).get("Name"),
            "maximum_retry_count": (host.get("RestartPolicy") or {}).get("MaximumRetryCount"),
        },
        "state": state.get("Status"), "running": state.get("Running") is True,
        "paused": state.get("Paused") is True,
        "health": (state.get("Health") or {}).get("Status"),
        "exit_code": state.get("ExitCode"), "started_at": state.get("StartedAt"),
        "restart_count": value.get("RestartCount"),
    }


def _discover(project: str, runner: Callable = _docker) -> list[dict]:
    identifiers = runner(
        "ps", "--all", "--no-trunc", "--quiet", "--filter",
        f"label=com.docker.compose.project={project}",
    ).split()
    if any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in identifiers):
        raise RuntimeError("Docker project discovery returned an invalid identity")
    if not identifiers:
        return []
    values = json.loads(runner("inspect", *identifiers))
    if not isinstance(values, list) or len(values) != len(identifiers):
        raise RuntimeError("Docker project inspection returned an unexpected container count")
    containers = sorted((_container(value) for value in values), key=lambda item: item["id"])
    if ({item["id"] for item in containers} != set(identifiers)
            or any(item["identity"].get(IDENTITY_LABELS[0]) != project for item in containers)):
        raise RuntimeError("Docker Compose project identity changed during discovery")
    return containers


def _inspect(identifier: str, runner: Callable = _docker) -> dict:
    values = json.loads(runner("inspect", identifier))
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError("Docker inspect returned an unexpected container count")
    return _container(values[0])


def _same_container(original: dict, current: dict) -> None:
    for key in ("id", "name", "service", "identity", "image", "limits"):
        if current.get(key) != original.get(key):
            raise RuntimeError("Container identity or allocation changed; recovery refused")


def _engine(runner: Callable = _docker) -> dict:
    value = json.loads(runner("info", "--format", "{{json .}}"))
    result = {"cpus": value.get("NCPU"), "memory_bytes": value.get("MemTotal"),
              "os_type": value.get("OSType")}
    if (not isinstance(result["cpus"], (int, float)) or result["cpus"] <= 0
            or not isinstance(result["memory_bytes"], (int, float))
            or result["memory_bytes"] <= 0 or result["os_type"] != "linux"):
        raise RuntimeError("Docker CPU/RAM capacity or Linux engine is unavailable")
    return result


def host_memory() -> dict:
    value = psutil.virtual_memory()
    if value.total <= 0:
        raise RuntimeError("Host RAM capacity is unavailable")
    return {"memory_available_percent": 100 * value.available / value.total,
            "memory_available_bytes": value.available, "memory_total_bytes": value.total}


def reclaim_docker_desktop(*, runner: Callable = _docker,
                           clock: Callable = time.monotonic,
                           sleeper: Callable = time.sleep) -> None:
    """Restart Docker Desktop after base restart policies have been disabled."""
    if os.name != "nt":
        raise RuntimeError("Docker Desktop memory reclaim is supported only on Windows")
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop"
    program = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker" / "Docker"
    directories = (local, program)
    cli = next((path / "DockerCli.exe" for path in directories
                if (path / "DockerCli.exe").is_file()), None)
    desktop = next((path / "Docker Desktop.exe" for path in directories
                    if (path / "Docker Desktop.exe").is_file()), None)
    if cli is None or desktop is None:
        raise RuntimeError("Docker Desktop executables are unavailable")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run([str(cli), "-Shutdown"], check=True, timeout=120,
                   creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    subprocess.Popen([str(desktop)], creationflags=flags, startupinfo=startup,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = clock() + 180
    last_error = None
    while clock() < deadline:
        try:
            runner("info", "--format", "{{json .ServerVersion}}", timeout=15)
            return
        except Exception as exc:
            last_error = exc
            sleeper(3)
    raise RuntimeError("Docker Desktop did not restart within the bounded wait") from last_error


def _policy_argument(policy: dict) -> str:
    if not isinstance(policy, dict):
        raise ValueError("Container restart policy is invalid")
    name, retries = policy.get("name"), policy.get("maximum_retry_count")
    if name not in {"no", "always", "unless-stopped", "on-failure"}:
        raise ValueError("Container restart policy is unsupported")
    if type(retries) is not int or retries < 0:
        raise ValueError("Container restart retry count is invalid")
    return f"on-failure:{retries}" if name == "on-failure" and retries else name


def _set_restart_policy(original: dict, expected: dict, runner: Callable) -> dict:
    runner("update", "--restart=" + _policy_argument(expected), original["id"], timeout=30)
    current = _inspect(original["id"], runner)
    _same_container(original, current)
    if current["restart_policy"] != expected:
        raise RuntimeError("Docker did not apply the expected restart policy")
    return current


def _has_explicit_limits(item: dict) -> bool:
    limits = item["limits"]
    memory = limits.get("Memory")
    nano, period, quota = (limits.get(key) for key in ("NanoCpus", "CpuPeriod", "CpuQuota"))
    cpu_limited = ((isinstance(nano, (int, float)) and nano > 0)
                   or (isinstance(period, (int, float)) and period > 0
                       and isinstance(quota, (int, float)) and quota > 0))
    return isinstance(memory, (int, float)) and memory > 0 and cpu_limited


def _validate_lean(config: LeanRuntimeConfig, containers: list[dict], *, ready: bool) -> dict:
    if any(item["service"] not in config.allowed_services for item in containers):
        raise RuntimeError("Lean Compose project contains an unexpected service")
    by_service = {}
    for service in config.allowed_services:
        matches = [item for item in containers if item["service"] == service]
        if len(matches) != 1:
            raise RuntimeError(f"Exactly one lean container is required for {service}")
        by_service[service] = matches[0]
    if ready:
        for service in config.running_services:
            item = by_service[service]
            if not item["running"] or item["paused"] or item["health"] != "healthy":
                raise RuntimeError(f"Lean service is not healthy: {service}")
            if not _has_explicit_limits(item):
                raise RuntimeError(f"Lean service lacks explicit CPU/RAM limits: {service}")
        for service in config.completed_services:
            item = by_service[service]
            if item["running"] or item["paused"] or item["state"] != "exited" or item["exit_code"] != 0:
                raise RuntimeError(f"Lean initialization service did not complete: {service}")
    return by_service


def _compose(config: LeanRuntimeConfig, runner: Callable, *arguments: str,
             timeout: float = 180) -> str:
    return runner(
        "compose", "--project-directory", str(Path(config.project_directory).resolve()),
        "--project-name", config.lean_project,
        "--file", str(Path(config.compose_path).resolve()), *arguments, timeout=timeout,
    )


def _wait_ready(config: LeanRuntimeConfig, runner: Callable, clock: Callable,
                sleeper: Callable, stop_requested: Callable[[], bool] | None) -> list[dict]:
    deadline = clock() + config.ready_timeout_seconds
    last_error = None
    while True:
        if stop_requested is not None and stop_requested():
            raise RuntimeError("Lean runtime preparation cancelled")
        try:
            current = _discover(config.lean_project, runner)
            _validate_lean(config, current, ready=True)
            return current
        except RuntimeError as exc:
            last_error = exc
        remaining = deadline - clock()
        if remaining <= 0:
            raise RuntimeError("Lean topology did not become ready within the bounded wait") from last_error
        sleeper(min(2.0, remaining))


def _wait_memory(receipt: dict, manifest_path: Path, floor: float, wait_seconds: float,
                 clock: Callable, sleeper: Callable,
                 stop_requested: Callable[[], bool] | None,
                 memory_reader: Callable[[], dict]) -> None:
    deadline = clock() + wait_seconds
    while True:
        if stop_requested is not None and stop_requested():
            raise RuntimeError("Lean runtime preparation cancelled")
        sample = memory_reader()
        receipt["host_before_trial"] = sample
        _write(manifest_path, receipt)
        available = sample.get("memory_available_percent")
        if (isinstance(available, (int, float)) and not isinstance(available, bool)
                and math.isfinite(available) and available > floor):
            return
        if clock() >= deadline:
            raise RuntimeError("Host available RAM is at or below the lean experiment safety floor")
        sleeper(min(2.0, max(0.0, deadline - clock())))


def _topology_snapshot(config: LeanRuntimeConfig, containers: list[dict],
                       engine: dict, hashes: dict) -> dict:
    topology = {
        "project": config.lean_project,
        "running_services": list(config.running_services),
        "completed_services": list(config.completed_services),
        "engine": engine, "file_hashes": hashes, "containers": containers,
    }
    topology["fingerprint"] = _digest(topology)
    return topology


def _config_from_manifest(receipt: dict) -> LeanRuntimeConfig:
    if receipt.get("schema_version") != 1 or receipt.get("kind") != "llm-lean-runtime":
        raise ValueError("Not an LLM lean-runtime recovery manifest")
    values = receipt.get("config")
    if not isinstance(values, dict):
        raise ValueError("Lean recovery manifest has an invalid configuration")
    try:
        return LeanRuntimeConfig(
            base_project=values["base_project"], lean_project=values["lean_project"],
            compose_path=Path(values["compose_path"]),
            project_directory=Path(values["project_directory"]),
            running_services=tuple(values["running_services"]),
            completed_services=tuple(values["completed_services"]),
            ready_timeout_seconds=values["ready_timeout_seconds"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("Lean recovery manifest has an invalid configuration") from None


def verify_lean_runtime(receipt: dict, *, runner: Callable = _docker) -> dict:
    """Require the fixed lean topology and stopped base project to remain unchanged."""
    config = _config_from_manifest(receipt)
    if _file_hashes(config) != receipt.get("file_hashes"):
        raise RuntimeError("Lean Compose or environment file changed during the experiment")
    current_base = {item["id"]: item for item in _discover(config.base_project, runner)}
    originals = receipt.get("base_containers") or []
    if set(current_base) != {item["id"] for item in originals}:
        raise RuntimeError("Base Compose project membership changed during the experiment")
    for original in originals:
        current = current_base[original["id"]]
        _same_container(original, current)
        if original.get("stop_attempted"):
            if current["running"] or current["paused"] or current["state"] != "exited":
                raise RuntimeError("A base project container resumed during the lean experiment")
        elif (current["running"] != original["running"]
              or current["paused"] != original["paused"]
              or current["state"] != original["state"]):
            raise RuntimeError("An initially inactive base container changed state")
        if (receipt.get("daemon_reclaim_enabled")
                and current["restart_policy"] != {"name": "no", "maximum_retry_count": 0}):
            raise RuntimeError("A base restart policy changed during the lean experiment")
    lean = _discover(config.lean_project, runner)
    _validate_lean(config, lean, ready=True)
    snapshot = _topology_snapshot(config, lean, _engine(runner), receipt["file_hashes"])
    if snapshot["fingerprint"] != (receipt.get("lean_snapshot") or {}).get("fingerprint"):
        raise RuntimeError("Lean topology identity, state or allocation changed")
    return {"fingerprint": snapshot["fingerprint"], "topology": snapshot,
            "base_stopped": True, "files_unchanged": True}


def verify_runtime(receipt: dict, *, deep_artifact_check: bool = True,
                   runner: Callable = _docker) -> dict:
    """Compatibility entry point for repeated checks between A/B arms."""
    del deep_artifact_check
    return verify_lean_runtime(receipt, runner=runner)


def _validate_manifest(receipt: dict) -> LeanRuntimeConfig:
    config = _config_from_manifest(receipt)
    base = receipt.get("base_containers")
    if not isinstance(base, list) or not base:
        raise ValueError("Lean recovery manifest has no base container snapshots")
    identifiers = set()
    for item in base:
        identifier = item.get("id") if isinstance(item, dict) else None
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier)
                or identifier in identifiers):
            raise ValueError("Lean recovery manifest has invalid base identities")
        identifiers.add(identifier)
        if (item.get("identity") or {}).get(IDENTITY_LABELS[0]) != config.base_project:
            raise ValueError("Lean recovery manifest contains a foreign base container")
        if item.get("stop_attempted") and (not item.get("running") or item.get("paused")):
            raise ValueError("Lean recovery manifest cannot start an originally inactive container")
        _policy_argument(item.get("restart_policy"))
    lean = receipt.get("lean_containers")
    if lean is not None and not isinstance(lean, list):
        raise ValueError("Lean recovery manifest has invalid experiment containers")
    return config


def _stop_lean(receipt: dict, config: LeanRuntimeConfig, manifest_path: Path,
               runner: Callable) -> None:
    if not receipt.get("lean_create_attempted"):
        return
    originals = receipt.get("lean_containers")
    if not originals:
        originals = _discover(config.lean_project, runner)
        if any(item["service"] not in config.allowed_services for item in originals):
            raise RuntimeError("Lean project changed externally; recovery refused")
        receipt["lean_containers"] = originals
        _write(manifest_path, receipt)
    for original in reversed(originals):
        if original.get("stop_status") in {"stopped", "already_stopped"}:
            continue
        try:
            current = _inspect(original["id"], runner)
            _same_container(original, current)
            if current["paused"]:
                raise RuntimeError("Lean container paused externally; recovery refused")
            if current["running"]:
                runner("stop", "--time", "20", original["id"], timeout=35)
                current = _inspect(original["id"], runner)
                _same_container(original, current)
                if current["running"] or current["paused"] or current["state"] != "exited":
                    raise RuntimeError("Lean container did not stop")
                original["stop_status"] = "stopped"
            elif current["state"] in {"created", "exited"}:
                original["stop_status"] = "already_stopped"
            else:
                raise RuntimeError("Lean container is not in a recoverable state")
        except Exception as exc:
            original["stop_status"] = "failed"
            receipt["cleanup_errors"].append({"phase": "lean_stop", "id": original["id"],
                                               "service": original.get("service"),
                                               "error_type": type(exc).__name__})
        _write(manifest_path, receipt)


def _restore_base(receipt: dict, manifest_path: Path, runner: Callable) -> None:
    for original in reversed(receipt["base_containers"]):
        if (not original.get("policy_change_attempted")
                or original.get("policy_restore_status") in {"restored", "already_original"}):
            continue
        try:
            current = _inspect(original["id"], runner)
            _same_container(original, current)
            if current["restart_policy"] == original["restart_policy"]:
                original["policy_restore_status"] = "already_original"
            else:
                _set_restart_policy(original, original["restart_policy"], runner)
                original["policy_restore_status"] = "restored"
        except Exception as exc:
            original["policy_restore_status"] = "failed"
            receipt["cleanup_errors"].append({"phase": "base_policy_restore",
                                               "id": original["id"],
                                               "service": original.get("service"),
                                               "error_type": type(exc).__name__})
        _write(manifest_path, receipt)
    for original in reversed(receipt["base_containers"]):
        if (not original.get("stop_attempted")
                or original.get("restore_status") in {"restored", "already_running"}):
            continue
        try:
            current = _inspect(original["id"], runner)
            _same_container(original, current)
            if current["paused"]:
                raise RuntimeError("Base container paused externally; recovery refused")
            if current["running"]:
                original["restore_status"] = "already_running"
            elif current["state"] == "exited":
                runner("start", original["id"], timeout=60)
                current = _inspect(original["id"], runner)
                _same_container(original, current)
                if not current["running"] or current["paused"]:
                    raise RuntimeError("Base container failed to resume")
                original["restore_status"] = "restored"
            else:
                raise RuntimeError("Base container is not in a restorable stopped state")
        except Exception as exc:
            original["restore_status"] = "failed"
            receipt["cleanup_errors"].append({"phase": "base_restore", "id": original["id"],
                                               "service": original.get("service"),
                                               "error_type": type(exc).__name__})
        _write(manifest_path, receipt)


def _restore(receipt: dict, manifest_path: Path, runner: Callable, *, failed: bool) -> dict:
    config = _validate_manifest(receipt)
    if receipt.get("restore_status") == "restored":
        return receipt
    receipt["cleanup_errors"] = []
    try:
        _stop_lean(receipt, config, manifest_path, runner)
    except Exception as exc:
        receipt["cleanup_errors"].append({"phase": "lean_discovery",
                                           "error_type": type(exc).__name__})
    # Never run the full base stack concurrently with an unverified/running
    # lean container. Manual recovery can retry after the lean issue is fixed.
    if not any(item["phase"].startswith("lean_") for item in receipt["cleanup_errors"]):
        _restore_base(receipt, manifest_path, runner)
    else:
        receipt["base_restore_deferred"] = True
    receipt["ended_at"] = _now()
    receipt["restore_status"] = "failed" if receipt["cleanup_errors"] else "restored"
    receipt["status"] = ("restore_failed" if receipt["cleanup_errors"] else
                         "restored_after_error" if failed else "restored")
    _write(manifest_path, receipt)
    if receipt["cleanup_errors"]:
        phases = ", ".join(sorted({item["phase"] for item in receipt["cleanup_errors"]}))
        raise RuntimeError(f"Lean runtime recovery failed in {phases}; inspect {manifest_path}")
    return receipt


def restore_lean_runtime(manifest_path: str | Path, *, runner: Callable = _docker) -> dict:
    """Retry stopping the lean project and resuming exact base IDs."""
    path = Path(manifest_path)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    return _restore(receipt, path, runner, failed=True)


@contextmanager
def lean_runtime(manifest_path: str | Path, config: LeanRuntimeConfig | None = None,
                 *, runner: Callable = _docker, clock: Callable = time.monotonic,
                 sleeper: Callable = time.sleep,
                 stop_requested: Callable[[], bool] | None = None,
                 min_memory_available_percent: float = 25.0,
                 memory_wait_seconds: float = 60.0,
                 memory_reader: Callable[[], dict] = host_memory,
                 reclaim_docker_daemon: bool = False,
                 daemon_reclaimer: Callable | None = None):
    """Run a separate fixed lean topology and restore the exact base project."""
    if (not isinstance(min_memory_available_percent, (int, float))
            or isinstance(min_memory_available_percent, bool)
            or not math.isfinite(min_memory_available_percent)
            or not 10 <= min_memory_available_percent < 100):
        raise ValueError("Host available RAM floor must be at least 10% and below 100%")
    if (not isinstance(memory_wait_seconds, (int, float))
            or isinstance(memory_wait_seconds, bool) or not math.isfinite(memory_wait_seconds)
            or not 0 <= memory_wait_seconds <= 300):
        raise ValueError("Memory readiness wait must be between 0 and 300 seconds")
    if type(reclaim_docker_daemon) is not bool:
        raise ValueError("Docker daemon reclaim flag must be boolean")
    if daemon_reclaimer is not None and not callable(daemon_reclaimer):
        raise ValueError("Docker daemon reclaimer must be callable")
    config = config or LeanRuntimeConfig()
    path = Path(manifest_path)
    if path.exists():
        raise FileExistsError("Use a new lean-runtime recovery manifest")
    engine = _engine(runner)
    base = _discover(config.base_project, runner)
    if not base:
        raise RuntimeError("Base Compose project containers are unavailable")
    if any(item["paused"] for item in base):
        raise RuntimeError("Paused base containers must be resolved before a lean experiment")
    preexisting = _discover(config.lean_project, runner)
    if any(item["running"] or item["paused"] for item in preexisting):
        raise RuntimeError("Lean Compose project is already active; recover it first")
    if any(item["service"] not in config.allowed_services for item in preexisting):
        raise RuntimeError("Lean Compose project contains an unexpected service")
    receipt = {
        "schema_version": 1, "kind": "llm-lean-runtime", "status": "preparing",
        "started_at": _now(), "config": {
            "base_project": config.base_project, "lean_project": config.lean_project,
            "compose_path": str(Path(config.compose_path).resolve()),
            "project_directory": str(Path(config.project_directory).resolve()),
            "running_services": list(config.running_services),
            "completed_services": list(config.completed_services),
            "ready_timeout_seconds": config.ready_timeout_seconds,
        }, "engine": engine, "file_hashes": _file_hashes(config),
        "memory_floor_percent": min_memory_available_percent,
        "memory_wait_seconds": memory_wait_seconds,
        "daemon_reclaim_enabled": reclaim_docker_daemon,
        "daemon_reclaim_attempted": False, "daemon_reclaim_completed": False,
        "base_containers": base, "preexisting_lean_containers": preexisting,
        "build_attempted": False, "build_completed": False,
        "lean_create_attempted": False, "lean_containers": None,
        "cleanup_errors": [],
    }
    _write(path, receipt)
    body_failed = False
    try:
        for original in receipt["base_containers"]:
            if not original["running"]:
                continue
            if stop_requested is not None and stop_requested():
                raise RuntimeError("Lean runtime preparation cancelled")
            current = _inspect(original["id"], runner)
            _same_container(original, current)
            if not current["running"] or current["paused"]:
                raise RuntimeError("Base container state changed before isolation")
            original["stop_attempted"] = True
            _write(path, receipt)
            try:
                runner("stop", "--time", "20", original["id"], timeout=35)
            finally:
                current = _inspect(original["id"], runner)
                _same_container(original, current)
                original["stop_confirmed"] = (not current["running"] and not current["paused"]
                                                and current["state"] == "exited")
                _write(path, receipt)
            if not original["stop_confirmed"]:
                raise RuntimeError("Docker did not confirm a base container stopped")
        if reclaim_docker_daemon:
            no_restart = {"name": "no", "maximum_retry_count": 0}
            for original in receipt["base_containers"]:
                original["policy_change_attempted"] = True
                _write(path, receipt)
                if original["restart_policy"] == no_restart:
                    original["policy_change_status"] = "already_disabled"
                else:
                    _set_restart_policy(original, no_restart, runner)
                    original["policy_change_status"] = "disabled"
                _write(path, receipt)
            receipt["daemon_reclaim_attempted"] = True
            _write(path, receipt)
            (daemon_reclaimer or reclaim_docker_desktop)(
                runner=runner, clock=clock, sleeper=sleeper)
            receipt["daemon_reclaim_completed"] = True
            _write(path, receipt)
            current_base = {item["id"]: item for item in _discover(config.base_project, runner)}
            if set(current_base) != {item["id"] for item in receipt["base_containers"]}:
                raise RuntimeError("Base project membership changed across Docker restart")
            for original in receipt["base_containers"]:
                current = current_base[original["id"]]
                _same_container(original, current)
                if (current["running"] or current["paused"] or current["state"] != "exited"
                        or current["restart_policy"] != no_restart):
                    raise RuntimeError("Base project resumed across Docker restart")
        # Build from the current checkout only after the memory-heavy base stack
        # is stopped. Container identities are captured later and never rebuilt
        # during the measured session.
        receipt["build_attempted"] = True
        _write(path, receipt)
        _compose(config, runner, "build", "--pull=false", "processor", timeout=900)
        receipt["build_completed"] = True
        _write(path, receipt)
        receipt["lean_create_attempted"] = True
        _write(path, receipt)
        _compose(config, runner, "create", "--no-build", timeout=240)
        created = _discover(config.lean_project, runner)
        _validate_lean(config, created, ready=False)
        receipt["lean_containers"] = created
        _write(path, receipt)
        _compose(config, runner, "up", "-d", "--no-recreate", "--no-build", timeout=240)
        ready = _wait_ready(config, runner, clock, sleeper, stop_requested)
        before = {item["id"]: item for item in receipt["lean_containers"]}
        if set(before) != {item["id"] for item in ready}:
            raise RuntimeError("Lean Compose recreated a container after the durable snapshot")
        for item in ready:
            _same_container(before[item["id"]], item)
        receipt["lean_containers"] = ready
        receipt["lean_snapshot"] = _topology_snapshot(config, ready, engine, receipt["file_hashes"])
        _wait_memory(receipt, path, min_memory_available_percent, memory_wait_seconds,
                     clock, sleeper, stop_requested, memory_reader)
        receipt["status"] = "active"
        _write(path, receipt)
        verify_lean_runtime(receipt, runner=runner)
        yield receipt
    except BaseException as exc:
        body_failed = True
        receipt["failure_type"] = type(exc).__name__
        raise
    finally:
        _restore(receipt, path, runner, failed=body_failed)
