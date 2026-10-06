"""Explicit, reversible reduction of optional services during a telemetry session."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile


OPTIONAL_SERVICES = frozenset({
    "airflow", "trainer", "scraper", "api", "predictor", "frontend", "grafana",
    "mongo-express", "ai-agent", "stress-agent",
})
PROJECT_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_LABELS = (
    "com.docker.compose.project", "com.docker.compose.service",
    "com.docker.compose.config-hash",
)


def _docker(*arguments, timeout=30):
    """Do not propagate stdout/stderr, which may contain sensitive configuration."""
    try:
        result = subprocess.run(
            ["docker", *arguments], cwd=PROJECT_ROOT, capture_output=True,
            text=True, encoding="utf-8", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Docker {arguments[0]} failed ({type(exc).__name__})") from None
    if result.returncode:
        raise RuntimeError(f"Docker {arguments[0]} failed (exit {result.returncode})")
    return result.stdout


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(path, receipt):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _inspect(container_id):
    values = json.loads(_docker("inspect", container_id))
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError("Docker inspect returned an unexpected container count")
    item = values[0]
    labels = item.get("Config", {}).get("Labels") or {}
    state = item.get("State", {})
    return {
        "id": item["Id"], "name": item.get("Name", "").lstrip("/"),
        "service": labels.get("com.docker.compose.service"),
        "identity": {key: labels.get(key) for key in IDENTITY_LABELS},
        "state": state.get("Status"), "running": bool(state.get("Running")),
        "paused": bool(state.get("Paused")),
    }


def _same_container(original, current):
    if (current["id"] != original["id"] or current["identity"] != original["identity"]
            or current["name"] != original["name"]):
        raise RuntimeError("Container identity/configuration changed; refusing to alter it")


def _discover():
    raw = _docker("compose", "ps", "--all", "--format", "json").strip()
    if not raw:
        return []
    rows = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines()]
    snapshots = []
    for row in rows:
        if row.get("Service") not in OPTIONAL_SERVICES:
            continue
        snapshot = _inspect(row["ID"])
        if snapshot["service"] != row["Service"]:
            raise RuntimeError("Compose service identity changed during discovery")
        snapshot["selected"] = snapshot["running"] and not snapshot["paused"]
        snapshots.append(snapshot)
    return snapshots


@contextmanager
def lean_environment(enabled: bool, receipt_path: Path):
    """Stop only running optional containers and restore the same IDs on exit.

    Call under the experiment lock. No restart, recreation, deletion, environment
    edits or Docker queries occur when disabled. SIGKILL/power loss cannot execute
    finally: the receipt retains exact IDs and progress for manual recovery.
    """
    if not enabled:
        yield {"enabled": False}
        return
    receipt = {
        "enabled": True, "started_at": _now(), "status": "preparing",
        "containers": _discover(), "restore_errors": [],
    }
    selected = [item for item in receipt["containers"] if item["selected"]]
    _write(receipt_path, receipt)  # Persist the plan before touching any container.
    print("Lean session temporarily stops optional services: " +
          (", ".join(item["service"] for item in selected) or "none"), flush=True)
    body_failed = False
    try:
        for original in selected:
            current = _inspect(original["id"])
            _same_container(original, current)
            if not current["running"] or current["paused"]:
                raise RuntimeError("Optional container state changed before stopping")
            original["stop_attempted"] = True
            _write(receipt_path, receipt)
            try:
                _docker("stop", "--time", "30", original["id"], timeout=45)
            finally:
                # A timeout/nonzero command can still have stopped its container.
                current = _inspect(original["id"])
                _same_container(original, current)
                original["stop_confirmed"] = not current["running"] and current["state"] == "exited"
                _write(receipt_path, receipt)
            if not original["stop_confirmed"]:
                raise RuntimeError("Docker did not confirm the optional container stopped")
        receipt["status"] = "active"
        _write(receipt_path, receipt)
        yield receipt
    except BaseException as exc:
        body_failed = True
        receipt["failure_type"] = type(exc).__name__
        raise
    finally:
        receipt_write_failed = False
        for original in reversed(selected):
            if not original.get("stop_attempted"):
                continue
            try:
                current = _inspect(original["id"])
                _same_container(original, current)
                # Reconcile an uncertain stop (e.g. an inspect timeout) without
                # ever starting a container that was initially stopped/paused.
                if current["paused"]:
                    raise RuntimeError("Container was paused externally; restore refused")
                if current["running"]:
                    original["restore_status"] = "already_running"
                elif current["state"] == "exited":
                    _docker("start", original["id"], timeout=45)
                    restored = _inspect(original["id"])
                    _same_container(original, restored)
                    if not restored["running"] or restored["paused"]:
                        raise RuntimeError("Container failed to resume running")
                    original["restore_status"] = "restored"
                else:
                    raise RuntimeError("Container is not in a restorable stopped state")
            except Exception as exc:
                original["restore_status"] = "failed"
                receipt["restore_errors"].append({
                    "id": original["id"], "service": original["service"],
                    "error_type": type(exc).__name__,
                })
            try:
                _write(receipt_path, receipt)
            except Exception:
                # A full disk must not prevent restoration of the other services.
                receipt_write_failed = True
        receipt["ended_at"] = _now()
        receipt["status"] = ("restore_failed" if receipt["restore_errors"] else
                             "restored_after_error" if body_failed else "restored")
        try:
            _write(receipt_path, receipt)
        except Exception:
            receipt_write_failed = True
        if receipt["restore_errors"]:
            services = ", ".join(item["service"] for item in receipt["restore_errors"])
            raise RuntimeError(f"Lean session could not restore: {services}; inspect {receipt_path}")
        if receipt_write_failed:
            raise RuntimeError(f"Lean services restored but receipt update failed: {receipt_path}")
