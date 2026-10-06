"""Admission recovery must never turn incomplete measurements into healthy data."""
from copy import deepcopy

import pytest

from research.telemetry_guard import TelemetryRecoveryConfig, TelemetryRecoveryGuard


def healthy():
    return {"cpu_percent": 10.0, "ram_percent": 45.0, "kafka_lag": 0,
            "errors": [], "instrumentation_ready": True,
            "processing_latency_ms": None, "error_rate": None}


def transient(source="http://localhost:8005/metrics", error_type="ReadTimeout"):
    return {**healthy(), "errors": [f"{source}: {error_type}: metrics unavailable"],
            "instrumentation_ready": False,
            "worker_transport_errors": [{"source": source, "error_type": error_type}]}


def guard(**kwargs):
    return TelemetryRecoveryGuard(TelemetryRecoveryConfig(max_consecutive_errors=3, **kwargs))


def test_transient_pauses_then_requires_two_healthy_samples_without_mutation():
    recovery = guard()
    assert recovery.observe(healthy(), 0)["state"] == "healthy"
    bad = transient()
    original = deepcopy(bad)
    assert recovery.observe(bad, 5) == {
        "state": "paused", "reason": "worker metrics transport unavailable; admission paused",
        "admission_paused": True, "telemetry_usable": False,
    }
    assert bad == original
    assert recovery.observe(healthy(), 10)["state"] == "paused"
    assert recovery.observe(healthy(), 15) == {
        "state": "resumed", "reason": None, "admission_paused": False, "telemetry_usable": True,
    }
    assert recovery.observe(healthy(), 100)["state"] == "healthy"
    assert not recovery.paused


def test_failure_budget_stops_and_cannot_be_resumed():
    recovery = guard()
    assert recovery.observe(transient(), 1)["state"] == "paused"
    assert recovery.observe(transient(), 2)["state"] == "paused"
    decision = recovery.observe(transient(), 3)
    assert decision["state"] == "stopped"
    assert "consecutive" in decision["reason"]
    assert recovery.observe(healthy(), 4)["state"] == "stopped"
    assert recovery.paused


def test_wall_deadline_does_not_reset_after_one_healthy_sample():
    recovery = guard(max_recovery_seconds=20)
    recovery.observe(transient(), 0)
    recovery.observe(healthy(), 5)
    assert recovery.consecutive_errors == 0
    assert recovery.deadline_reason(19.99) is None
    assert recovery.deadline_reason(20) == "telemetry recovery time budget exhausted"
    assert recovery.observe(healthy(), 21)["state"] == "stopped"


def test_wall_deadline_wins_over_late_success():
    recovery = guard(max_recovery_seconds=10)
    recovery.observe(transient(), 1)
    recovery.observe(healthy(), 6)
    assert recovery.observe(healthy(), 11)["state"] == "stopped"


def test_intermittent_errors_reset_healthy_streak_but_not_recovery_window():
    recovery = guard(max_recovery_seconds=20)
    recovery.observe(transient(), 0)
    recovery.observe(healthy(), 5)
    recovery.observe(transient(), 10)
    assert recovery.consecutive_errors == 1
    assert recovery.observe(healthy(), 15)["state"] == "paused"
    assert recovery.observe(healthy(), 20)["state"] == "stopped"


def test_fresh_failure_after_recovery_gets_a_new_bounded_window():
    recovery = guard()
    recovery.observe(transient(), 1)
    recovery.observe(healthy(), 2)
    recovery.observe(healthy(), 3)
    assert recovery.observe(transient(), 100)["state"] == "paused"
    assert recovery.deadline_reason(119) is None
    assert recovery.deadline_reason(120) is not None


