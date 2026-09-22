"""Bounded Docker resource actuator for the isolated traffic experiment."""
from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Callable, Sequence


@dataclass(frozen=True)
class ResourceScalingConfig:
    enabled: bool = False
    containers: tuple[str, ...] = (
        "real_estate_processor_1",
        "real_estate_processor_2",
        "real_estate_processor_3",
    )
    initial_cpus: float = 1.0
    min_cpus: float = 0.5
    max_cpus: float = 4.0
    cpu_step: float = 0.5
    initial_memory_mb: int = 1024
    min_memory_mb: int = 512
    max_memory_mb: int = 4096
    memory_step_mb: int = 512

    def __post_init__(self) -> None:
        if not self.containers:
            raise ValueError("at least one actuator container is required")
        if not self.min_cpus <= self.initial_cpus <= self.max_cpus or self.cpu_step <= 0:
            raise ValueError("invalid CPU resource bounds")
        if not self.min_memory_mb <= self.initial_memory_mb <= self.max_memory_mb or self.memory_step_mb <= 0:
            raise ValueError("invalid memory resource bounds")
        if any(not name or any(char.isspace() for char in name) for name in self.containers):
            raise ValueError("container names must be non-empty and contain no whitespace")

    @classmethod
    def from_mapping(cls, value: dict | None) -> "ResourceScalingConfig":
        value = value or {}
        return cls(
            enabled=bool(value.get("enabled", False)),
            containers=tuple(value.get("containers", cls.containers)),
            initial_cpus=float(value.get("initial_cpus", 1.0)),
            min_cpus=float(value.get("min_cpus", .5)),
            max_cpus=float(value.get("max_cpus", 4.0)),
            cpu_step=float(value.get("cpu_step", .5)),
            initial_memory_mb=int(value.get("initial_memory_mb", 1024)),
            min_memory_mb=int(value.get("min_memory_mb", 512)),
            max_memory_mb=int(value.get("max_memory_mb", 4096)),
            memory_step_mb=int(value.get("memory_step_mb", 512)),
        )


class DockerResourceActuator:
    """Apply bounded CPU/memory changes and restore the initial allocation."""

    def __init__(self, config: ResourceScalingConfig, runner: Callable[..., object] | None = None):
        self.config = config
        self._runner = runner or subprocess.run
        self.current_cpus = config.initial_cpus
        self.current_memory_mb = config.initial_memory_mb

    def _command(self, args: Sequence[str]) -> None:
        self._runner(list(args), check=True, capture_output=True, text=True)

    def _update(self, cpus: float, memory_mb: int, action: str) -> dict:
        if not self.config.enabled:
            return {"status": "disabled", "action": "none", "cpus": self.current_cpus,
                    "memory_mb": self.current_memory_mb}
        try:
            for container in self.config.containers:
                self._command(("docker", "update", "--cpus", str(cpus), "--memory", f"{memory_mb}m", container))
        except (OSError, subprocess.CalledProcessError) as exc:
            return {"status": "failed", "action": action, "cpus": cpus, "memory_mb": memory_mb,
                    "error": type(exc).__name__}
        self.current_cpus, self.current_memory_mb = cpus, memory_mb
        return {"status": "applied", "action": action, "cpus": cpus, "memory_mb": memory_mb}

    def apply_for_decision(self, decision) -> dict:
        """Scale up on congestion and down after a safe controller increase."""
        if decision.action == "decrease":
            cpus = min(self.config.max_cpus, self.current_cpus + self.config.cpu_step)
            memory = min(self.config.max_memory_mb, self.current_memory_mb + self.config.memory_step_mb)
            return self._update(cpus, memory, "scale_up")
        if decision.action == "increase":
            cpus = max(self.config.min_cpus, self.current_cpus - self.config.cpu_step)
            memory = max(self.config.min_memory_mb, self.current_memory_mb - self.config.memory_step_mb)
            return self._update(cpus, memory, "scale_down")
        return {"status": "no_change", "action": "none", "cpus": self.current_cpus,
                "memory_mb": self.current_memory_mb}

    def restore(self) -> dict:
        return self._update(self.config.initial_cpus, self.config.initial_memory_mb, "restore")
