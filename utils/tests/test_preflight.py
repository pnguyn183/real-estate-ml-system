"""Preflight never admits workload on missing data or retries correctness errors."""
from copy import deepcopy

from confluent_kafka import KafkaError, KafkaException
import pytest

import research.preflight as preflight


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def time(self):
        return 1_000_000 + self.now

    def sleep(self, seconds):
        assert 0 <= seconds <= 0.2
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    result = Clock()
    monkeypatch.setattr(preflight, "time", result)
    return result


def healthy(**overrides):
    return {"cpu_percent": 10, "ram_percent": 30, "kafka_lag": 0,
            "incoming_rate": 0, "instrumentation_ready": True, "errors": [], **overrides}


def transient(source="http://localhost:8005/metrics"):
    detail = {"source": source, "error_type": "ReadTimeout"}
    return healthy(instrumentation_ready=False, collection_transport_errors=[detail],
                   worker_transport_errors=[detail], errors=[f"{source}: ReadTimeout: temporarily unavailable"])


class Collector:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.calls = 0

    def sample(self):
        self.calls += 1
        assert self.rows, "preflight sampled after a terminal decision"
        value = self.rows.pop(0)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


def test_two_complete_healthy_samples_return_topology_and_idle_measurement(clock):
    idle = healthy(timestamp=123)
    collector = Collector(healthy(incoming_rate=None), idle)
    attempts = []
    topology = [{"partition": 0, "leader": 1}]
    result = preflight.wait_for_ready(collector, lambda: topology, attempts=attempts)
    assert result == (topology, idle)
    assert collector.calls == 2
    assert attempts == [{"count": 1, "timestamp": 1_000_000, "status": "ready", "retryable": False, "errors": []}]
    assert clock.sleeps == []


def test_default_timeout_retains_single_attempt_on_transport_error(clock):
    collector = Collector(transient(), healthy(), healthy())
    attempts = []
    with pytest.raises(RuntimeError, match="preflight needs drained queue and healthy telemetry"):
        preflight.wait_for_ready(collector, lambda: [], attempts=attempts)
    assert collector.calls == 1 and not clock.sleeps
    assert attempts[-1]["status"] == "failed" and attempts[-1]["retryable"] is True


@pytest.mark.parametrize("failure_at", [0, 1])
def test_transport_error_retries_an_entire_pair_of_healthy_samples(clock, failure_at):
    rows = [healthy()] * failure_at + [transient(), healthy(), healthy(timestamp=456)]
    collector = Collector(*rows)
    attempts = []
    _, idle = preflight.wait_for_ready(collector, lambda: [], timeout_seconds=10, attempts=attempts)
    assert collector.calls == failure_at + 3
    assert idle["timestamp"] == 456
    assert [row["status"] for row in attempts] == ["retry", "ready"]
    assert clock.now == pytest.approx(2)
    assert max(clock.sleeps) <= 0.2


def test_persistent_transport_failure_exhausts_fixed_deadline(clock):
    collector = Collector(*(transient() for _ in range(10)))
    attempts = []
    with pytest.raises(RuntimeError, match="preflight readiness timeout.*8005/metrics"):
        preflight.wait_for_ready(collector, lambda: [], timeout_seconds=5, retry_seconds=2, attempts=attempts)
    assert collector.calls == 3
    assert clock.now == pytest.approx(5)
    assert [row["status"] for row in attempts] == ["retry", "retry", "retry", "failed"]
    assert attempts[-1]["count"] == 4


@pytest.mark.parametrize("code", [KafkaError._TIMED_OUT, KafkaError._TRANSPORT, KafkaError._ALL_BROKERS_DOWN])
def test_classified_kafka_topology_transport_failure_can_recover(clock, code):
    calls = []

    def topology():
        calls.append(1)
        if len(calls) == 1:
            raise KafkaException(KafkaError(code))
        return ["all three brokers"]

    collector = Collector(healthy(), healthy())
    attempts = []
    result, _ = preflight.wait_for_ready(collector, topology, timeout_seconds=10, attempts=attempts)
    assert result == ["all three brokers"]
    assert len(calls) == 2 and collector.calls == 2
    assert attempts[0]["retryable"] is True
    assert "KafkaException" in attempts[0]["errors"][0]


@pytest.mark.parametrize("failure", [
    RuntimeError("third broker missing"), RuntimeError("all replicas must be in sync"),
    KafkaException(KafkaError(KafkaError.TOPIC_AUTHORIZATION_FAILED)),
    RuntimeError("KafkaException: Local: Timed out"),
])
def test_invalid_topology_and_non_transport_kafka_failures_are_not_retried(clock, failure):
    collector = Collector()
    attempts = []
    calls = []

    def topology():
        calls.append(1)
        raise failure

    with pytest.raises(RuntimeError, match="preflight topology"):
        preflight.wait_for_ready(collector, topology, timeout_seconds=20, attempts=attempts)
    assert calls == [1] and collector.calls == 0 and not clock.sleeps
    assert attempts[-1]["retryable"] is False


