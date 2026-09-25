"""Optional, bounded vertical CPU scaling with verified snapshot and rollback.

Call prepare() in BOTH policies before load. Memory configuration fields remain
for compatibility but are never actuated: lowering a running worker's memory
limit can kill it. This actuator scales CPU quotas, not worker replicas.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import re
import subprocess
import time
from typing import Callable, Sequence


@dataclass(frozen=True)
class ResourceScalingConfig:
    enabled: bool = False
    containers: tuple[str, ...] = (
        "real_estate_processor_1", "real_estate_processor_2", "real_estate_processor_3",
    )
    initial_cpus: float = 1.0
    min_cpus: float = .5
    max_cpus: float = 4.0
    cpu_step: float = .5
    cooldown_seconds: float = 30.0
    max_total_cpu_fraction: float = .8
    telemetry_max_age_seconds: float = 15.0
    command_timeout_seconds: float = 15.0
    # Compatibility fields, not applied to running containers.
    initial_memory_mb: int = 1024
    min_memory_mb: int = 512
    max_memory_mb: int = 4096
    memory_step_mb: int = 512

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        for name, value in asdict(self).items():
            if name in {"enabled", "containers"}:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        if not .01 <= self.min_cpus <= self.initial_cpus <= self.max_cpus <= 64:
            raise ValueError("invalid CPU resource bounds")
        if not self.min_memory_mb <= self.initial_memory_mb <= self.max_memory_mb:
            raise ValueError("invalid memory resource bounds")
        if not 0 < self.max_total_cpu_fraction <= 1:
            raise ValueError("max_total_cpu_fraction must be in (0, 1]")
        if (not isinstance(self.containers, (tuple, list)) or not 1 <= len(self.containers) <= 16
                or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
                       for name in self.containers)
                or len(set(self.containers)) != len(self.containers)):
            raise ValueError("containers must contain 1..16 unique Docker container names")

    @classmethod
    def from_mapping(cls, value: dict | None) -> "ResourceScalingConfig":
        value = dict(value or {})
        if "containers" in value:
            if not isinstance(value["containers"], (tuple, list)):
                raise ValueError("containers must be a list of names")
            value["containers"] = tuple(value["containers"])
        # Never turn string "false" into True or ignore misspelled limits.
        return cls(**value)


class DockerResourceActuator:
    """Apply CPU limits only after snapshot; restore inspected originals.

    Abort the experiment on any failed receipt. Rollback is attempted on partial
    failure and can be retried through restore(). All mutations target immutable
    container IDs, never replacements which happen to have the same name.
    """

    def __init__(self, config: ResourceScalingConfig, runner: Callable[..., object] | None = None):
        self.config = config
        self._runner = runner or subprocess.run
        self.current_cpus = config.initial_cpus
        self.current_memory_mb = config.initial_memory_mb
        self._original: dict[str, dict] = {}
        self._changed: set[str] = set()
        self._prepared = False
        self._last_scale: float | None = None
        self._effective_max = config.max_cpus

    def _command(self, args: Sequence[str]) -> str:
        result = self._runner(list(args), check=True, capture_output=True, text=True,
                              timeout=self.config.command_timeout_seconds)
        return result.stdout

    def _inspect(self, identifier: str) -> dict:
        value = json.loads(self._command(("docker", "inspect", identifier)))[0]
        host = value["HostConfig"]
        return {"id": value["Id"], "running": bool(value["State"]["Running"]),
                **{key: host.get(key, 0) for key in ("NanoCpus", "CpuPeriod", "CpuQuota", "Memory", "MemorySwap")}}

    def _receipt(self, status: str, action: str, **extra) -> dict:
        return {"status": status, "action": action, "timestamp": time.time(),
                "cpus": self.current_cpus, "memory_changed": False, **extra}

    def prepare(self) -> dict:
        if not self.config.enabled:
            return self._receipt("disabled", "none")
        if self._prepared:
            return self._receipt("no_change", "already_prepared")
        if self._changed:
            return self._receipt("failed", "prepare", error="restore outstanding containers before prepare")
        try:
            engine_cpus = float(self._command(("docker", "info", "--format", "{{.NCPU}}")))
            if not math.isfinite(engine_cpus) or engine_cpus <= 0:
                raise ValueError("Docker did not report a positive CPU capacity")
            total_budget = engine_cpus * self.config.max_total_cpu_fraction
            self._effective_max = math.floor(min(self.config.max_cpus, total_budget / len(self.config.containers)) * 1e9) / 1e9
            if self.config.initial_cpus > self._effective_max:
                raise ValueError("initial CPU allocation exceeds the configured share of Docker capacity")
            snapshots = {name: self._inspect(name) for name in self.config.containers}
            for snapshot in snapshots.values():
                if not snapshot["running"]:
                    raise ValueError("all target containers must be running")
                # Conversion from legacy quota controls cannot be restored
                # exactly on all Engine versions. Reject before any mutation.
                if snapshot["CpuPeriod"] or snapshot["CpuQuota"]:
                    raise ValueError("legacy CpuPeriod/CpuQuota targets unsupported; no allocation changed")
                if not isinstance(snapshot["NanoCpus"], int) or snapshot["NanoCpus"] < 0:
                    raise ValueError("invalid inspected NanoCpus")
                if snapshot["NanoCpus"] == 0:
                    # Moby treats NanoCPUs=0 in an update as 'unchanged', so a
                    # live --cpus 0 cannot reliably restore unlimited capacity.
                    # Use an explicit CPU limit in a temporary Compose override
                    # before the experiment; recreate from base Compose after it.
                    raise ValueError("unlimited CPU originals cannot be restored by docker update; configure an explicit CPU limit before the experiment")
            self._original = snapshots
            receipt = self._update(self.config.initial_cpus, "prepare")
            if receipt["status"] == "applied":
                self._prepared = True
                receipt.update(original_allocations=snapshots, engine_cpus=engine_cpus,
                               effective_max_cpus=self._effective_max)
            return receipt
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError) as exc:
            return self._receipt("failed", "prepare", error=f"{type(exc).__name__}: {exc}", rollback=self.restore())

    def _update(self, cpus: float, action: str) -> dict:
        started = time.time()
        try:
            if not self._original:
                raise RuntimeError("prepare must snapshot allocations before changes")
            if not self.config.min_cpus <= cpus <= self._effective_max:
                raise ValueError("CPU allocation outside bounded budget")
            target_nanos = round(cpus * 1_000_000_000)
            verified = {}
            for name, original in self._original.items():
                # A timed-out command might still have applied its change.
                self._changed.add(name)
                self._command(("docker", "update", "--cpus", f"{cpus:.9f}", original["id"]))
                actual = self._inspect(original["id"])
                if (actual["id"] != original["id"] or actual["NanoCpus"] != target_nanos
                        or not actual["running"] or any(actual[key] != original[key] for key in
                                                      ("CpuPeriod", "CpuQuota", "Memory", "MemorySwap"))):
                    raise RuntimeError(f"Docker allocation verification failed: {name}")
                verified[name] = actual
            self.current_cpus = cpus
            return self._receipt("applied", action, applied_at=time.time(),
                                 actuation_latency_seconds=time.time() - started, verified_allocations=verified)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError, RuntimeError) as exc:
            rollback = self.restore()
            return self._receipt("failed", action, error=f"{type(exc).__name__}: {exc}", rollback=rollback)

    def apply_for_decision(self, decision) -> dict:
        if not self.config.enabled:
            return self._receipt("disabled", "none")
        if not self._prepared:
            return self._receipt("failed", "none", error="prepare() has not succeeded")
        # A missing metric can decrease the gate but must not allocate resources.
        # An additive admission increase is not evidence for CPU downscaling.
        if not decision.reason.startswith("congestion:"):
            return self._receipt("no_change", "none", reason="no_measured_congestion")
        observation = decision.observation or {}
        required = ("timestamp", "incoming_rate", "throughput", "cpu_percent", "ram_percent",
                    "kafka_lag", "latency_p95_seconds", "error_rate")
        if any(isinstance(observation.get(key), bool) or not isinstance(observation.get(key), (int, float))
               or not math.isfinite(observation[key]) or observation[key] < 0 for key in required):
            return self._receipt("no_change", "none", reason="telemetry_missing_or_invalid")
        age = decision.timestamp - observation["timestamp"]
        if age < 0 or age > self.config.telemetry_max_age_seconds:
            return self._receipt("no_change", "none", reason="telemetry_stale")
        if observation["cpu_percent"] > 100 or observation["ram_percent"] > 100 or observation["error_rate"] > 1:
            return self._receipt("no_change", "none", reason="telemetry_missing_or_invalid")
        if not any(name in decision.reason.split(":")[-1].split(",") for name in
                   ("cpu_percent", "kafka_lag", "latency_p95_seconds")):
            return self._receipt("no_change", "none", reason="no_cpu_or_backlog_pressure")
        if self._last_scale is not None and decision.timestamp - self._last_scale < self.config.cooldown_seconds:
            return self._receipt("no_change", "none", reason="resource_cooldown")
        cpus = min(self._effective_max, self.current_cpus + self.config.cpu_step)
        if cpus <= self.current_cpus:
            return self._receipt("no_change", "none", reason="maximum_cpu_budget")
        receipt = self._update(cpus, "scale_up")
        if receipt["status"] == "applied":
            self._last_scale = decision.timestamp
        return receipt

    def restore(self) -> dict:
        if not self.config.enabled:
            return self._receipt("disabled", "none")
        failures, restored = {}, {}
        for name in tuple(self._changed):
            original = self._original[name]
            try:
                self._command(("docker", "update", "--cpus", str(original["NanoCpus"] / 1_000_000_000), original["id"]))
                actual = self._inspect(original["id"])
                if any(actual[key] != original[key] for key in ("id", "NanoCpus", "CpuPeriod", "CpuQuota", "Memory", "MemorySwap")):
                    raise RuntimeError("restored allocations differ from original snapshot")
                restored[name] = actual
                self._changed.remove(name)
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError, RuntimeError) as exc:
                failures[name] = f"{type(exc).__name__}: {exc}"
        self._prepared = False
        self._last_scale = None
        return self._receipt("failed" if failures else "restored", "restore",
                             restored_allocations=restored, failures=failures,
                             outstanding_containers=sorted(self._changed))
