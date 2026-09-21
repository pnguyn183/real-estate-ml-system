"""Counter reset, missing telemetry, and partition-attribution safeguards."""
from __future__ import annotations

import copy
from unittest.mock import Mock
import pytest

from research.telemetry import TelemetryCollector, histogram_quantile, parse_docker_stats, size_bytes


def test_docker_units_and_cpu_core_percent_are_not_host_percent():
    assert size_bytes("1.5MiB") == 1.5 * 1024 ** 2
    assert size_bytes("1.5MB") == 1_500_000
    stats = parse_docker_stats([{"Name": "broker", "ID": "abc", "MemUsage": "1MiB / 1GiB",
                                 "NetIO": "2kB / 3kB", "BlockIO": "0B / 4MB", "CPUPerc": "120.5%"}])
    assert stats["broker"]["cpu_core_percent"] == 120.5
    assert stats["broker"]["network_rx_bytes"] == 2000


def kafka_snapshot(timestamp, high=100, committed=90, leader=1):
    return {"kafka": {"timestamp": timestamp, "partitions": {
        str(i): {"leader": leader if i == 0 else i + 1, "replicas": [1, 2, 3], "isr": [1, 2, 3],
                 "high_offset": high, "low_offset": 0, "committed_offset": committed, "lag": high - committed}
        for i in range(3)
    }}}


def empty_result():
    return {"incoming_rate": None, "throughput": None, "brokers": {str(i): {} for i in (1, 2, 3)}, "errors": []}


def test_kafka_all_partitions_and_committed_positions_are_used():
    result = empty_result()
    TelemetryCollector._derive_kafka(kafka_snapshot(10, 120, 100), kafka_snapshot(5), result)
    assert result["kafka_lag"] == 60
    assert result["incoming_rate"] == 12
    assert result["throughput"] == 6
    assert result["kafka_interval_seconds"] == 5
    assert result["brokers"]["3"]["leader_incoming_rate"] == 4
    assert result["broker_ingress_max_mean"] == 1
    assert result["brokers"]["3"]["leader_incoming_share"] == pytest.approx(1 / 3)


def test_leader_migration_preserves_group_rates_but_invalidates_broker_attribution():
    result = empty_result()
    TelemetryCollector._derive_kafka(kafka_snapshot(10, 120, 100, leader=2), kafka_snapshot(5), result)
    assert result["incoming_rate"] == 12
    assert all(broker["leader_incoming_rate"] is None for broker in result["brokers"].values())
    assert result["errors"]


def test_kafka_offset_reset_does_not_become_negative_or_zero_rate():
    result = empty_result()
    TelemetryCollector._derive_kafka(kafka_snapshot(10, 20, 10), kafka_snapshot(5), result)
    assert result["incoming_rate"] is None
    assert result["throughput"] is None


def test_missing_committed_offset_is_not_zero_backlog():
    current = kafka_snapshot(5)
    current["kafka"]["partitions"]["0"].update(committed_offset=None, lag=None)
    result = empty_result()
    TelemetryCollector._derive_kafka(current, {}, result)
    assert result["kafka_lag"] is None
    assert result["incoming_rate"] is None


def test_histogram_quantile_interpolates_and_refuses_unbounded_tail():
    assert histogram_quantile({1.: 80, 2.: 100, float("inf"): 100}, .95) == 1.75
    assert histogram_quantile({1.: 80, float("inf"): 100}, .95) is None
    assert histogram_quantile({1.: 0, float("inf"): 0}, .95) is None


def test_container_replacement_invalidates_network_derivative():
    stats = parse_docker_stats([{"Name": "real_estate_kafka_1", "ID": "new", "MemUsage": "1MiB / 1GiB",
                                 "NetIO": "2kB / 3kB", "BlockIO": "0B / 4MB", "CPUPerc": "120%"}])
    current = {"docker": {"timestamp": 10, "engine": {"cpu_count": 4, "memory_bytes": 1024**3}, "containers": stats}}
    previous = copy.deepcopy(current)
    previous["docker"]["timestamp"] = 5
    previous["docker"]["containers"]["real_estate_kafka_1"]["container_id"] = "old"
    result = empty_result()
    TelemetryCollector._derive_docker(current, previous, result)
    assert result["cpu_percent"] == 30
    assert result["ram_percent"] == 100 / 1024
    assert result["brokers"]["1"]["network_rx_bytes_per_second"] is None


