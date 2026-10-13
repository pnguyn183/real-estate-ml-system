"""Reversible, fixed-allocation isolation for rate-only LLM experiments.

Use under the shared research experiment lock. Recovery manifests contain only
container identity and allocation metadata, never container environment values.
No container is recreated and no Compose, environment or resource limit is edited.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import time

import psutil

from research.lean_runtime import PROJECT_ROOT, _docker, _now, _write


PROCESSORS = frozenset({"processor", "processor-2", "processor-3"})
ISOLATED_SERVICES = frozenset({
    "stress-agent", "scraper", "trainer", "airflow", "ai-agent", "api",
    "predictor", "frontend", "grafana", "mongo-express",
})
IDENTITY_LABELS = (
    "com.docker.compose.project", "com.docker.compose.service",
    "com.docker.compose.config-hash", "com.docker.compose.oneoff",
)
LIMIT_KEYS = (
    "NanoCpus", "CpuPeriod", "CpuQuota", "Memory", "MemorySwap",
    "MemoryReservation", "CpuShares", "CpusetCpus", "CpusetMems", "PidsLimit",
)


def _project(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise ValueError("An explicit valid Compose project name is required")
    return value


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def _container(value):
    labels = value.get("Config", {}).get("Labels") or {}
    state, host = value.get("State", {}), value.get("HostConfig", {})
    identifier = value.get("Id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise RuntimeError("Docker did not return a complete container identity")
    return {
        "id": identifier, "name": value.get("Name", "").lstrip("/"),
        "service": labels.get("com.docker.compose.service"),
        "identity": {key: labels.get(key) for key in IDENTITY_LABELS},
        "image": value.get("Image"), "limits": {key: host.get(key) for key in LIMIT_KEYS},
        "state": state.get("Status"), "running": state.get("Running") is True,
        "paused": state.get("Paused") is True, "started_at": state.get("StartedAt"),
        "restart_count": value.get("RestartCount"),
    }


def _inspect(identifier):
    values = json.loads(_docker("inspect", identifier))
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError("Docker inspect returned an unexpected container count")
    return _container(values[0])


def _same_container(original, current):
    for key in ("id", "name", "identity", "image", "limits"):
        if original[key] != current[key]:
            raise RuntimeError("Container identity or allocation changed; restoration refused")


def _fingerprint(snapshot):
    active = [{key: item[key] for key in (
        "id", "name", "service", "identity", "image", "limits", "started_at", "restart_count",
    )} for item in snapshot["containers"] if item["running"] and not item["paused"]]
    return _digest({"engine": snapshot["engine"], "active": sorted(active, key=lambda x: x["id"])})


def _validate_processors(containers):
    limits = {}
    for service in sorted(PROCESSORS):
        matches = [item for item in containers if item["service"] == service]
        if len(matches) != 1:
            raise RuntimeError(f"Exactly one container is required for {service}")
        item, value = matches[0], matches[0]["limits"]
        if not item["running"] or item["paused"]:
            raise RuntimeError(f"Processor is not running: {service}")
        nano, period, quota, memory = (value.get(key) for key in
                                     ("NanoCpus", "CpuPeriod", "CpuQuota", "Memory"))
        if _finite(nano) and nano > 0:
            cpus = nano / 1_000_000_000
        elif _finite(quota) and quota > 0 and _finite(period) and period > 0:
            cpus = quota / period
        else:
            raise RuntimeError(f"An explicit finite CPU limit is required for {service}")
        if not _finite(memory) or memory <= 0:
            raise RuntimeError(f"An explicit finite RAM limit is required for {service}")
        limits[service] = {"id": item["id"], "cpus": cpus, "memory_bytes": memory,
                           "limits": value}
    return limits


def snapshot_runtime(project_name="real-estate-ml-system"):
    """Read actual Docker identities and limits; fail before any mutation if unknown."""
    _project(project_name)
    info = json.loads(_docker("info", "--format", "{{json .}}"))
    engine = {"cpus": info.get("NCPU"), "memory_bytes": info.get("MemTotal"),
              "os_type": info.get("OSType")}
    if (not _finite(engine["cpus"]) or engine["cpus"] <= 0
            or not _finite(engine["memory_bytes"]) or engine["memory_bytes"] <= 0
            or engine["os_type"] != "linux"):
        raise RuntimeError("Docker CPU/RAM capacity or Linux engine is unavailable")
    identifiers = _docker("ps", "--all", "--no-trunc", "--quiet", "--filter",
                          f"label=com.docker.compose.project={project_name}").split()
    if not identifiers or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in identifiers):
        raise RuntimeError("Compose project containers are unavailable")
    values = json.loads(_docker("inspect", *identifiers))
    if not isinstance(values, list) or len(values) != len(identifiers):
        raise RuntimeError("Docker discovery returned an unexpected container count")
    containers = sorted((_container(value) for value in values), key=lambda item: item["id"])
    if ({item["id"] for item in containers} != set(identifiers)
            or any(item["identity"][IDENTITY_LABELS[0]] != project_name for item in containers)):
        raise RuntimeError("Compose project identity changed during discovery")
    snapshot = {"project_name": project_name, "engine": engine, "containers": containers,
                "processors": _validate_processors(containers)}
    snapshot["fingerprint"] = _fingerprint(snapshot)
    return snapshot


def host_memory():
    value = psutil.virtual_memory()
    if value.total <= 0:
        raise RuntimeError("Host RAM capacity is unavailable")
    return {"memory_available_percent": 100 * value.available / value.total,
            "memory_available_bytes": value.available, "memory_total_bytes": value.total}


def _file_hashes():
    return {name: hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
            for name in ("docker-compose.yml", ".env") if (PROJECT_ROOT / name).is_file()}


def _model_paths():
    return [path for path in sorted((PROJECT_ROOT / "artifacts").rglob("*"))
            if path.is_file() and (path.suffix == ".joblib" or path.name == "price_model_metrics.json")]


def _model_hashes():
    # Start this snapshot only after training containers have stopped. Normal
    # scheduled fitting may resume after restoration and is outside the trial.
    return {str(path.relative_to(PROJECT_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in _model_paths()}


def _model_metadata():
    result = {}
    for path in _model_paths():
        stat = path.stat()
        result[str(path.relative_to(PROJECT_ROOT))] = {
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
        }
    return result


def verify_runtime(receipt, *, deep_artifact_check=True):
    """Require unchanged identities, allocations, isolation and model artifacts.

    Intermediate samples may use metadata checks to avoid repeatedly hashing
    archived models. Keep full SHA-256 verification before and after every arm.
    Docker checks remain synchronous and should run off the admission/guard loop.
    """
    current = snapshot_runtime(receipt["project_name"])
    if current["fingerprint"] != receipt["isolated_snapshot"]["fingerprint"]:
        raise RuntimeError("Experiment container membership, allocation or process identity changed")
    if any(item["running"] and not item["paused"] and item["service"] in ISOLATED_SERVICES
           for item in current["containers"]):
        raise RuntimeError("A competing publisher, training or optional service resumed")
    if _file_hashes() != receipt["file_hashes"]:
        raise RuntimeError("Compose or environment file changed during the experiment")
    if _model_metadata() != receipt["model_metadata"]:
        raise RuntimeError("Model artifacts changed during the isolated experiment")
    if deep_artifact_check and _model_hashes() != receipt["model_hashes"]:
        raise RuntimeError("Model artifacts changed during the isolated experiment")
    return {"fingerprint": current["fingerprint"], "processors": current["processors"],
            "models_unchanged": True, "files_unchanged": True,
            "artifact_verification": "sha256" if deep_artifact_check else "file_metadata"}


def _validate_manifest(receipt):
    if receipt.get("schema_version") != 1 or receipt.get("kind") != "llm-rate-experiment":
        raise ValueError("Not an LLM rate experiment recovery manifest")
    project_name = _project(receipt.get("project_name"))
    containers = receipt.get("containers")
    if not isinstance(containers, list):
        raise ValueError("Recovery manifest has no container snapshots")
    identifiers = set()
    for item in containers:
        identifier = item.get("id", "")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier)
                or identifier in identifiers):
            raise ValueError("Recovery manifest has an invalid or repeated container ID")
        identifiers.add(identifier)
        if not item.get("stop_attempted"):
            continue
        if (item.get("service") not in ISOLATED_SERVICES or item.get("running") is not True
                or item.get("paused") is not False
                or item.get("identity", {}).get(IDENTITY_LABELS[0]) != project_name
                or item.get("identity", {}).get(IDENTITY_LABELS[1]) != item["service"]):
            raise ValueError("Recovery manifest cannot start a container outside its original running set")
        if not all(key in item for key in ("name", "image", "limits")):
            raise ValueError("Recovery manifest has incomplete container identity")


def _restore(receipt, manifest_path, *, failed=False, on_event=None):
    _validate_manifest(receipt)
    receipt["restore_errors"] = []
    write_failed = False
    for original in reversed(receipt["containers"]):
        if (not original.get("stop_attempted")
                or original.get("restore_status") in {"restored", "already_running"}):
            # Recovery can be retried after a crash or a partial restore. A
            # completed action is not authority to restart a service that the
            # operator has subsequently stopped.
            continue
        try:
            current = _inspect(original["id"])
            _same_container(original, current)
            if current["paused"]:
                raise RuntimeError("Container paused externally; restoration refused")
            if current["running"]:
                original["restore_status"] = "already_running"
            elif current["state"] == "exited":
                _docker("start", original["id"], timeout=45)
                current = _inspect(original["id"])
                _same_container(original, current)
                if not current["running"] or current["paused"]:
                    raise RuntimeError("Original container failed to resume")
                original["restore_status"] = "restored"
            else:
                raise RuntimeError("Container is not in a restorable stopped state")
        except Exception as exc:
            original["restore_status"] = "failed"
            receipt["restore_errors"].append({"id": original["id"], "service": original["service"],
                                              "error_type": type(exc).__name__})
        try:
            _write(manifest_path, receipt)
        except Exception:
            write_failed = True  # A full disk must not strand the other containers.
        if on_event is not None:
            try:
                on_event({"event": "restore", "service": original["service"],
                          "status": original["restore_status"]})
            except Exception:
                pass  # Progress reporting must never interrupt cleanup.
    receipt["ended_at"] = _now()
    receipt["status"] = ("restore_failed" if receipt["restore_errors"] else
                         "restored_after_error" if failed else "restored")
    try:
        _write(manifest_path, receipt)
    except Exception:
        write_failed = True
    if receipt["restore_errors"]:
        services = ", ".join(item["service"] for item in receipt["restore_errors"])
        raise RuntimeError(f"Experiment could not restore: {services}; inspect {manifest_path}")
    if write_failed:
        raise RuntimeError(f"Services restored but recovery manifest update failed: {manifest_path}")
    return receipt


def restore_runtime(manifest_path):
    """Recover an interrupted trial using only its original, unchanged container IDs."""
    manifest_path = Path(manifest_path)
    receipt = json.loads(manifest_path.read_text(encoding="utf-8"))
    return _restore(receipt, manifest_path, failed=True)


@contextmanager
def isolated_runtime(manifest_path, *, project_name="real-estate-ml-system",
                     min_memory_available_percent=10.0, memory_wait_seconds=60.0,
                     stop_requested=None, on_wait=None, on_event=None):
    """Stop competing work, keep actual limits fixed, restore exact original IDs.

    The host RAM floor may be strengthened but cannot be lowered below 10%.
    Call ``verify_runtime(receipt)`` before and after each paired workload.
    """
    if (not _finite(min_memory_available_percent)
            or not 10 <= min_memory_available_percent < 100):
        raise ValueError("Host available RAM floor must be at least 10% and below 100%")
    if not _finite(memory_wait_seconds) or not 0 <= memory_wait_seconds <= 300:
        raise ValueError("Memory readiness wait must be between 0 and 300 seconds")
    manifest_path = Path(manifest_path)
    if manifest_path.exists():
        raise FileExistsError("Use a new recovery manifest; do not overwrite an earlier trial")
    initial = snapshot_runtime(project_name)
    receipt = {"schema_version": 1, "kind": "llm-rate-experiment", "project_name": project_name,
               "started_at": _now(), "status": "preparing", "initial_snapshot": initial,
               "containers": initial["containers"], "file_hashes": _file_hashes(),
               "restore_errors": [], "memory_floor_percent": min_memory_available_percent,
               "training_note": "Training is stopped for the trial and may fit on normal resume."}
    _write(manifest_path, receipt)  # Durable recovery plan before the first mutation.
    failed = False
    try:
        for original in receipt["containers"]:
            if (original["service"] not in ISOLATED_SERVICES or not original["running"]
                    or original["paused"]):
                continue
            if stop_requested is not None and stop_requested():
                raise RuntimeError("Experiment isolation cancelled")
            current = _inspect(original["id"])
            _same_container(original, current)
            if not current["running"] or current["paused"]:
                raise RuntimeError("Container state changed before experiment isolation")
            original["stop_attempted"] = True
            _write(manifest_path, receipt)
            if on_event is not None:
                on_event({"event": "stop", "service": original["service"]})
            try:
                _docker("stop", "--time", "20", original["id"], timeout=35)
            finally:
                current = _inspect(original["id"])
                _same_container(original, current)
                original["stop_confirmed"] = not current["running"] and current["state"] == "exited"
                _write(manifest_path, receipt)
            if not original["stop_confirmed"]:
                raise RuntimeError("Docker did not confirm a competing container stopped")
        deadline = time.monotonic() + memory_wait_seconds
        while True:
            if stop_requested is not None and stop_requested():
                raise RuntimeError("Experiment isolation cancelled")
            memory = host_memory()
            receipt["host_before_trial"] = memory
            _write(manifest_path, receipt)
            available = memory.get("memory_available_percent")
            if _finite(available) and available > min_memory_available_percent:
                break
            if on_wait is not None:
                on_wait(memory)
            if time.monotonic() >= deadline:
                raise RuntimeError("Host available RAM is at or below the experiment safety floor")
            time.sleep(min(2.0, max(0, deadline - time.monotonic())))
        isolated = snapshot_runtime(project_name)
        if any(item["running"] and not item["paused"] and item["service"] in ISOLATED_SERVICES
               for item in isolated["containers"]):
            raise RuntimeError("A competing service is still running after isolation")
        if isolated["processors"] != initial["processors"] or isolated["engine"] != initial["engine"]:
            raise RuntimeError("Docker processor allocations changed during isolation")
        receipt["isolated_snapshot"] = isolated
        receipt["model_hashes"] = _model_hashes()
        receipt["model_metadata"] = _model_metadata()
        receipt["status"] = "active"
        verify_runtime(receipt)
        _write(manifest_path, receipt)
        yield receipt
    except BaseException as exc:
        failed = True
        receipt["failure_type"] = type(exc).__name__
        raise
    finally:
        _restore(receipt, manifest_path, failed=failed, on_event=on_event)