@pytest.mark.parametrize("mutate", [
    lambda row: row["errors"].append("docker: Timeout: engine unavailable"),
    lambda row: row["errors"].append("worker restarted: process changed"),
    lambda row: row.pop("worker_transport_errors"),
    lambda row: row["worker_transport_errors"][0].update(error_type="HTTPError"),
    lambda row: row["worker_transport_errors"][0].update(source="http://localhost:8004/metrics"),
    lambda row: row.update(errors="worker timeout"),
    lambda row: row.update(worker_transport_errors=[None]),
    lambda row: row.update(worker_transport_errors={}),
    lambda row: row.update(errors=[]),
])
def test_only_matching_structured_worker_transport_errors_can_recover(mutate):
    row = transient()
    mutate(row)
    assert guard().observe(row, 0)["state"] == "stopped"


@pytest.mark.parametrize("source", ["docker", "kafka", "file:///worker/metrics", "http://", "http://["])
def test_non_http_worker_sources_cannot_recover(source):
    assert guard().observe(transient(source=source), 0)["state"] == "stopped"


@pytest.mark.parametrize("error_type", ["ReadTimeout", "ConnectTimeout", "ConnectionError", "Timeout"])
def test_supported_worker_transport_errors(error_type):
    assert guard().observe(transient(error_type=error_type), 0)["state"] == "paused"


def test_multiple_unique_worker_timeouts_can_recover_but_duplicates_cannot():
    row = transient()
    other = transient(source="http://localhost:8004/metrics")
    row["errors"] += other["errors"]
    row["worker_transport_errors"] += other["worker_transport_errors"]
    assert guard().observe(row, 0)["state"] == "paused"
    row["errors"].append(row["errors"][0])
    row["worker_transport_errors"].append(row["worker_transport_errors"][0])
    assert guard().observe(row, 0)["state"] == "stopped"


@pytest.mark.parametrize("key", ["cpu_percent", "ram_percent", "kafka_lag"])
@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "10", True])
def test_missing_or_invalid_core_telemetry_stops_even_with_retryable_error(key, value):
    row = transient()
    row[key] = value
    assert guard().observe(row, 0)["state"] == "stopped"


def test_default_preserves_fail_fast_behavior():
    recovery = TelemetryRecoveryGuard(TelemetryRecoveryConfig())
    assert recovery.observe(transient(), 0)["state"] == "stopped"


def test_unrepresentable_numeric_value_fails_closed_instead_of_raising():
    row = transient()
    row["kafka_lag"] = 10 ** 1000
    assert guard().observe(row, 0)["state"] == "stopped"


@pytest.mark.parametrize("readiness", [False, None, 1, "true"])
def test_no_errors_does_not_replace_explicit_readiness(readiness):
    row = healthy()
    row["instrumentation_ready"] = readiness
    assert guard().observe(row, 0)["state"] == "stopped"


@pytest.mark.parametrize("value", [None, "now", float("nan"), float("inf"), True, -1])
def test_invalid_or_backwards_clock_fails_closed(value):
    recovery = guard()
    recovery.observe(healthy(), 0)
    assert recovery.observe(healthy(), value)["state"] == "stopped"


@pytest.mark.parametrize("config", [
    {"max_consecutive_errors": 0}, {"max_consecutive_errors": 11},
    {"max_consecutive_errors": 2.0}, {"max_consecutive_errors": True},
    {"healthy_samples_to_resume": 0}, {"healthy_samples_to_resume": 6},
    {"healthy_samples_to_resume": 1.0}, {"healthy_samples_to_resume": False},
    {"max_recovery_seconds": 0}, {"max_recovery_seconds": 121},
    {"max_recovery_seconds": float("nan")}, {"max_recovery_seconds": float("inf")},
    {"max_recovery_seconds": "20"}, {"max_recovery_seconds": True},
])
def test_config_bounds(config):
    with pytest.raises(ValueError):
        TelemetryRecoveryConfig(**config)


