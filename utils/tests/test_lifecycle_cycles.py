import json
from types import SimpleNamespace

import pytest

from research.lifecycle_cycles import EVIDENCE_CLASSIFICATION, run_lifecycle_cycles


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeLifecycle:
    """Offline stateful actuator; no Docker daemon or subprocess is used."""

    def __init__(self, clock):
        self.clock = clock
        self.config = SimpleNamespace(allowed_services=("trainer",), cooldown_seconds=2)
        self.container_id = "a" * 64
        self.running = True
        self.owned_stop = False
        self.last_action = float("-inf")
        self.mutations = []
        self.start_attempts = 0
        self.fail_next_start = False
        self.include_drain = True

    def observe(self):
        eligible = []
        if self.clock() - self.last_action >= self.config.cooldown_seconds:
            eligible = ["stop" if self.running else "start"]
        return {"status": "observed", "services": {"trainer": {
            "id": self.container_id, "running": self.running,
            "ready": self.running, "owned_stop": self.owned_stop,
            "eligible_actions": eligible,
        }}}

    def apply(self, actions, *, dry_run, sample, decision_id):
        assert sample["timestamp"] == self.clock()
        operation = actions["trainer"]
        if dry_run:
            return {"status": "dry_run", "container_id": self.container_id,
                    "operation": operation, "decision_id": decision_id}
        if operation == "stop":
            assert self.running
            self.running, self.owned_stop = False, True
        else:
            assert not self.running and self.owned_stop
            self.start_attempts += 1
            if self.fail_next_start:
                self.fail_next_start = False
                return {"status": "failed", "container_id": self.container_id,
                        "operation": operation, "decision_id": decision_id,
                        "owned_stop": True}
            self.running, self.owned_stop = True, False
        self.last_action = self.clock()
        self.mutations.append(operation)
        receipt = {"status": "applied", "container_id": self.container_id,
                "operation": operation, "decision_id": decision_id,
                "owned_stop": self.owned_stop, "ready": self.running}
        if operation == "stop" and self.include_drain:
            receipt["drain"] = {"schema_version": 1,
                "kind": "trainer_drain_acknowledgement", "request_id": decision_id,
                "acknowledged_at": self.clock(), "validated_at": self.clock(),
                "expires_at": self.clock() + 30,
                "training_policy_decision_id": "training-off-1",
                "training_policy_expires_at": self.clock() + 60,
                "source_topic": "real_estate_stress_raw", "hostname_matched": True,
                "training_disabled_lease_verified": True}
        return receipt


def run(fake, clock, **kwargs):
    return run_lifecycle_cycles(fake, "trainer", lambda: {"timestamp": clock()},
        clock=clock, wall_clock=clock, sleep=clock.sleep, timeout_seconds=5,
        poll_seconds=1, **kwargs)


def test_three_cycle_harness_is_bounded_same_identity_and_not_live_evidence(tmp_path):
    clock = Clock()
    fake = FakeLifecycle(clock)
    output = tmp_path / "cycles.json"
    result = run(fake, clock, execute=True, cycles=3, output_path=output)
    assert result["status"] == "completed" and result["completed_cycles"] == 3
    assert fake.mutations == ["stop", "start"] * 3 and fake.running
    assert result["container_id"] == fake.container_id
    assert all(row["stop"]["container_id"] == row["start"]["container_id"] == fake.container_id
               for row in result["cycles"])
    assert len([row for row in result["cycles"] if row["stop"].get("drain")]) == 3
    assert result["evidence_classification"] == EVIDENCE_CLASSIFICATION
    assert not result["live_evidence"] and not result["llm_selected_actions"]
    assert not result["causal_ram_effect_verified"]
    assert json.loads(output.read_text()) == result


def test_default_dry_run_never_mutates_or_claims_completed_cycles():
    clock = Clock()
    fake = FakeLifecycle(clock)
    result = run(fake, clock)
    assert result["status"] == "dry_run" and result["completed_cycles"] == 0
    assert result["plan"]["status"] == "dry_run"
    assert not fake.mutations and fake.running


def test_failed_cycle_restores_controller_owned_stop_without_claiming_completion():
    clock = Clock()
    fake = FakeLifecycle(clock)
    fake.fail_next_start = True
    result = run(fake, clock, execute=True)
    assert result["status"] == "failed" and result["completed_cycles"] == 0
    assert result["restore"]["status"] == "applied"
    assert fake.mutations == ["stop", "start"] and fake.running and not fake.owned_stop


def test_trainer_cycle_without_durable_drain_proof_fails_and_restores():
    clock = Clock()
    fake = FakeLifecycle(clock)
    fake.include_drain = False
    result = run(fake, clock, execute=True)
    assert result["status"] == "failed" and result["completed_cycles"] == 0
    assert "drain proof" in result["error"]
    assert result["restore"]["status"] == "applied" and fake.running


@pytest.mark.parametrize("cycles", [0, 4, True])
def test_cycle_count_is_strictly_bounded(cycles):
    clock = Clock()
    with pytest.raises(ValueError, match="cycles"):
        run(FakeLifecycle(clock), clock, execute=True, cycles=cycles)

