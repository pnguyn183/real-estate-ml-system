"""Bounded tools for an LLM to inspect and change Compose CPU/RAM allocations.

The model supplies numeric limits, never Docker commands or file paths. Changes
are dry runs by default. Live changes preserve swap allowance, verify immutable
container identities, persist a comment-preserving Compose edit, and attempt
rollback on failure. This module never recreates containers or starts services.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Callable, Literal, TypedDict

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


ServiceName = Literal["processor", "processor-2", "processor-3", "trainer"]
ALLOWED_SERVICES = ("processor", "processor-2", "processor-3", "trainer")
MIB = 1024 ** 2
NANO = 1_000_000_000


class ResourceLimit(TypedDict):
    cpus: float
    memory_mb: int


class ResourceError(RuntimeError):
    """A sanitized, actionable error safe to put in an agent receipt."""


def _finite(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


@dataclass(frozen=True)
class ComposeResourceConfig:
    compose_path: str | Path
    project_name: str
    enabled: bool = False
    allowed_services: tuple[ServiceName, ...] = ALLOWED_SERVICES
    min_cpus: float = .25
    max_cpus: float = 4.0
    min_memory_mb: int = 256
    max_memory_mb: int = 4096
    max_total_cpu_fraction: float = .75
    max_total_memory_fraction: float = .70
    memory_headroom_fraction: float = .25
    min_memory_headroom_mb: int = 128
    command_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        if (not isinstance(self.project_name, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.project_name)):
            raise ValueError("project_name must be an explicit Compose project name")
        if (not isinstance(self.allowed_services, (list, tuple)) or not self.allowed_services
                or any(service not in ALLOWED_SERVICES for service in self.allowed_services)
                or len(set(self.allowed_services)) != len(self.allowed_services)):
            raise ValueError("allowed_services must be unique approved services")
        object.__setattr__(self, "allowed_services", tuple(self.allowed_services))
        object.__setattr__(self, "compose_path", Path(self.compose_path).resolve())
        for name in ("min_cpus", "max_cpus", "min_memory_mb", "max_memory_mb",
                     "max_total_cpu_fraction", "max_total_memory_fraction",
                     "memory_headroom_fraction", "min_memory_headroom_mb", "command_timeout_seconds"):
            value = getattr(self, name)
            if not _finite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        if not .01 <= self.min_cpus <= self.max_cpus <= 64:
            raise ValueError("invalid CPU bounds")
        if (not isinstance(self.min_memory_mb, int) or not isinstance(self.max_memory_mb, int)
                or not 6 <= self.min_memory_mb <= self.max_memory_mb):
            raise ValueError("memory bounds must be integer MiB, at least 6 MiB")
        if not 0 < self.max_total_cpu_fraction <= 1 or not 0 < self.max_total_memory_fraction <= 1:
            raise ValueError("capacity fractions must be in (0, 1]")
        if self.memory_headroom_fraction > 1 or self.command_timeout_seconds > 120:
            raise ValueError("invalid headroom or timeout bound")


class ComposeResourceActuator:
    def __init__(self, config: ComposeResourceConfig, runner: Callable | None = None):
        self.config = config
        self._runner = runner or subprocess.run

    def _command(self, args: Sequence[str]) -> str:
        try:
            result = self._runner(list(args), check=True, capture_output=True, text=True,
                                  timeout=self.config.command_timeout_seconds)
            return result.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            # In particular, never surface Compose's rendered environment or
            # subprocess stderr (which may contain credentials) to an LLM/log.
            raise ResourceError(f"Docker {args[1]} command failed ({type(exc).__name__})") from None

    def _json_command(self, args: Sequence[str]):
        try:
            return json.loads(self._command(args))
        except (ValueError, TypeError):
            raise ResourceError("Docker returned invalid JSON") from None

    def _engine(self) -> dict:
        data = self._json_command(("docker", "info", "--format", "{{json .}}"))
        cpus, memory = data.get("NCPU"), data.get("MemTotal")
        if not _finite(cpus) or cpus <= 0 or not _finite(memory) or memory <= 0:
            raise ResourceError("Docker did not report a positive CPU/RAM capacity")
        return {"cpus": cpus, "memory_bytes": memory, "os_type": data.get("OSType"),
                "memory_limit_supported": data.get("MemoryLimit") is True,
                "swap_limit_supported": data.get("SwapLimit") is True,
                "cpu_quota_supported": data.get("CpuCfsQuota") is True}

    def _inspect(self, identifier: str, service: str) -> dict:
        rows = self._json_command(("docker", "inspect", identifier))
        if not isinstance(rows, list) or len(rows) != 1:
            raise ResourceError("Docker inspect must return exactly one container")
        value = rows[0]
        labels = value.get("Config", {}).get("Labels") or {}
        state = value.get("State", {})
        if (value.get("Id") != identifier
                or labels.get("com.docker.compose.project") != self.config.project_name
                or labels.get("com.docker.compose.service") != service
                or labels.get("com.docker.compose.oneoff", "false").lower() != "false"
                or state.get("Running") is not True or state.get("Paused") is True):
            raise ResourceError("Container identity, Compose labels or running state changed")
        host = value.get("HostConfig", {})
        keys = ("NanoCpus", "CpuPeriod", "CpuQuota", "Memory", "MemorySwap", "MemoryReservation")
        for key in keys:
            item = host.get(key, 0)
            if not isinstance(item, int) or isinstance(item, bool):
                raise ResourceError("Docker returned invalid resource allocations")
        return {"id": identifier, "service": service,
                **{key: host.get(key, 0) for key in keys}}

    def _memory_usage(self, identifier: str) -> int:
        # Docker CLI stats subtracts cache and rounds the result. Read the raw
        # cgroup counter instead, to avoid shrinking RAM below real usage.
        for path in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
            try:
                value = self._command(("docker", "exec", identifier, "cat", path)).strip()
            except ResourceError:
                continue
            if re.fullmatch(r"[0-9]+", value):
                return int(value)
        raise ResourceError("Raw container memory usage is unavailable; RAM changes are blocked")

    def _snapshots(self) -> dict[str, dict]:
        result = {}
        for service in self.config.allowed_services:
            ids = self._command(("docker", "ps", "--no-trunc", "--quiet", "--filter",
                                 f"label=com.docker.compose.project={self.config.project_name}",
                                 "--filter", f"label=com.docker.compose.service={service}",
                                 "--filter", "label=com.docker.compose.oneoff=False")).split()
            if not ids:
                continue
            if len(ids) != 1 or not re.fullmatch(r"[a-f0-9]{64}", ids[0]):
                raise ResourceError("Each controlled service must have exactly one running container")
            result[service] = self._inspect(ids[0], service)
        return result

    def _budget(self, engine: dict) -> dict:
        return {"cpus": engine["cpus"] * self.config.max_total_cpu_fraction,
                "memory_bytes": int(engine["memory_bytes"] * self.config.max_total_memory_fraction),
                "scope": "all running allowlisted services; remaining capacity reserved for other workloads"}

    def observe(self) -> dict:
        timestamp = time.time()
        try:
            engine, snapshots = self._engine(), self._snapshots()
            services = {}
            for service, snapshot in snapshots.items():
                usage = self._memory_usage(snapshot["id"])
                services[service] = {"id": snapshot["id"], "cpus": snapshot["NanoCpus"] / NANO,
                                     "memory_mb": snapshot["Memory"] / MIB,
                                     "memory_usage_bytes": usage,
                                     "memory_swap_bytes": snapshot["MemorySwap"],
                                     "memory_headroom_bytes": snapshot["Memory"] - usage,
                                     "cpu_unlimited": snapshot["NanoCpus"] == 0,
                                     "memory_unlimited": snapshot["Memory"] == 0}
            return {"status": "observed", "timestamp": timestamp, "observed_at": timestamp,
                    "engine": engine, "budgets": self._budget(engine), "services": services}
        except (ResourceError, KeyError, TypeError, AttributeError) as exc:
            return {"status": "failed", "timestamp": timestamp, "error": self._error(exc)}

    @staticmethod
    def _error(exc: Exception) -> str:
        return str(exc) if isinstance(exc, ResourceError) else f"Invalid resource state ({type(exc).__name__})"

    @contextmanager
    def _lock(self):
        lock_path = self.config.compose_path.with_name(self.config.compose_path.name + ".resource-agent.lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ResourceError("Another resource edit is active; existing lock must not be overwritten") from None
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(descriptor)
            lock_path.unlink()

    def _limits(self, limits: Mapping[str, ResourceLimit]) -> dict:
        if not isinstance(limits, Mapping) or not limits:
            raise ResourceError("limits must be a nonempty service mapping")
        result = {}
        for service, value in limits.items():
            if service not in self.config.allowed_services:
                raise ResourceError("Requested service is not on the resource allowlist")
            if not isinstance(value, Mapping) or set(value) != {"cpus", "memory_mb"}:
                raise ResourceError("Each service must specify only cpus and memory_mb")
            cpus, memory = value["cpus"], value["memory_mb"]
            if (not _finite(cpus) or not self.config.min_cpus <= cpus <= self.config.max_cpus
                    or not isinstance(memory, int) or isinstance(memory, bool)
                    or not self.config.min_memory_mb <= memory <= self.config.max_memory_mb):
                raise ResourceError("Requested CPU/RAM exceeds configured bounds or is not finite")
            result[service] = {"NanoCpus": round(cpus * NANO), "Memory": memory * MIB}
        return result

    def _check_original(self, snapshot: dict) -> None:
        if snapshot["CpuPeriod"] or snapshot["CpuQuota"]:
            raise ResourceError("Legacy CPU quota settings are unsupported for exact live rollback")
        if snapshot["NanoCpus"] <= 0:
            raise ResourceError("Unlimited CPU cannot be restored by docker update; configure explicit quotas and recreate first")
        if snapshot["Memory"] <= 0 or snapshot["MemorySwap"] == 0:
            raise ResourceError("Explicit RAM and swap limits are required before live resource changes")
        if snapshot["MemorySwap"] < -1 or 0 < snapshot["MemorySwap"] < snapshot["Memory"]:
            raise ResourceError("Original memory/swap allocation is invalid")

    def _check_memory(self, identifier: str, target_memory: int) -> int:
        usage = self._memory_usage(identifier)
        margin = max(self.config.min_memory_headroom_mb * MIB,
                     math.ceil(usage * self.config.memory_headroom_fraction))
        if target_memory < usage + margin:
            raise ResourceError("Target RAM lacks required headroom above measured cgroup usage")
        return usage

    def _plan(self, requested: dict, engine: dict, snapshots: dict) -> dict:
        if engine["os_type"] != "linux" or not all(engine[key] for key in
                ("memory_limit_supported", "swap_limit_supported", "cpu_quota_supported")):
            raise ResourceError("Docker Linux CPU, memory and swap limit support is required")
        for snapshot in snapshots.values():
            self._check_original(snapshot)
        planned = {}
        for service, target in requested.items():
            if service not in snapshots:
                raise ResourceError("Requested service is not running; the agent does not start or recreate containers")
            original = snapshots[service]
            if target["Memory"] < original["MemoryReservation"]:
                raise ResourceError("Target memory would fall below the existing memory reservation")
            swap = original["MemorySwap"]
            planned[service] = {**original, **target, "MemorySwap":
                                -1 if swap == -1 else target["Memory"] + swap - original["Memory"]}
            if target["Memory"] != original["Memory"]:
                self._check_memory(original["id"], target["Memory"])
        final = {**snapshots, **planned}
        budget = self._budget(engine)
        if sum(s["NanoCpus"] for s in final.values()) / NANO > budget["cpus"]:
            raise ResourceError("Total CPU allocation of all controlled services exceeds the Docker capacity budget")
        if sum(s["Memory"] for s in final.values()) > budget["memory_bytes"]:
            raise ResourceError("Total RAM allocation of all controlled services exceeds the Docker capacity budget")
        return planned

    def _candidate(self, original: bytes, planned: dict) -> bytes:
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.width = 4096
        try:
            source = original.decode("utf-8-sig")
            document = yaml.load(source)
            # A second independent parse detects aliases that accidentally edit
            # another service, and restricts the semantic diff to approved keys.
            expected = json.loads(json.dumps(document))
            for service, target in planned.items():
                entry = document["services"][service]
                if "extends" in entry:
                    raise ResourceError("Extended Compose services need explicit local resource configuration")
                for candidate in (entry, expected["services"][service]):
                    limits = candidate.setdefault("deploy", {}).setdefault("resources", {}).setdefault("limits", {})
                    limits["cpus"] = f"{target['NanoCpus'] / NANO:.9f}".rstrip("0").rstrip(".")
                    limits["memory"] = str(target["Memory"])
                    candidate["memswap_limit"] = target["MemorySwap"]
            if json.loads(json.dumps(document)) != expected:
                raise ResourceError("Compose aliases would modify an unrequested resource or service")
            stream = StringIO()
            yaml.dump(document, stream)
            result = stream.getvalue()
            if b"\r\n" in original:
                result = result.replace("\n", "\r\n")
            return (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + result.encode("utf-8")
        except (YAMLError, ValueError, TypeError, KeyError, AttributeError):
            raise ResourceError("Compose YAML is invalid or lacks an editable local service definition") from None

    def _validate_candidate(self, candidate: bytes) -> None:
        path = self._temporary(candidate, suffix=".yaml")
        try:
            self._command(("docker", "compose", "--project-directory", str(self.config.compose_path.parent),
                           "--project-name", self.config.project_name, "--file", str(path), "config", "--quiet"))
        finally:
            path.unlink(missing_ok=True)

    def _temporary(self, contents: bytes, suffix: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=".resource-agent-", suffix=suffix,
                                           dir=self.config.compose_path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        return Path(name)

    def _compare(self, expected: bytes) -> None:
        if self.config.compose_path.read_bytes() != expected:
            raise ResourceError("Compose changed concurrently; refusing to overwrite the edited file")

    def _replace(self, expected: bytes, replacement: bytes) -> None:
        temporary = self._temporary(replacement, suffix=".tmp")
        try:
            self._compare(expected)
            os.replace(temporary, self.config.compose_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _update(self, target: dict) -> None:
        self._command(("docker", "update", "--cpus", f"{target['NanoCpus'] / NANO:.9f}",
                       "--memory", str(target["Memory"]), "--memory-swap", str(target["MemorySwap"]), target["id"]))

    def _verify(self, expected: dict) -> dict:
        actual = self._inspect(expected["id"], expected["service"])
        if actual != expected:
            raise ResourceError("Docker did not retain the requested allocation; verification failed")
        return actual

    @staticmethod
    def _steps(snapshots: dict, planned: dict) -> list[dict]:
        # Release resources before granting them elsewhere. A final allocation
        # can fit the total budget while an increase-first ordering temporarily
        # exceeds it. Mixed CPU/RAM changes therefore need two bounded updates.
        reductions, increases = [], []
        for service, target in planned.items():
            original = snapshots[service]
            memory = min(original["Memory"], target["Memory"])
            intermediate = {**original, "NanoCpus": min(original["NanoCpus"], target["NanoCpus"]),
                            "Memory": memory, "MemorySwap": -1 if original["MemorySwap"] == -1
                            else memory + original["MemorySwap"] - original["Memory"]}
            if intermediate != original:
                reductions.append(intermediate)
            if target != intermediate:
                increases.append(target)
        return reductions + increases

    def _rollback(self, snapshots: dict, changed: list[str], original: bytes,
                  candidate: bytes, file_written: bool, steps: list[tuple[dict, dict]]) -> dict:
        restored, failures = [], {}
        # Undo grants before restoring releases, reversing the staged updates.
        for before, attempted in reversed(steps):
            service = before["service"]
            if service in failures:
                continue
            try:
                actual = self._inspect(before["id"], service)
                if actual != before:
                    # Do not undo an operator's concurrent allocation. A failed
                    # update may have applied, so only the exact allocation we
                    # attempted is eligible for an automatic rollback.
                    if actual != attempted:
                        raise ResourceError("Allocation changed concurrently; refusing to overwrite it during rollback")
                    if before["Memory"] < actual["Memory"]:
                        self._check_memory(before["id"], before["Memory"])
                    self._update(before)
                    self._verify(before)
            except (ResourceError, OSError, KeyError, TypeError, AttributeError) as exc:
                failures[service] = self._error(exc)
        for service in changed:
            if service not in failures:
                try:
                    self._verify(snapshots[service])
                    restored.append(service)
                except (ResourceError, OSError, KeyError, TypeError, AttributeError) as exc:
                    failures[service] = self._error(exc)
        file_status = "unchanged"
        if file_written:
            try:
                self._replace(candidate, original)
                file_status = "restored"
            except (ResourceError, OSError) as exc:
                failures["compose"] = self._error(exc)
                file_status = "not_restored"
        return {"status": "failed" if failures else "restored", "restored_services": restored,
                "failures": failures, "compose": file_status,
                "outstanding_services": [s for s in changed if s in failures]}

    def apply(self, limits: Mapping[str, ResourceLimit], *, dry_run: bool = True) -> dict:
        receipt = {"status": "failed", "action": "compose_resource_limits", "timestamp": time.time(),
                   "dry_run": dry_run, "runtime_changed": False, "compose_changed": False}
        if not isinstance(dry_run, bool):
            return {**receipt, "error": "dry_run must be a boolean"}
        if not dry_run and not self.config.enabled:
            return {**receipt, "status": "disabled", "error": "Live resource changes are disabled"}
        snapshots, changed, original, candidate, file_written = {}, [], b"", b"", False
        steps = []
        try:
            requested = self._limits(limits)
            with self._lock():
                try:
                    original = self.config.compose_path.read_bytes()
                    engine, snapshots = self._engine(), self._snapshots()
                    planned = self._plan(requested, engine, snapshots)
                    receipt.update(engine=engine, budgets=self._budget(engine), planned_allocations=planned)
                    candidate = self._candidate(original, planned)
                    self._validate_candidate(candidate)
                    self._compare(original)
                    if dry_run:
                        return {**receipt, "status": "dry_run"}
                    # Re-read every allocation to catch changes during planning,
                    # including untouched services included in the shared budget.
                    if self._snapshots() != snapshots or self._engine() != engine:
                        raise ResourceError("Docker allocation or capacity changed during planning")
                    backup = self._temporary(original, suffix=".bak")
                    receipt["backup_path"] = str(backup)
                    current = dict(snapshots)
                    for target in self._steps(snapshots, planned):
                        service = target["service"]
                        self._verify(current[service])
                        if target["Memory"] != current[service]["Memory"]:
                            self._check_memory(target["id"], target["Memory"])
                        if service not in changed:
                            changed.append(service)
                        # A timeout may mean Docker already applied this step.
                        steps.append((current[service], target))
                        self._update(target)
                        self._verify(target)
                        current[service] = target
                    self._replace(original, candidate)
                    file_written = True
                    for target in planned.values():
                        self._verify(target)
                    # Untouched services count towards the same total budget.
                    # Detect replacements and allocation changes during apply,
                    # not just during the initial Compose validation.
                    if self._snapshots() != {**snapshots, **planned} or self._engine() != engine:
                        raise ResourceError("Docker allocation or capacity changed during apply")
                    self._compare(candidate)
                    return {**receipt, "status": "applied", "runtime_changed": bool(changed),
                            "compose_changed": candidate != original, "verified_allocations": planned}
                except (ResourceError, OSError, KeyError, TypeError, AttributeError) as exc:
                    receipt["error"] = self._error(exc)
                    if changed or file_written:
                        receipt["rollback"] = self._rollback(snapshots, changed, original, candidate,
                                                             file_written, steps)
                        receipt["runtime_changed"] = bool(receipt["rollback"]["outstanding_services"])
                        receipt["compose_changed"] = receipt["rollback"]["compose"] == "not_restored"
                    return receipt
        except (ResourceError, OSError, KeyError, TypeError, AttributeError) as exc:
            return {**receipt, "error": self._error(exc)}
