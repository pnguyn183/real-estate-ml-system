"""Trainer drain acknowledgements prove an idle boundary, never training permission."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from agents.container_drain import wait_for_container_drain
from agents.runtime_policy import RuntimePolicy


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.delays = []
        self.on_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.on_sleep()
        self.now += seconds


def setup(tmp_path):
    clock = Clock()
    policy = RuntimePolicy(True, tmp_path / "policy.json", wall_clock=clock)
    return policy, clock, tmp_path / "trainer-drain.json", tmp_path / "trainer-drain-ack.json"


def request(path, **changes):
    value = {"request_id": "decision-123", "expires_at": 1003.0}
    value.update(changes)
    path.write_text(json.dumps(value), encoding="utf-8")


def wait(policy, clock, **kwargs):
    wait_for_container_drain(policy, clock=clock, monotonic=clock, sleep=clock.sleep,
                             hostname="trainer-container", **kwargs)


def test_live_drain_blocks_until_expiry_and_refreshes_exact_identity(tmp_path):
    policy, clock, req, ack = setup(tmp_path)
    request(req)
    acknowledgements = []
    clock.on_sleep = lambda: acknowledgements.append(json.loads(ack.read_text()))
    wait(policy, clock)
    assert clock.now == 1003.0
    assert [item["timestamp"] for item in acknowledgements] == [1000, 1001, 1002]
    assert all(item["request_id"] == "decision-123" and
               item["hostname"] == "trainer-container" for item in acknowledgements)
    assert not ack.exists()
    assert req.exists()  # The trainer never removes the controller's request.
    assert not policy.path.exists()  # Drain cannot grant training permission.


def test_removal_releases_boundary_and_cleans_own_ack(tmp_path):
    policy, clock, req, ack = setup(tmp_path)
    request(req, expires_at=1100)
    def remove():
        assert json.loads(ack.read_text())["request_id"] == "decision-123"
        req.unlink()
    clock.on_sleep = remove
    wait(policy, clock)
    assert clock.delays == [1.0] and not ack.exists()


def test_scheduled_sleep_detects_new_request_and_waits_beyond_sleep_deadline(tmp_path):
    policy, clock, req, ack = setup(tmp_path)
    def publish_during_sleep():
        if clock.now == 1000:
            request(req, expires_at=1004)
        elif clock.now >= 1001:
            assert json.loads(ack.read_text())["request_id"] == "decision-123"
    clock.on_sleep = publish_during_sleep
    wait(policy, clock, sleep_seconds=2)
    assert clock.now == 1004
    assert max(clock.delays) <= 1 and not ack.exists()


@pytest.mark.parametrize("changes", [
    {"request_id": ""}, {"request_id": "../other"}, {"request_id": 1},
    {"expires_at": 999}, {"expires_at": 1121}, {"expires_at": float("nan")},
    {"expires_at": float("inf")}, {"expires_at": True}, {"extra": "unexpected"},
])
def test_invalid_requests_never_acknowledged(tmp_path, changes):
    policy, clock, req, ack = setup(tmp_path)
    request(req, **changes)
    wait(policy, clock)
    assert not ack.exists() and not clock.delays


@pytest.mark.parametrize("raw", ["not json", "[]", "null", "x" * 4097])
def test_malformed_or_oversized_file_does_not_authorize_stop(tmp_path, raw):
    policy, clock, req, ack = setup(tmp_path)
    req.write_text(raw)
    wait(policy, clock)
    assert not ack.exists() and not clock.delays


def test_disabled_control_ignores_request_and_preserves_existing_ack(tmp_path):
    policy, clock, req, ack = setup(tmp_path)
    request(req)
    ack.write_text('{"request_id":"other-controller"}')
    wait(replace(policy, enabled=False), clock, sleep_seconds=4)
    assert clock.delays == [4]
    assert json.loads(ack.read_text())["request_id"] == "other-controller"


def test_cleanup_does_not_remove_an_ack_owned_by_other_request(tmp_path):
    policy, clock, req, ack = setup(tmp_path)
    request(req)
    def replace_receipt():
        ack.write_text('{"request_id":"another-request"}')
        req.unlink()
    clock.on_sleep = replace_receipt
    wait(policy, clock)
    assert json.loads(ack.read_text())["request_id"] == "another-request"


def test_training_permission_must_be_revoked_before_ack(tmp_path):
    policy, clock, req, ack = setup(tmp_path)
    request(req)
    state = {"allowed": True}
    policy = SimpleNamespace(path=policy.path, enabled=True,
                             read=lambda: SimpleNamespace(training_allowed=state["allowed"]))
    def revoke():
        if clock.now == 1000:
            assert not ack.exists()
            state["allowed"] = False
        else:
            assert json.loads(ack.read_text())["request_id"] == "decision-123"
    clock.on_sleep = revoke
    wait(policy, clock)
    assert clock.now == 1003 and not ack.exists()


def test_existing_ack_never_releases_work_while_request_still_live(tmp_path):
    policy, clock, req, ack = setup(tmp_path)
    request(req)
    state = {"allowed": False}
    policy = SimpleNamespace(path=policy.path, enabled=True,
                             read=lambda: SimpleNamespace(training_allowed=state["allowed"]))
    def permit_again():
        assert ack.exists()
        state["allowed"] = True
    clock.on_sleep = permit_again
    wait(policy, clock)
    assert clock.now == 1003 and not ack.exists()


@pytest.mark.parametrize("seconds", [-1, True, float("nan"), float("inf")])
def test_invalid_sleep_rejected(tmp_path, seconds):
    policy, clock, _, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        wait(policy, clock, sleep_seconds=seconds)