def test_missing_worker_response_does_not_create_fake_zero_error_rate():
    collector = object.__new__(TelemetryCollector)
    collector.processor_urls = ("a", "b", "c")
    result = {"error_rate": None, "latency_p95_seconds": None, "errors": []}
    collector._derive_workers({"workers": {"a": {"samples": {}}}}, {}, result)
    assert result["error_rate"] is None


def test_worker_counter_reset_invalidates_interval():
    collector = object.__new__(TelemetryCollector)
    collector.processor_urls = ("a",)
    key = ("processor_input_outcomes_total", (("topic", "test"), ("outcome", "handled")))
    previous = {"workers": {"a": {"samples": {key: 100}}}}
    current = {"workers": {"a": {"samples": {key: 2}}}}
    result = {"error_rate": None, "latency_p95_seconds": None, "errors": []}
    collector._derive_workers(current, previous, result)
    assert result["error_rate"] is None
    assert "reset" in result["errors"][0]


@pytest.mark.parametrize("version", [None, 0, 2])
def test_idle_worker_missing_instrumentation_is_a_preflight_error(monkeypatch, version):
    collector = object.__new__(TelemetryCollector)
    collector.topic, collector.timeout = "test", 5
    text = "# HELP processor_input_handling_seconds Existing empty labelled histogram\n"
    if version is not None:
        text += f"processor_traffic_instrumentation_version {version}\n"
    monkeypatch.setattr("research.telemetry.requests.get", lambda *a, **kw: Mock(text=text))
    with pytest.raises(RuntimeError, match="instrumentation v1 missing"):
        collector._worker("http://worker/metrics")


def test_idle_instrumented_worker_can_create_first_histogram_series(monkeypatch):
    collector = object.__new__(TelemetryCollector)
    collector.processor_urls, collector.topic, collector.timeout = ("a",), "test", 5
    idle = "processor_traffic_instrumentation_version 1\nprocess_start_time_seconds 100\n"
    monkeypatch.setattr("research.telemetry.requests.get", lambda *a, **kw: Mock(text=idle))
    previous = {"workers": {"a": collector._worker("a")}}
    active = idle + '\n'.join([
        'processor_input_outcomes_total{topic="test",outcome="handled"} 2',
        'processor_input_handling_seconds_count{topic="test"} 2',
        'processor_input_handling_seconds_sum{topic="test"} 0.1',
        'processor_input_end_to_end_seconds_bucket{topic="test",le="1"} 2',
        'processor_input_end_to_end_seconds_bucket{topic="test",le="+Inf"} 2',
    ]) + '\n'
    monkeypatch.setattr("research.telemetry.requests.get", lambda *a, **kw: Mock(text=active))
    current = {"workers": {"a": collector._worker("a")}}
    result = {"error_rate": None, "latency_p95_seconds": None, "errors": []}
    collector._derive_workers(current, previous, result)
    assert result["error_rate"] == 0
    assert result["processing_mean_seconds"] == .05
    assert result["latency_sample_count"] == 2
    assert result["latency_p95_seconds"] == .95
    assert result["errors"] == []


def test_worker_restart_invalidates_delta_even_when_counter_has_caught_up():
    collector = object.__new__(TelemetryCollector)
    collector.processor_urls = ("a",)
    key = ("processor_input_outcomes_total", (("topic", "test"), ("outcome", "handled")))
    previous = {"workers": {"a": {"samples": {key: 100}, "process_start_time": 5}}}
    current = {"workers": {"a": {"samples": {key: 200}, "process_start_time": 10}}}
    result = {"error_rate": None, "latency_p95_seconds": None, "errors": []}
    collector._derive_workers(current, previous, result)
    assert result["error_rate"] is None
    assert "restarted" in result["errors"][0]
