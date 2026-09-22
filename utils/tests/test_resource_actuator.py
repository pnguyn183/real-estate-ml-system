import subprocess

import pytest

from agents.resource_actuator import DockerResourceActuator, ResourceScalingConfig


def test_resource_actuator_scales_up_and_restores_with_bounded_commands():
    commands = []

    def runner(args, **kwargs):
        commands.append(args)

    actuator = DockerResourceActuator(
        ResourceScalingConfig(enabled=True, containers=("processor-a",), initial_cpus=1,
                              min_cpus=.5, max_cpus=2, cpu_step=.5,
                              initial_memory_mb=1024, min_memory_mb=512,
                              max_memory_mb=2048, memory_step_mb=512), runner=runner
    )
    receipt = actuator.apply_for_decision(type("Decision", (), {"action": "decrease"})())
    assert receipt == {"status": "applied", "action": "scale_up", "cpus": 1.5, "memory_mb": 1536}
    assert commands[-1] == ["docker", "update", "--cpus", "1.5", "--memory", "1536m", "processor-a"]
    assert actuator.restore()["action"] == "restore"


def test_resource_actuator_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="invalid CPU resource bounds"):
        ResourceScalingConfig(initial_cpus=5, max_cpus=4)


def test_resource_actuator_reports_docker_failure_without_raising():
    def runner(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    actuator = DockerResourceActuator(ResourceScalingConfig(enabled=True), runner=runner)
    receipt = actuator.apply_for_decision(type("Decision", (), {"action": "decrease"})())
    assert receipt["status"] == "failed"
    assert receipt["action"] == "scale_up"