def test_config_boundary_values_and_single_healthy_resume():
    recovery = TelemetryRecoveryGuard(TelemetryRecoveryConfig(10, 1, 1))
    assert recovery.observe(transient(), 0)["state"] == "paused"
    assert recovery.observe(healthy(), 0.5)["state"] == "resumed"
    TelemetryRecoveryConfig(1, 120, 5)


def collection_transient(*sources):
    row = healthy()
    row.update(errors=[], collection_transport_errors=[], worker_transport_errors=[], instrumentation_ready=False)
    for source in sources:
        if source == "docker":
            detail = {"source": source, "error_type": "TimeoutExpired"}
            row.update(cpu_percent=None, ram_percent=None)
        elif source == "kafka":
            detail = {"source": source, "error_type": "KafkaException", "error_code": -185}
            row["kafka_lag"] = None
        else:
            detail = {"source": source, "error_type": "ReadTimeout"}
            row["worker_transport_errors"].append(detail.copy())
        row["collection_transport_errors"].append(detail)
        row["errors"].append(f"{source}: {detail['error_type']}: transport unavailable")
    return row


@pytest.mark.parametrize("sources", [
    ("docker",), ("kafka",), ("http://localhost:8005/metrics",),
    ("docker", "kafka", "http://localhost:8005/metrics"),
])
def test_opt_in_pauses_on_classified_collection_failures_and_resumes_only_with_good_samples(sources):
    recovery = guard(allow_core_transport_recovery=True, max_recovery_seconds=60)
    row = collection_transient(*sources)
    original = deepcopy(row)
    decision = recovery.observe(row, 0)
    assert decision["state"] == "paused"
    assert decision["admission_paused"] is True
    assert decision["telemetry_usable"] is False
    assert row == original
    assert recovery.observe(healthy(), 5)["state"] == "paused"
    decision = recovery.observe(healthy(), 10)
    assert decision["state"] == "resumed"
    assert decision["admission_paused"] is False
    assert decision["telemetry_usable"] is True


def test_opt_in_preserves_compatibility_with_worker_only_samples():
    assert guard(allow_core_transport_recovery=True).observe(transient(), 0)["state"] == "paused"


@pytest.mark.parametrize("sources", [("docker",), ("kafka",), ("docker", "kafka")])
def test_default_core_transport_failures_still_fail_fast(sources):
    assert guard().observe(collection_transient(*sources), 0)["state"] == "stopped"


@pytest.mark.parametrize("missing", ["cpu_percent", "ram_percent", "kafka_lag"])
def test_missing_core_measurement_requires_classification_of_its_source(missing):
    source = "kafka" if missing != "kafka_lag" else "docker"
    row = collection_transient(source)
    row[missing] = None
    recovery = guard(allow_core_transport_recovery=True)
    assert recovery.observe(row, 0)["state"] == "stopped"
    assert recovery.observe(healthy(), 1)["state"] == "stopped"


@pytest.mark.parametrize("key", ["cpu_percent", "ram_percent", "kafka_lag"])
@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "10", True])
def test_opt_in_cannot_hide_invalid_core_measurements_without_classified_failure(key, value):
    row = healthy()
    row[key] = value
    assert guard(allow_core_transport_recovery=True).observe(row, 0)["state"] == "stopped"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "10", True, 10 ** 1000])
def test_classified_transport_failure_does_not_excuse_malformed_core_values(value):
    row = collection_transient("docker", "kafka")
    row["cpu_percent"] = value
    assert guard(allow_core_transport_recovery=True).observe(row, 0)["state"] == "stopped"


@pytest.mark.parametrize("mutate", [
    lambda row: row.pop("collection_transport_errors"),
    lambda row: row.update(collection_transport_errors=[]),
    lambda row: row.update(collection_transport_errors=[None]),
    lambda row: row["errors"].append("worker: instrumentation counters reset"),
    lambda row: row["collection_transport_errors"][0].update(source="unknown"),
    lambda row: row["collection_transport_errors"][0].update(error_type="Timeout"),
    lambda row: row["errors"].__setitem__(0, "docker: ConnectionError: timeout"),
    lambda row: row["collection_transport_errors"].append(row["collection_transport_errors"][0]),
])
def test_collection_recovery_requires_exact_one_to_one_structured_classification(mutate):
    row = collection_transient("docker", "kafka", "http://localhost:8005/metrics")
    mutate(row)
    assert guard(allow_core_transport_recovery=True).observe(row, 0)["state"] == "stopped"


