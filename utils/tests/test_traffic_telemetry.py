"""Counter reset, missing telemetry, and partition-attribution safeguards."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from research.telemetry import TelemetryCollector, histogram_quantile, parse_docker_stats, size_bytes
from research.telemetry import parse_vm_memory, transport_error_detail


@pytest.mark.parametrize("explicit_run_id", [None, "crawl-history-test"])
def test_cli_preserves_topic_and_group_and_writes_stable_run_id(monkeypatch, tmp_path, explicit_run_id):
    from research import telemetry

    created = []

    class FakeCollector:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.closed = False
            created.append(self)

        def sample(self):
            return {"timestamp": 100, "incoming_rate": 2.5}

        def close(self):
            self.closed = True

    monkeypatch.setattr(telemetry, "TelemetryCollector", FakeCollector)
    monkeypatch.setattr(telemetry.time, "sleep", lambda _seconds: None)
    run_ids = []
    for index in range(2):
        output = tmp_path / f"history-{index}.jsonl"
        argv = ["research.telemetry", "--output", str(output), "--samples", "2", "--interval", "5",
                "--topic", "real_estate_raw", "--group-id", "crawl-processors", "--bootstrap-servers", "broker:9092"]
        if explicit_run_id is not None:
            argv += ["--run-id", explicit_run_id]
        monkeypatch.setattr("sys.argv", argv)
        telemetry.main()
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert rows[0]["run_id"] and rows[0]["run_id"] == rows[1]["run_id"]
        assert all(row["topic"] == "real_estate_raw" and row["group_id"] == "crawl-processors" for row in rows)
        assert all(row["incoming_rate"] == 2.5 and "requested_rate" not in row and "current_limit" not in row for row in rows)
        assert created[-1].options == {"bootstrap_servers": "broker:9092", "topic": "real_estate_raw", "group_id": "crawl-processors"}
        assert created[-1].closed
        run_ids.append(rows[0]["run_id"])
    if explicit_run_id is not None:
        assert run_ids == [explicit_run_id, explicit_run_id]
    else:
        assert run_ids[0] != run_ids[1]


def test_sample_scope_names_the_measured_topic(monkeypatch):
    collector = object.__new__(TelemetryCollector)
    collector.topic, collector.processor_urls, collector._previous = "real_estate_raw", (), None
    monkeypatch.setattr(collector, "_docker", lambda: {"timestamp": 100})
    monkeypatch.setattr(collector, "_kafka", lambda: {"timestamp": 100})
    for name in ("_derive_kafka", "_derive_docker", "_derive_workers"):
        monkeypatch.setattr(collector, name, lambda *_args: None)
    result = collector.sample()
    assert result["metric_scope"].startswith("Kafka topic real_estate_raw;")


@pytest.mark.parametrize("kind,retryable", [("ReadTimeout", True), ("ConnectionError", True), ("HTTPError", False), ("RuntimeError", False)])
def test_only_worker_transport_errors_receive_recovery_classification(monkeypatch, kind, retryable):
    import requests
    collector = object.__new__(TelemetryCollector)
    url = "http://localhost:8004/metrics"
    collector.topic, collector.processor_urls, collector._previous = "real_estate_stress_raw", (url,), None
    monkeypatch.setattr(collector, "_docker", lambda: {"timestamp": 100})
    monkeypatch.setattr(collector, "_kafka", lambda: {"timestamp": 100})
    exception = RuntimeError if kind == "RuntimeError" else getattr(requests.exceptions, kind)

    def fail(source):
        raise exception("fixture measurement failure")

    monkeypatch.setattr(collector, "_worker", fail)
    for name in ("_derive_kafka", "_derive_docker", "_derive_workers"):
        monkeypatch.setattr(collector, name, lambda *_args: None)
    result = collector.sample()
    assert result["errors"] == [f"{url}: {kind}: fixture measurement failure"]
    assert result["instrumentation_ready"] is False
    assert result["latency_p95_seconds"] is None and result["error_rate"] is None
    assert result["worker_transport_errors"] == ([{"source": url, "error_type": kind}] if retryable else [])


def test_real_http_timeout_can_be_followed_by_successful_scrape():
    """HTTP integration fixture, not measured Kafka or production performance."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import requests
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/delayed":
                release.wait(3)
                return
            data = b"processor_traffic_instrumentation_version 1\nprocess_start_time_seconds 100\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass  # Suppress fixture HTTP access logs.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    collector = object.__new__(TelemetryCollector)
    collector.topic, collector.timeout = "real_estate_stress_raw", .15
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(requests.exceptions.ReadTimeout):
            collector._worker(base + "/delayed")
        release.set()
        collector.timeout = 2
        result = collector._worker(base + "/metrics")
        assert result["instrumentation_version"] == 1
        assert result["process_start_time"] == 100
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_docker_units_and_cpu_core_percent_are_not_host_percent():
    assert size_bytes("1.5MiB") == 1.5 * 1024 ** 2
    assert size_bytes("1.5MB") == 1_500_000
    stats = parse_docker_stats([{"Name": "broker", "ID": "abc", "MemUsage": "1MiB / 1GiB",
                                 "NetIO": "2kB / 3kB", "BlockIO": "0B / 4MB", "CPUPerc": "120.5%"}])
    assert stats["broker"]["cpu_core_percent"] == 120.5
    assert stats["broker"]["network_rx_bytes"] == 2000


