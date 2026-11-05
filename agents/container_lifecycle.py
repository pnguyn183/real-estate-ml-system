"""Bounded container stop/start tools for model-selected load management.

Only explicitly dispensable Compose services are eligible. The journal records
intent before commands and ownership after confirmation, survives restarts, and never authorizes
starting a container that this controller did not stop. Observation is read-only.
No container is removed, recreated, paused, or selected from a model-supplied ID.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from uuid import uuid4


MIB = 1024 ** 2
DEFAULT_OPTIONAL_SERVICES = ("trainer", "grafana", "mongo-express")
OPTIONAL_SERVICES = (*DEFAULT_OPTIONAL_SERVICES, "frontend", "api", "predictor")
IDENTITY_LABELS = ("com.docker.compose.project", "com.docker.compose.service",
                   "com.docker.compose.config-hash", "com.docker.compose.oneoff")
# Conservative fallback for containers built by older Compose versions without
# the depends_on label. Live Compose dependency labels add to these constraints.
DEPENDENCIES = {"trainer": {"mongodb": "service_healthy"},
                "grafana": {"prometheus": "service_healthy"},
                "mongo-express": {"mongodb": "service_healthy"},
                "frontend": {"api": "service_healthy"},
                "api": {"mongodb": "service_healthy"}}


class LifecycleError(RuntimeError):
    """Sanitized lifecycle error safe to include in a decision receipt."""


class LifecycleDeferred(LifecycleError):
    """A temporary guard prevented a container action."""


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _canonical_path(value):
    value = str(value).replace("\\", "/").lower().rstrip("/")
    for prefix in ("/run/desktop/mnt/host/", "/host_mnt/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            value = value[0] + ":" + value[1:]
    return value


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=True, allow_nan=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        deadline = time.monotonic() + 1
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ContainerLifecycleConfig:
    project_name: str
    state_path: Path
    policy_path: Path
    enabled: bool = False
    allowed_services: tuple[str, ...] = DEFAULT_OPTIONAL_SERVICES
    cooldown_seconds: float = 60.0
    max_sample_age_seconds: float = 30.0
    min_memory_available_percent: float = 10.0
    command_timeout_seconds: float = 15.0
    drain_timeout_seconds: float = 10.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("Lifecycle enabled must be a boolean")
        if not isinstance(self.project_name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.project_name):
            raise ValueError("Lifecycle requires an explicit Compose project")
        if (not isinstance(self.allowed_services, (tuple, list)) or not self.allowed_services
                or any(s not in OPTIONAL_SERVICES for s in self.allowed_services)
                or len(set(self.allowed_services)) != len(self.allowed_services)):
            raise ValueError("Only unique optional services may be controlled")
        object.__setattr__(self, "allowed_services", tuple(self.allowed_services))
        for name in ("state_path", "policy_path"):
            object.__setattr__(self, name, Path(getattr(self, name)).resolve())
        if self.state_path == self.policy_path:
            raise ValueError("Lifecycle journal and ingress policy must be separate")
        for name in ("cooldown_seconds", "max_sample_age_seconds", "min_memory_available_percent",
                     "command_timeout_seconds", "drain_timeout_seconds"):
            if not _finite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"Invalid lifecycle setting: {name}")
        if (not 10 <= self.min_memory_available_percent < 100
                or self.command_timeout_seconds > 45 or self.command_timeout_seconds < 2
                or self.drain_timeout_seconds > 60 or self.max_sample_age_seconds > 120
                or self.cooldown_seconds > 3600):
            raise ValueError("Lifecycle bounds exceed supported limits")


class ContainerLifecycleActuator:
    def __init__(self, config: ContainerLifecycleConfig, runner=None, clock=time.time, sleep=time.sleep):
        self.config = config
        self._runner = runner or subprocess.run
        self.clock, self.sleep = clock, sleep
        self._last_observation = None
        self._last_inventory = None

    def _command(self, *args):
        try:
            return self._runner(["docker", *args], check=True, capture_output=True, text=True,
                                timeout=self.config.command_timeout_seconds).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise LifecycleError(f"Docker {args[0]} failed ({type(exc).__name__})") from None

    def _json_command(self, *args):
        try:
            return json.loads(self._command(*args))
        except (ValueError, TypeError):
            raise LifecycleError("Docker returned invalid lifecycle JSON") from None

    @contextmanager
    def _lock(self):
        path = self.config.state_path.with_suffix(self.config.state_path.suffix + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise LifecycleDeferred("Another lifecycle action is active") from None
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _state(self):
        if not self.config.state_path.exists():
            return {"schema_version": 1, "project_name": self.config.project_name, "services": {}}
        try:
            value = json.loads(self.config.state_path.read_text(encoding="utf-8"))
            if (value.get("schema_version") != 1 or value.get("project_name") != self.config.project_name
                    or not isinstance(value.get("services"), dict)):
                raise ValueError
            for service, entry in value["services"].items():
                if (service not in OPTIONAL_SERVICES or not isinstance(entry, dict)
                        or not re.fullmatch(r"[a-f0-9]{64}", entry.get("id", ""))
                        or type(entry.get("owned_stop")) is not bool
                        or not isinstance(entry.get("identity"), dict)
                        or not _finite(entry.get("last_action_at"))
                        or not _finite(entry.get("memory_usage_bytes"))
                        or entry["memory_usage_bytes"] < 0):
                    raise ValueError
            return value
        except (OSError, ValueError, TypeError, AttributeError):
            raise LifecycleError("Lifecycle journal is invalid; automatic recovery is blocked") from None

    def _snapshot(self, raw):
        labels = raw.get("Config", {}).get("Labels") or {}
        service, cid = labels.get("com.docker.compose.service"), raw.get("Id", "")
        if (not re.fullmatch(r"[a-f0-9]{64}", cid)
                or labels.get("com.docker.compose.project") != self.config.project_name
                or not isinstance(service, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", service)
                or labels.get("com.docker.compose.oneoff", "false").lower() != "false"):
            raise LifecycleError("Container project or immutable identity is invalid")
        state = raw.get("State", {})
        running, paused, restarting = (state.get(k) is True for k in ("Running", "Paused", "Restarting"))
        health = state.get("Health", {}).get("Status", "none")
        if health not in {"none", "healthy", "unhealthy", "starting"}:
            raise LifecycleError("Unknown container health state")
        dependencies = dict(DEPENDENCIES.get(service, {}))
        for part in labels.get("com.docker.compose.depends_on", "").split(","):
            if part:
                fields = part.split(":")
                if not re.fullmatch(r"[a-zA-Z0-9_.-]+", fields[0]):
                    raise LifecycleError("Invalid Compose dependency label")
                dependencies[fields[0]] = fields[1] if len(fields) > 1 else "service_started"
        env = dict(pair.split("=", 1) for pair in raw.get("Config", {}).get("Env", []) if "=" in pair)
        mounts = [m for m in raw.get("Mounts", []) if m.get("Destination") == "/app/runtime/control"]
        controlled = (env.get("CONTROL_ENABLED", "").lower() == "true"
                      and env.get("CONTROL_POLICY_PATH") == "/app/runtime/control/policy.json"
                      and len(mounts) == 1 and mounts[0].get("RW") is True
                      and _canonical_path(mounts[0].get("Source")) == _canonical_path(self.config.policy_path.parent))
        return {"id": cid, "service": service, "name": raw.get("Name", "").lstrip("/"),
                "identity": {key: labels.get(key) for key in IDENTITY_LABELS},
                "hostname": raw.get("Config", {}).get("Hostname"),
                "state": state.get("Status"), "running": running, "paused": paused,
                "restarting": restarting, "health": health,
                "started_at": state.get("StartedAt"), "finished_at": state.get("FinishedAt"),
                "ready": running and not paused and not restarting and health in {"none", "healthy"},
                "exit_code": state.get("ExitCode"), "dependencies": dependencies,
                "trainer_controlled": controlled, "source_topic": env.get("CONTROL_SOURCE_TOPIC")}

    def _discover(self):
        ids = self._command("ps", "--all", "--no-trunc", "--quiet", "--filter",
                            f"label=com.docker.compose.project={self.config.project_name}",
                            "--filter", "label=com.docker.compose.oneoff=False").split()
        if not ids:
            return {}
        if any(not re.fullmatch(r"[a-f0-9]{64}", cid) for cid in ids) or len(ids) > 100:
            raise LifecycleError("Unexpected Compose container discovery")
        rows = self._json_command("inspect", *ids)
        if not isinstance(rows, list) or len(rows) != len(ids):
            raise LifecycleError("Incomplete Docker lifecycle inventory")
        inventory = {}
        for raw in rows:
            item = self._snapshot(raw)
            if item["id"] not in ids or item["service"] in inventory:
                raise LifecycleError("Ambiguous Compose service identity")
            inventory[item["service"]] = item
        return inventory

    def _inspect(self, identifier):
        rows = self._json_command("inspect", identifier)
        if not isinstance(rows, list) or len(rows) != 1:
            raise LifecycleError("Docker lifecycle inspect returned an unexpected count")
        item = self._snapshot(rows[0])
        if item["id"] != identifier:
            raise LifecycleError("Container identity changed during lifecycle action")
        return item

    @staticmethod
    def _same(before, after):
        if any(before.get(k) != after.get(k) for k in ("id", "identity", "name", "hostname")):
            raise LifecycleError("Container identity or Compose configuration changed")

    def _stats(self, inventory):
        targets = [v for k, v in inventory.items() if k in self.config.allowed_services and v["running"] and not v["paused"]]
        if not targets:
            return {}
        text = self._command("stats", "--no-stream", "--no-trunc", "--format", "{{json .}}",
                             *(item["id"] for item in targets))
        from research.telemetry import size_bytes
        result = {}
        try:
            for line in text.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                cid = row["ID"]
                cpu = float(row["CPUPerc"].rstrip("%"))
                memory = size_bytes(row["MemUsage"].split("/")[0])
                if cid in result or not _finite(cpu) or cpu < 0 or not _finite(memory) or memory < 0:
                    raise ValueError
                result[cid] = {"memory_usage_bytes": int(memory), "cpu_percent": cpu,
                               "memory_measurement": "docker_cli_working_set_rounded"}
            if set(result) != {v["id"] for v in targets}:
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LifecycleError("Optional container telemetry is unavailable or invalid") from None
        return result

    def _dependency_problem(self, service, action, inventory):
        item = inventory[service]
        if action == "stop":
            if any(v["running"] and service in v["dependencies"] for v in inventory.values()):
                return "A running service depends on this container"
        else:
            for dependency, condition in item["dependencies"].items():
                target = inventory.get(dependency)
                if condition == "service_completed_successfully":
                    if not target or target["running"] or target["exit_code"] != 0:
                        return "A required setup dependency has not completed successfully"
                elif (not target or not target["ready"]
                      or condition == "service_healthy" and target["health"] != "healthy"):
                    return "A required dependency is not ready"
        return None

    def _eligible(self, service, item, entry, inventory):
        if not item["identity"].get("com.docker.compose.config-hash"):
            return [], "Compose configuration identity is missing"
        if entry:
            try:
                self._same(entry, item)
            except LifecycleError:
                return [], "Container was replaced; previous lifecycle ownership does not transfer"
            age = self.clock() - entry["last_action_at"]
            if age < 0 or age < self.config.cooldown_seconds:
                return [], "Lifecycle cooldown is active"
        if item["paused"] or item["restarting"]:
            return [], "Externally paused or restarting container is protected"
        if item["running"]:
            if entry and entry["owned_stop"] and entry.get("phase") in {"stop_requested", "stop_pending", "stopped"}:
                return [], "Owned stop is unresolved or the container was started externally"
            if not item["ready"]:
                return [], "Container has not become ready"
            if service == "trainer" and not item["trainer_controlled"]:
                return [], "Trainer is not using this controlled policy directory"
            problem = self._dependency_problem(service, "stop", inventory)
            return ([], problem) if problem else (["stop"], "Optional running service")
        if item["state"] != "exited":
            return [], "Only exited containers can be restored"
        if not entry or not entry["owned_stop"]:
            return [], "Container was not stopped by this controller"
        if not self._owns_stop(entry, item):
            return [], "Container lifecycle changed externally or the stop outcome is uncertain"
        problem = self._dependency_problem(service, "start", inventory)
        return ([], problem) if problem else (["start"], "Controller-owned stopped service")

    @staticmethod
    def _owns_stop(entry, item):
        # A previous stop does not authorize undoing a later operator stop. Docker
        # timestamps also detect an external start/stop between two observations.
        return bool(entry and entry.get("owned_stop") and entry.get("phase") == "stopped"
                    and all(entry.get(k) == item.get(k) for k in ("id", "identity", "name", "hostname"))
                    and item["state"] == "exited" and not item["running"]
                    and entry.get("stopped_started_at") == item.get("started_at")
                    and entry.get("stopped_finished_at") == item.get("finished_at")
                    and isinstance(item.get("started_at"), str)
                    and isinstance(item.get("finished_at"), str))

    def _observation(self):
        state, inventory = self._state(), self._discover()
        stats = self._stats(inventory)
        services = {}
        for service in self.config.allowed_services:
            item = inventory.get(service)
            entry = state["services"].get(service)
            if item is None:
                if entry and entry["owned_stop"]:
                    services[service] = {"id": entry["id"], "state": "missing", "running": False,
                        "paused": False, "health": "none", "ready": False, "dependencies": {},
                        "owned_stop": False, "eligible_actions": [],
                        "reason": "Previously stopped container is missing; ownership cannot transfer"}
                continue
            actions, reason = self._eligible(service, item, entry, inventory)
            measured = stats.get(item["id"], {})
            services[service] = {k: item[k] for k in ("id", "state", "running", "paused", "health", "ready", "dependencies")}
            services[service].update({**measured, "owned_stop": self._owns_stop(entry, item),
                                     "eligible_actions": actions, "reason": reason,
                                     "last_action_at": entry["last_action_at"] if entry else None,
                                     "last_running_memory_bytes": entry["memory_usage_bytes"] if entry else None,
                                     "phase": entry.get("phase") if entry else None})
        return {"status": "observed", "timestamp": self.clock(), "services": services,
                "actions_enabled": self.config.enabled, "max_actions_per_decision": 1}, state, inventory

    def observe(self):
        try:
            value, _, inventory = self._observation()
            self._last_observation = value
            self._last_inventory = inventory
            return value
        except Exception as exc:
            self._last_observation = None
            self._last_inventory = None
            return {"status": "failed", "timestamp": self.clock(), "services": {}, "error": self._error(exc)}

    @staticmethod
    def _error(exc):
        return str(exc) if isinstance(exc, LifecycleError) else f"Invalid lifecycle state ({type(exc).__name__})"

    def _sample(self, sample, *, starting=False, memory_bytes=0):
        now = self.clock()
        stamp = sample.get("timestamp")
        if not _finite(stamp) or not 0 <= now - stamp <= self.config.max_sample_age_seconds:
            raise LifecycleDeferred("Lifecycle telemetry is stale")
        if sample.get("errors") or sample.get("instrumentation_ready") is not True:
            raise LifecycleDeferred("Lifecycle telemetry is incomplete")
        sources = sample.get("source_timestamps", {})
        timestamps = [sources.get("docker"), sources.get("kafka"), *sources.get("workers", {}).values()]
        if sources and any(not _finite(t) or not 0 <= now - t <= self.config.max_sample_age_seconds for t in timestamps):
            raise LifecycleDeferred("Lifecycle telemetry sources are stale")
        host, vm = sample.get("host", {}), sample.get("vm_memory", {})
        for value in (host.get("memory_available_percent"), sample.get("vm_memory_available_percent")):
            if not _finite(value) or not 0 <= value <= 100:
                raise LifecycleDeferred("Host and VM memory observations are required")
            if starting and value <= self.config.min_memory_available_percent:
                raise LifecycleDeferred("Memory pressure prevents starting a container")
        if starting:
            reserve = max(256 * MIB, math.ceil(memory_bytes * 1.5) + 128 * MIB)
            for total, available in ((host.get("memory_bytes"), host.get("memory_available_bytes")),
                                     (vm.get("total_bytes"), vm.get("available_bytes"))):
                if not _finite(total) or not _finite(available) or not 0 <= available <= total or total <= 0:
                    raise LifecycleDeferred("Starting requires host and VM available-memory byte measurements")
                if available - reserve <= total * self.config.min_memory_available_percent / 100:
                    raise LifecycleDeferred("Insufficient measured memory headroom to start the service")

    def _drain_trainer(self, item):
        if not item["trainer_controlled"] or not item["hostname"]:
            raise LifecycleDeferred("Trainer cannot acknowledge safe shutdown")
        try:
            policy = json.loads(self.config.policy_path.read_text(encoding="utf-8"))
            if (policy.get("training_allowed") is not False or not _finite(policy.get("expires_at"))
                    or policy["expires_at"] <= self.clock()
                    or policy.get("source_topic") != item["source_topic"]):
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise LifecycleDeferred("Trainer must receive a valid training-disabled lease before shutdown") from None
        request_path = self.config.policy_path.with_name("trainer-drain.json")
        ack_path = self.config.policy_path.with_name("trainer-drain-ack.json")
        request_id = uuid4().hex
        started = self.clock()
        expiry = started + self.config.drain_timeout_seconds + self.config.command_timeout_seconds + 5
        request = {"request_id": request_id, "expires_at": expiry}
        _atomic_json(request_path, request)
        try:
            deadline = started + self.config.drain_timeout_seconds
            while True:
                try:
                    ack = json.loads(ack_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    ack = {}
                stamp = ack.get("timestamp")
                now = self.clock()
                if (ack.get("request_id") == request_id and ack.get("hostname") == item["hostname"]
                        and _finite(stamp) and started <= stamp <= now
                        and now - stamp <= 2 and expiry - now > self.config.command_timeout_seconds + 1):
                    return request_path, request_id, expiry, started
                if now >= deadline:
                    raise LifecycleDeferred("Trainer is busy or has not acknowledged safe shutdown")
                self.sleep(min(.25, max(0, deadline - now)))
        except BaseException:
            self._clear_drain(request_path, request_id)
            raise

    @staticmethod
    def _clear_drain(path, request_id):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("request_id") == request_id:
                path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass

    def _validate_drain(self, drain, item):
        path, request_id, expiry, started = drain
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
            ack = json.loads(path.with_name("trainer-drain-ack.json").read_text(encoding="utf-8"))
            now = self.clock()
            stamp = ack.get("timestamp")
            if (request != {"request_id": request_id, "expires_at": expiry}
                    or ack.get("request_id") != request_id or ack.get("hostname") != item["hostname"]
                    or not _finite(stamp) or not started <= stamp <= now or now - stamp > 2
                    or expiry - now <= self.config.command_timeout_seconds + 1):
                raise ValueError
        except (OSError, ValueError, TypeError, AttributeError):
            raise LifecycleDeferred("Trainer drain acknowledgement expired or changed before stop") from None

    def _receipt(self, status, decision_id, **fields):
        return {"status": status, "action": "container_lifecycle", "timestamp": self.clock(),
                "decision_id": decision_id, **fields}

    def _audit(self, receipt):
        path = self.config.state_path.with_name("lifecycle_actions.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(receipt, allow_nan=False, ensure_ascii=True) + "\n")
            output.flush()
            os.fsync(output.fileno())

    def apply(self, actions, *, dry_run=True, sample, decision_id):
        receipt = None
        try:
            if not self.config.enabled:
                raise LifecycleError("Container lifecycle changes are disabled")
            if (not isinstance(actions, Mapping) or len(actions) > 1
                    or any(k not in self.config.allowed_services or v not in {"stop", "start"} for k, v in actions.items())):
                raise LifecycleError("Only one allowlisted stop/start action is permitted per decision")
            if not actions:
                return self._receipt("no_change", decision_id)
            if not isinstance(decision_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", decision_id):
                raise LifecycleError("A bounded decision identifier is required")
            # Dry run neither takes a filesystem lock nor writes a journal/audit.
            if dry_run:
                receipt = self._apply(actions, sample, decision_id, dry_run=True)
            else:
                with self._lock():
                    receipt = self._apply(actions, sample, decision_id, dry_run=False)
        except Exception as exc:
            receipt = self._receipt("deferred" if isinstance(exc, LifecycleDeferred) else "failed", decision_id,
                                    error=self._error(exc))
        if not dry_run:
            try:
                self._audit(receipt)
            except OSError:
                receipt = {**receipt, "status": "failed", "audit_error": "Lifecycle audit could not be persisted"}
        return receipt

    def _apply(self, actions, sample, decision_id, *, dry_run):
        service, action = next(iter(actions.items()))
        observed = self._last_observation
        if (not observed or observed.get("status") != "observed"
                or not 0 <= self.clock() - observed["timestamp"] <= self.config.max_sample_age_seconds
                or service not in self._last_inventory):
            raise LifecycleDeferred("A fresh model-visible container observation is required")
        model_target = self._last_inventory[service]
        observation, state, inventory = self._observation()
        if service not in observation["services"]:
            raise LifecycleError("Requested optional service is not present")
        view, before = observation["services"][service], inventory[service]
        self._same(model_target, before)
        if any(model_target[k] != before[k] for k in ("running", "paused", "started_at", "finished_at")):
            raise LifecycleDeferred("Container lifecycle changed after the model observation")
        if action not in view["eligible_actions"]:
            raise LifecycleDeferred(view["reason"])
        entry = state["services"].get(service)
        usage = view.get("memory_usage_bytes") if action == "stop" else entry["memory_usage_bytes"]
        if not _finite(usage) or usage < 0:
            raise LifecycleDeferred("Measured running-service memory footprint is required")
        self._sample(sample, starting=action == "start", memory_bytes=usage)
        plan = {"service": service, "operation": action, "container_id": before["id"],
                "before": {k: view[k] for k in ("state", "running", "health", "ready")},
                "last_running_memory_bytes": usage}
        if dry_run:
            return self._receipt("dry_run", decision_id, **plan,
                                 requires_trainer_drain=service == "trainer" and action == "stop")
        drain = None
        try:
            if service == "trainer" and action == "stop":
                drain = self._drain_trainer(before)
            # Re-read after a potentially slow drain; validate the exact target
            # and dependency graph again immediately before requesting mutation.
            self._sample(sample, starting=action == "start", memory_bytes=usage)
            latest = self._discover()
            if service not in latest:
                raise LifecycleError("Container disappeared before action")
            current = latest[service]
            self._same(before, current)
            if any(before[k] != current[k] for k in ("running", "paused", "restarting", "state", "started_at", "finished_at")):
                raise LifecycleDeferred("Container state changed before action")
            problem = self._dependency_problem(service, action, latest)
            if problem:
                raise LifecycleDeferred(problem)
            prior_entry = state["services"].get(service)
            entry = {k: before[k] for k in ("id", "name", "hostname", "identity")}
            # Intent alone does not prove a stop happened. A process can die
            # after this write and before the Docker command; recovery must not
            # adopt an unrelated later stop by an operator.
            entry.update(owned_stop=False, phase=action + "_requested", last_action_at=self.clock(),
                         memory_usage_bytes=usage, decision_id=decision_id)
            state["services"][service] = entry
            _atomic_json(self.config.state_path, state)
            try:
                self._sample(sample, starting=action == "start", memory_bytes=usage)
                if drain:
                    self._validate_drain(drain, current)
            except LifecycleError:
                # No Docker command has been issued. Preserve an earlier
                # confirmed stop so a deferred restart remains recoverable.
                if prior_entry is None:
                    state["services"].pop(service)
                else:
                    state["services"][service] = prior_entry
                _atomic_json(self.config.state_path, state)
                raise
            command_error = None
            try:
                if action == "stop":
                    grace = max(1, min(10, int(self.config.command_timeout_seconds) - 1))
                    self._command("stop", "--time", str(grace), before["id"])
                else:
                    self._command("start", before["id"])
            except LifecycleError as exc:
                command_error = str(exc)
            try:
                after = self._inspect(before["id"])
                self._same(before, after)
            except LifecycleError:
                # Retain uncertain intent, without granting automatic start rights.
                raise LifecycleError("Container action outcome is uncertain; retained recovery journal") from None
            stopped = not after["running"] and after["state"] == "exited" and not after["paused"]
            confirmed = stopped if action == "stop" else after["running"] and not after["paused"] and not after["restarting"]
            entry["phase"] = ("stopped" if action == "stop" else "running" if after["ready"] else "starting") if confirmed else action + "_pending"
            entry["owned_stop"] = action == "stop" and confirmed
            if entry["owned_stop"]:
                entry["stopped_started_at"] = after["started_at"]
                entry["stopped_finished_at"] = after["finished_at"]
            elif action == "start" and not confirmed and self._owns_stop(prior_entry, after):
                # Failed start with no intervening container lifetime remains
                # our confirmed stop, and can be retried after cooldown.
                entry.update(prior_entry)
            entry["last_action_at"] = self.clock()
            _atomic_json(self.config.state_path, state)
            result = self._receipt("applied" if confirmed else "failed", decision_id, **plan,
                                   after={k: after[k] for k in ("state", "running", "health", "ready")},
                                   phase=entry["phase"], ready=after["ready"],
                                   outcome_verified=confirmed, owned_stop=entry["owned_stop"])
            if command_error:
                result["command_error"] = command_error
            if not confirmed:
                result["error"] = "Docker did not confirm the requested container state"
            return result
        finally:
            if drain:
                self._clear_drain(*drain[:2])