@pytest.mark.parametrize("rows, message", [
    ([healthy(kafka_lag=2)], "preflight needs drained queue"),
    ([healthy(), healthy(kafka_lag=2)], "concurrent traffic or missing telemetry"),
    ([healthy(), healthy(incoming_rate=3)], "concurrent traffic or missing telemetry"),
    ([healthy(), healthy(incoming_rate=None)], "concurrent traffic or missing telemetry"),
    ([healthy(), healthy(incoming_rate=True)], "concurrent traffic or missing telemetry"),
    ([healthy(), healthy(incoming_rate=float("nan"))], "concurrent traffic or missing telemetry"),
    ([healthy(instrumentation_ready=False)], "preflight needs drained queue"),
    ([healthy(), healthy(instrumentation_ready=False)], "concurrent traffic or missing telemetry"),
    ([healthy(errors=["counter reset"])], "counter reset"),
    ([healthy(cpu_percent=None)], "required CPU"),
    ([{"not": "telemetry"}], "required CPU"),
    ([None], "measurement mapping"),
])
def test_non_transport_measurement_failures_are_not_retried(clock, rows, message):
    collector = Collector(*rows)
    attempts = []
    with pytest.raises(RuntimeError, match=message):
        preflight.wait_for_ready(collector, lambda: [], timeout_seconds=20, attempts=attempts)
    assert collector.calls == len(rows)
    assert attempts[-1]["retryable"] is False and not clock.sleeps


def test_positive_lag_or_concurrent_traffic_overrides_transient_failure(clock):
    for rows in ([{**transient(), "kafka_lag": 1}], [healthy(), {**transient(), "incoming_rate": 2}]):
        attempts = []
        with pytest.raises(RuntimeError):
            preflight.wait_for_ready(Collector(*rows), lambda: [], timeout_seconds=20, attempts=attempts)
        assert attempts[-1]["retryable"] is False
    assert not clock.sleeps


def test_memory_pressure_stops_without_retry_even_when_transport_is_retryable(clock):
    collector = Collector(transient())
    attempts = []
    with pytest.raises(RuntimeError, match="VM memory pressure"):
        preflight.wait_for_ready(collector, lambda: [], timeout_seconds=20, attempts=attempts,
                                 memory_check=lambda row: "VM memory pressure")
    assert collector.calls == 1 and not clock.sleeps
    assert attempts[-1]["retryable"] is False


def test_memory_check_is_applied_to_both_samples(clock):
    seen = []
    preflight.wait_for_ready(Collector(healthy(timestamp=1), healthy(timestamp=2)), lambda: [],
                             memory_check=lambda row: seen.append(row["timestamp"]))
    assert seen == [1, 2]


def test_cancel_before_any_check_does_not_touch_collector_or_topology(clock):
    collector = Collector()
    attempts = []
    with pytest.raises(preflight.PreflightCancelled):
        preflight.wait_for_ready(collector, lambda: pytest.fail("topology called after stop"),
                                 stop_requested=lambda: True, attempts=attempts)
    assert collector.calls == 0
    assert attempts[-1]["status"] == "cancelled"


def test_stop_is_responsive_during_retry_wait(clock):
    collector = Collector(transient())
    attempts = []
    with pytest.raises(preflight.PreflightCancelled):
        preflight.wait_for_ready(collector, lambda: [], timeout_seconds=60, retry_seconds=10,
                                 stop_requested=lambda: clock.now >= 0.35, attempts=attempts)
    assert collector.calls == 1
    assert clock.now == pytest.approx(0.4)
    assert attempts[-1]["status"] == "cancelled"


def test_stop_between_samples_does_not_collect_second_sample(clock):
    collector = Collector(healthy())
    with pytest.raises(preflight.PreflightCancelled):
        preflight.wait_for_ready(collector, lambda: [], stop_requested=lambda: collector.calls == 1)
    assert collector.calls == 1


def test_inflight_call_cannot_be_cancelled_but_no_next_call_starts_after_deadline(clock):
    collector = Collector()

    def slow_topology():
        clock.now += 10
        return []

    with pytest.raises(RuntimeError, match="preflight readiness timeout"):
        preflight.wait_for_ready(collector, slow_topology, timeout_seconds=5)
    assert collector.calls == 0 and clock.now == 10


def test_errors_are_bounded_and_arbitrary_sample_fields_are_not_persisted(clock):
    collector = Collector(healthy(errors=["worker: " + "X" * 10000], private_field="do not persist"))
    attempts = []
    with pytest.raises(RuntimeError) as error:
        preflight.wait_for_ready(collector, lambda: [], attempts=attempts)
    assert len(str(error.value)) <= 2000
    assert len(attempts[0]["errors"][0]) <= 2000
    assert "do not persist" not in str(attempts)


@pytest.mark.parametrize("config", [
    {"timeout_seconds": -1}, {"timeout_seconds": float("inf")}, {"timeout_seconds": True},
    {"retry_seconds": 0}, {"retry_seconds": -1}, {"retry_seconds": float("nan")},
    {"retry_seconds": "2"}, {"timeout_seconds": 10 ** 1000},
])
def test_invalid_budgets_fail_before_reading_dependencies(clock, config):
    with pytest.raises(ValueError):
        preflight.wait_for_ready(Collector(), lambda: pytest.fail("unexpected topology call"), **config)