@pytest.mark.parametrize("code", [-185, -195, -187])
def test_only_known_local_kafka_transport_codes_are_retryable(code):
    from confluent_kafka import KafkaError

    assert code in (KafkaError._TIMED_OUT, KafkaError._TRANSPORT, KafkaError._ALL_BROKERS_DOWN)
    row = collection_transient("kafka")
    row["collection_transport_errors"][0]["error_code"] = code
    assert guard(allow_core_transport_recovery=True).observe(row, 0)["state"] == "paused"


@pytest.mark.parametrize("code", [None, "-185", -185.0, True, -1, 0, 29, 3])
def test_missing_fake_or_non_transport_kafka_codes_are_fatal(code):
    row = collection_transient("kafka")
    row["collection_transport_errors"][0]["error_code"] = code
    assert guard(allow_core_transport_recovery=True).observe(row, 0)["state"] == "stopped"


def test_unknown_kafka_error_type_is_fatal_even_with_allowed_code():
    row = collection_transient("kafka")
    row["collection_transport_errors"][0]["error_type"] = "Timeout"
    row["errors"] = ["kafka: Timeout: unavailable"]
    assert guard(allow_core_transport_recovery=True).observe(row, 0)["state"] == "stopped"


def test_duplicate_sources_cannot_make_collection_errors_look_fully_classified():
    row = collection_transient("docker", "docker")
    assert guard(allow_core_transport_recovery=True).observe(row, 0)["state"] == "stopped"


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(instrumentation_ready=False),
    lambda row: row.update(kafka_lag=None),
    lambda row: row.update(errors=None),
    lambda row: row.update(collection_transport_errors=[{"source": "docker", "error_type": "TimeoutExpired"}]),
    lambda row: row.update(worker_transport_errors=[{"source": "http://localhost:8005/metrics", "error_type": "ReadTimeout"}]),
])
def test_recovery_cannot_resume_after_a_partially_healthy_sample(mutate):
    recovery = guard(allow_core_transport_recovery=True)
    assert recovery.observe(collection_transient("docker", "kafka"), 0)["state"] == "paused"
    row = healthy()
    mutate(row)
    assert recovery.observe(row, 1)["state"] == "stopped"
    assert recovery.observe(healthy(), 2)["state"] == "stopped"


def test_core_collection_recovery_has_same_bounded_deadline_and_error_budget():
    recovery = guard(allow_core_transport_recovery=True, max_recovery_seconds=60)
    assert recovery.observe(collection_transient("docker"), 0)["state"] == "paused"
    assert recovery.observe(healthy(), 20)["state"] == "paused"
    assert recovery.observe(collection_transient("kafka"), 40)["state"] == "paused"
    assert recovery.observe(healthy(), 59)["state"] == "paused"
    assert recovery.observe(healthy(), 60)["state"] == "stopped"

    recovery = guard(allow_core_transport_recovery=True)
    assert recovery.observe(collection_transient("docker"), 0)["state"] == "paused"
    assert recovery.observe(collection_transient("kafka"), 1)["state"] == "paused"
    assert recovery.observe(collection_transient("docker", "kafka"), 2)["state"] == "stopped"
    assert recovery.observe(healthy(), 3)["state"] == "stopped"


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_core_transport_recovery_config_requires_actual_boolean(value):
    with pytest.raises(ValueError, match="allow_core_transport_recovery"):
        TelemetryRecoveryConfig(allow_core_transport_recovery=value)