def test_vm_memory_reports_total_host_headroom_and_full_swap_separately():
    text = "MemTotal: 8388608 kB\nMemFree: 65536 kB\nMemAvailable: 524288 kB\nSwapTotal: 2097152 kB\nSwapFree: 0 kB\n"
    memory = parse_vm_memory(text)
    assert memory["available_percent"] == 6.25
    assert memory["free_bytes"] == 64 * 1024**2
    assert memory["swap_used_percent"] == 100
    no_swap = parse_vm_memory(text.replace("SwapTotal: 2097152", "SwapTotal: 0"))
    assert no_swap["swap_used_percent"] is None


@pytest.mark.parametrize("text", ["", "MemTotal: 0 kB", "MemTotal: secret bytes"])
def test_incomplete_vm_memory_is_not_zero_filled(text):
    with pytest.raises((ValueError, KeyError)):
        parse_vm_memory(text)


def test_only_known_kafka_transport_codes_and_docker_timeouts_are_recoverable():
    import subprocess
    from confluent_kafka import KafkaError, KafkaException
    for code in (KafkaError._TIMED_OUT, KafkaError._TRANSPORT, KafkaError._ALL_BROKERS_DOWN):
        detail = transport_error_detail("kafka", KafkaException(KafkaError(code)))
        assert detail == {"source": "kafka", "error_type": "KafkaException", "error_code": code}
    assert transport_error_detail("kafka", KafkaException(KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART))) is None
    assert transport_error_detail("docker", subprocess.TimeoutExpired(["docker", "stats"], 10))["error_type"] == "TimeoutExpired"
    assert transport_error_detail("docker", subprocess.CalledProcessError(1, ["docker", "stats"])) is None


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


@pytest.mark.parametrize("low,high,expected", [(0, 0, 0), (0, 3, None), (8, 8, None), (5, 8, None)])
def test_uncommitted_partition_has_zero_lag_only_when_new_and_empty(low, high, expected):
    collector = object.__new__(TelemetryCollector)
    collector.topic, collector.timeout = "real_estate_stress_raw", 1
    collector.consumer = SimpleNamespace(
        list_topics=lambda *a, **k: SimpleNamespace(
            brokers={1: None}, topics={collector.topic: SimpleNamespace(error=None, partitions={
                0: SimpleNamespace(leader=1, replicas=[1], isrs=[1])})}),
        committed=lambda *a, **k: [SimpleNamespace(partition=0, offset=-1001)],
        get_watermark_offsets=lambda *a, **k: (low, high),
    )
    snapshot = collector._kafka()
    assert snapshot["partitions"]["0"]["committed_offset"] is None
    assert snapshot["partitions"]["0"]["lag"] == expected


def test_new_empty_topic_warms_up_and_first_commit_does_not_require_every_partition_to_receive_data():
    previous = kafka_snapshot(5, high=0, committed=0)
    for partition in previous["kafka"]["partitions"].values():
        partition["committed_offset"] = None
    current = copy.deepcopy(previous)
    current["kafka"]["timestamp"] = 10
    empty = empty_result()
    TelemetryCollector._derive_kafka(current, previous, empty)
    assert empty["incoming_rate"] == empty["throughput"] == empty["kafka_lag"] == 0
    assert all(p["committed_offset"] is None for p in current["kafka"]["partitions"].values())

    first_commit = copy.deepcopy(current)
    first_commit["kafka"]["timestamp"] = 15
    first_commit["kafka"]["partitions"]["0"].update(high_offset=1, committed_offset=1)
    consumed = empty_result()
    TelemetryCollector._derive_kafka(first_commit, current, consumed)
    assert consumed["incoming_rate"] == consumed["throughput"] == .2
    assert consumed["kafka_lag"] == 0


def test_retained_empty_log_does_not_invent_consumption_without_committed_offsets():
    previous = kafka_snapshot(5, high=8, committed=8)
    for partition in previous["kafka"]["partitions"].values():
        partition.update(low_offset=8, committed_offset=None, lag=None)
    current = copy.deepcopy(previous)
    current["kafka"]["timestamp"] = 10
    result = empty_result()
    TelemetryCollector._derive_kafka(current, previous, result)
    assert result["incoming_rate"] == 0
    assert result["throughput"] is None and result["kafka_lag"] is None


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
