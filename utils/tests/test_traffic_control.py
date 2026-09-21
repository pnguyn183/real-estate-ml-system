"""Policy safety and measured reaction accounting, without simulating Kafka."""
from dataclasses import replace
import json

import pytest

from agents.traffic_control import ControlConfig, Observation, TrafficController


def sample(timestamp, **changes):
    return Observation(timestamp=timestamp, incoming_rate=200, throughput=80,
                       cpu_percent=changes.pop("cpu_percent", 30),
                       ram_percent=40, kafka_lag=changes.pop("kafka_lag", 10),
                       latency_p95_seconds=.1, error_rate=0, **changes)


def controller(**changes):
    return TrafficController(ControlConfig(enabled=True, cooldown_seconds=2,
                                           recovery_window_seconds=3, **changes))


def test_baseline_observes_congestion_without_claiming_agent_action_or_detection():
    agent = TrafficController(ControlConfig())
    agent.observe(sample(10, cpu_percent=95))
    decision = agent.decide(12)
    assert decision.reason == "fixed_limit_baseline" and not decision.changed
    report = agent.episode_reports()[0]
    assert report["t0"] == 10 and report["t1"] is None and report["t2"] is None
    assert report["total_response_seconds"] is None


def test_independent_observe_decide_act_recover_timestamps_and_no_premature_application():
    agent = controller()
    agent.observe(sample(10, cpu_percent=90))
    assert agent.episode_reports()[0]["t0"] == 10
    decision = agent.decide(12)
    assert decision.current_limit == 100 and decision.new_limit == 70
    assert agent.current_limit == 100
    assert agent.episode_reports()[0]["t2"] is None
    receipt = agent.acknowledge(decision, 12.25)
    assert receipt["actuation_latency_seconds"] == .25 and agent.current_limit == 70
    agent.observe(sample(14))
    agent.observe(sample(16))
    assert agent.episode_reports()[0]["t3"] is None
    agent.observe(sample(17))
    report = agent.episode_reports()[0]
    assert report["t3"] == 17 and report["status"] == "recovered"
    assert report["detection_latency_seconds"] == 2
    assert report["adjustment_latency_seconds"] == .25
    assert report["recovery_latency_seconds"] == 4.75
    assert report["total_response_seconds"] == 7


def test_hysteresis_avoids_premature_recovery_and_additive_increase():
    agent = controller()
    agent.observe(sample(10, cpu_percent=90))
    agent.acknowledge(agent.decide(10), 10)
    agent.observe(sample(11))
    assert agent.decide(11).reason == "cooldown"
    agent.observe(sample(14, cpu_percent=75))  # Between recovery and risk thresholds.
    assert agent.decide(14).reason == "hysteresis_hold"
    assert agent.episode_reports()[0]["t3"] is None
    agent.observe(sample(15))
    agent.observe(sample(18))
    decision = agent.decide(18)
    assert decision.new_limit == 75 and decision.reason == "safe_additive_increase"


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -1, True, 101])
def test_invalid_resource_telemetry_cannot_raise_rate_and_fails_closed(value):
    agent = controller()
    agent.observe(sample(10, cpu_percent=value))
    decision = agent.decide(10)
    assert decision.new_limit == 1
    assert decision.reason.startswith("telemetry_missing_or_invalid:")
    assert not agent.episode_reports()  # Invalid telemetry is not measured congestion.
    assert decision.observation["cpu_percent"] is None
    json.dumps(decision.as_dict(), allow_nan=False)


def test_stale_telemetry_and_missing_samples_break_continuous_recovery_window():
    agent = controller(telemetry_max_age_seconds=2)
    agent.observe(sample(10))
    decision = agent.decide(13)
    assert decision.reason == "telemetry_stale" and decision.new_limit == 1
    agent.acknowledge(decision, 13)
    agent.observe(sample(14))
    agent.observe(sample(18))  # Four-second gap cannot demonstrate continuous safety.
    assert agent.decide(18).new_limit == 1


def test_sustained_congestion_obeys_cooldown_and_minimum_limit():
    agent = controller(min_rate=60)
    agent.observe(sample(10, kafka_lag=2000))
    agent.acknowledge(agent.decide(10), 10)
    agent.observe(sample(11, kafka_lag=2000))
    assert not agent.decide(11).changed
    agent.observe(sample(12, kafka_lag=2000))
    agent.acknowledge(agent.decide(12), 12)
    assert agent.current_limit == 60
    agent.observe(sample(15, kafka_lag=2000))
    assert not agent.decide(15).changed
    assert len(agent.episode_reports()) == 1
    assert agent.episode_reports()[0]["t2"] == 10


def test_gate_acknowledgement_must_match_command_and_cannot_be_replayed():
    agent = controller()
    agent.observe(sample(10, cpu_percent=90))
    decision = agent.decide(10)
    assert agent.decide(11).reason == "awaiting_actuation"
    with pytest.raises(ValueError, match="predate"):
        agent.acknowledge(decision, 9)
    with pytest.raises(ValueError, match="match"):
        agent.acknowledge(decision, 11, applied_limit=200)
    agent.acknowledge(decision, 11)
    with pytest.raises(ValueError, match="pending"):
        agent.acknowledge(decision, 12)


def test_delayed_actuation_cannot_claim_recovery_that_happened_before_the_action():
    agent = controller()
    agent.observe(sample(10, cpu_percent=90))
    decision = agent.decide(10)
    agent.observe(sample(11))
    agent.observe(sample(14))
    assert agent.episode_reports()[0]["status"] == "unresolved"
    with pytest.raises(ValueError, match="latest observed"):
        agent.acknowledge(decision, 13)
    agent.acknowledge(decision, 14)
    agent.observe(sample(15))
    assert agent.episode_reports()[0]["t3"] is None
    agent.observe(sample(18))
    assert agent.episode_reports()[0]["recovery_latency_seconds"] == 4


def test_recovery_without_an_adjustment_is_not_reported_as_agent_recovery():
    agent = controller()
    agent.observe(sample(10, cpu_percent=90))
    agent.observe(sample(11))
    agent.observe(sample(14))
    report = agent.episode_reports()[0]
    assert report["status"] == "resolved_without_adjustment"
    assert report["resolved_without_adjustment_at"] == 14
    assert report["t2"] is None and report["t3"] is None
    assert report["recovery_latency_seconds"] is None


def test_telemetry_and_decision_clocks_cannot_move_backwards():
    agent = controller()
    agent.observe(sample(10))
    with pytest.raises(ValueError, match="strictly"):
        agent.observe(sample(10))
    with pytest.raises(ValueError, match="predate"):
        agent.decide(9)
    agent.decide(12)
    with pytest.raises(ValueError, match="backwards"):
        agent.decide(11)


@pytest.mark.parametrize("changes", [
    {"min_rate": 101}, {"max_rate": 10001}, {"decrease_factor": 1},
    {"increase_step": 0}, {"cpu_low_percent": 90}, {"error_high": 2},
    {"initial_rate": float("nan")}, {"recovery_window_seconds": 0},
    {"enabled": "true"}, {"cooldown_seconds": True},
])
def test_invalid_policy_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        replace(ControlConfig(), **changes)
