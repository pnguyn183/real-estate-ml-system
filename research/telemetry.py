"""Read-only measurements for the existing Docker/Kafka pipeline.

Broker ``leader_incoming_rate`` is the rate of log-offset growth on partitions
currently led by that broker, NOT a Kafka JMX MessagesIn count or replication
traffic. Docker network counters include replication, clients and administration.
CPU is percent of the Docker engine's capacity used by the selected containers;
RAM is their Docker working set divided by engine RAM. Neither is host-wide load.
Unavailable measurements stay None and errors are persisted, never zero-filled.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import time
from typing import Any

import requests
from prometheus_client.parser import text_string_to_metric_families


DEFAULT_CONTAINERS = (
    "real_estate_kafka_1", "real_estate_kafka_2", "real_estate_kafka_3",
    "real_estate_processor_1", "real_estate_processor_2", "real_estate_processor_3",
    "real_estate_mongodb",
)


def size_bytes(value: str) -> float:
    """Parse Docker's displayed SI/IEC units (CLI values have rounding error)."""
    match = re.fullmatch(r"\s*([0-9.]+)\s*([kmgtpe]?i?b)\s*", value, flags=re.I)
    if match is None:
        raise ValueError(f"Unsupported Docker size {value!r}")
    unit = match[2].lower()
    power = "bkmgtpe".index(unit[0])
    return float(match[1]) * (1024 if "i" in unit else 1000) ** power


def parse_docker_stats(rows: list[dict]) -> dict[str, dict]:
    result = {}
    for row in rows:
        name = row["Name"]
        memory, limit = (size_bytes(v) for v in row["MemUsage"].split("/"))
        rx, tx = (size_bytes(v) for v in row["NetIO"].split("/"))
        read, written = (size_bytes(v) for v in row["BlockIO"].split("/"))
        result[name] = {
            "cpu_core_percent": float(row["CPUPerc"].rstrip("%")),
            "memory_bytes": memory, "memory_limit_bytes": limit,
            "network_rx_bytes": rx, "network_tx_bytes": tx,
            "disk_read_bytes": read, "disk_write_bytes": written,
            "container_id": row["ID"],
        }
    return result


def histogram_quantile(buckets: dict[float, float], quantile: float) -> float | None:
    """Prometheus-style interpolated quantile from cumulative bucket deltas."""
    total = buckets.get(float("inf"), 0)
    if total <= 0:
        return None
    rank, last_bound, last_count = quantile * total, 0.0, 0.0
    for bound, count in sorted(buckets.items()):
        if count < last_count:
            return None
        if count >= rank:
            if math.isinf(bound):
                # Do not pretend the last finite bound measures unbounded tails.
                return None
            if count == last_count:
                return bound
            return last_bound + (bound - last_bound) * (rank - last_count) / (count - last_count)
        last_bound, last_count = bound, count
    return None


def counter_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or current < previous:
        return None
    return current - previous


def _command(argv: list[str], timeout: float = 15) -> str:
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True)
    return completed.stdout


class TelemetryCollector:
    def __init__(self, bootstrap_servers: str = "localhost:9092,localhost:9093,localhost:9094",
                 topic: str = "real_estate_stress_raw", group_id: str = "real_estate_training_pipeline",
                 processor_urls: tuple[str, ...] = ("http://localhost:8003/metrics", "http://localhost:8004/metrics", "http://localhost:8005/metrics"),
                 container_names: tuple[str, ...] = DEFAULT_CONTAINERS, timeout: float = 5):
        from confluent_kafka import Consumer
        self.topic, self.group_id = topic, group_id
        self.processor_urls, self.container_names, self.timeout = processor_urls, container_names, timeout
        # This observer never subscribes/polls, so it never joins or rebalances the group.
        self.consumer = Consumer({"bootstrap.servers": bootstrap_servers, "group.id": group_id,
                                  "enable.auto.commit": False, "enable.auto.offset.store": False,
                                  "socket.timeout.ms": max(1000, int(timeout * 1000))})
        self._previous: dict | None = None
        self._engine: dict | None = None

    def close(self) -> None:
        self.consumer.close()

    def _docker(self) -> dict:
        if self._engine is None:
            raw = _command(["docker", "info", "--format", '{{json .NCPU}} {{json .MemTotal}}'], self.timeout)
            cores, memory = raw.split()
            self._engine = {"cpu_count": int(cores), "memory_bytes": int(memory)}
        output = _command(["docker", "stats", "--no-stream", "--format", "{{json .}}", *self.container_names], max(10, self.timeout))
        containers = parse_docker_stats([json.loads(line) for line in output.splitlines() if line.strip()])
        missing = set(self.container_names) - set(containers)
        if missing:
            raise RuntimeError(f"Missing Docker measurements: {sorted(missing)}")
        return {"timestamp": time.time(), "engine": self._engine, "containers": containers}

    def _kafka(self) -> dict:
        from confluent_kafka import TopicPartition
        metadata = self.consumer.list_topics(self.topic, timeout=self.timeout)
        topic = metadata.topics[self.topic]
        if topic.error is not None:
            raise RuntimeError(str(topic.error))
        topic_partitions = [TopicPartition(self.topic, partition) for partition in sorted(topic.partitions)]
        committed = {item.partition: item.offset for item in self.consumer.committed(topic_partitions, timeout=self.timeout)}
        partitions = {}
        for partition in topic_partitions:
            details = topic.partitions[partition.partition]
            low, high = self.consumer.get_watermark_offsets(partition, timeout=self.timeout, cached=False)
            offset = committed[partition.partition]
            # No committed position is an observation gap, not proof of zero lag.
            partitions[str(partition.partition)] = {
                "leader": details.leader, "replicas": list(details.replicas), "isr": list(details.isrs),
                "low_offset": low, "high_offset": high, "committed_offset": offset if offset >= 0 else None,
                "lag": max(high - offset, 0) if low <= offset <= high else None,
            }
        return {"timestamp": time.time(), "partitions": partitions, "broker_ids": sorted(metadata.brokers)}

    def _worker(self, url: str) -> dict:
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        samples, version, process_started = {}, None, None
        wanted = {"processor_input_outcomes_total", "processor_input_handling_seconds_count",
                  "processor_input_handling_seconds_sum", "processor_input_end_to_end_seconds_count",
                  "processor_input_end_to_end_seconds_sum", "processor_input_end_to_end_seconds_bucket"}
        for family in text_string_to_metric_families(response.text):
            for sample in family.samples:
                if sample.name == "processor_traffic_instrumentation_version":
                    version = sample.value
                elif sample.name == "process_start_time_seconds":
                    process_started = sample.value
                if sample.name in wanted and sample.labels.get("topic") == self.topic:
                    key = (sample.name, tuple(sorted(sample.labels.items())))
                    samples[key] = sample.value
        if version != 1:
            raise RuntimeError("traffic instrumentation v1 missing; rebuild/restart this processor before the experiment")
        return {"timestamp": time.time(), "samples": samples, "instrumentation_version": version,
                "process_start_time": process_started}

    def sample(self) -> dict[str, Any]:
        """Collect one observation; first observation has no counter-derived rates."""
        started = time.time()
        errors, raw = [], {"workers": {}}
        with ThreadPoolExecutor(max_workers=2 + len(self.processor_urls)) as pool:
            jobs = {"docker": pool.submit(self._docker), "kafka": pool.submit(self._kafka)}
            for url in self.processor_urls:
                jobs[url] = pool.submit(self._worker, url)
            for source, future in jobs.items():
                try:
                    result = future.result()
                    if source in {"docker", "kafka"}:
                        raw[source] = result
                    else:
                        raw["workers"][source] = result
                except Exception as exc:
                    errors.append(f"{source}: {type(exc).__name__}: {exc}")
        now = time.time()
        raw["timestamp"] = now
        previous = self._previous or {}
        interval = now - previous["timestamp"] if previous else None
        result = {"timestamp": now, "timestamp_iso": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                  "interval_seconds": interval, "incoming_rate": None, "throughput": None,
                  "cpu_percent": None, "ram_percent": None, "memory_bytes": None, "kafka_lag": None,
                  "latency_p95_seconds": None, "processing_mean_seconds": None, "error_rate": None,
                  "brokers": {str(i): {} for i in (1, 2, 3)}, "errors": errors,
                  "instrumentation_ready": len(raw["workers"]) == len(self.processor_urls),
                  "collection_started_at": started, "collection_ended_at": now,
                  "collection_duration_seconds": now - started,
                  "source_timestamps": {**{source: raw[source]["timestamp"] for source in ("kafka", "docker") if source in raw},
                                        "workers": {url: item["timestamp"] for url, item in raw["workers"].items()}},
                  "metric_scope": "stress-topic; selected Kafka/processor/Mongo containers; Docker-engine capacity"}
        self._derive_kafka(raw, previous, result)
        self._derive_docker(raw, previous, result)
        self._derive_workers(raw, previous, result)
        self._previous = raw
        return result

    @staticmethod
    def _derive_kafka(raw: dict, previous: dict, result: dict) -> None:
        current = raw.get("kafka")
        if not current:
            return
        partitions = current["partitions"]
        result["partitions"] = partitions
        result["kafka_interval_seconds"] = None
        result["kafka_broker_ids"] = current.get("broker_ids", [])
        result["kafka_under_replicated_partitions"] = sum(len(p["isr"]) < len(p["replicas"]) for p in partitions.values())
        lags = [p["lag"] for p in partitions.values()]
        result["kafka_lag"] = sum(lags) if all(v is not None for v in lags) else None
        for broker_id, broker in result["brokers"].items():
            broker["leader_partitions"] = sum(p["leader"] == int(broker_id) for p in partitions.values())
            broker["replica_partitions"] = sum(int(broker_id) in p["replicas"] for p in partitions.values())
            broker["leader_incoming_rate"] = None
        old = previous.get("kafka")
        if not old or partitions.keys() != old["partitions"].keys():
            return
        interval = current["timestamp"] - old["timestamp"]
        if interval <= 0:
            return
        result["kafka_interval_seconds"] = interval
        rates, consumed, attribution_valid = [], [], True
        for key, partition in partitions.items():
            prev = old["partitions"][key]
            high_delta = counter_delta(partition["high_offset"], prev["high_offset"])
            commit_delta = counter_delta(partition["committed_offset"], prev["committed_offset"])
            rates.append(high_delta / interval if high_delta is not None else None)
            consumed.append(commit_delta / interval if commit_delta is not None else None)
            if partition["leader"] != prev["leader"] or high_delta is None:
                attribution_valid = False
        if all(v is not None for v in rates):
            result["incoming_rate"] = sum(rates)
        if all(v is not None for v in consumed):
            result["throughput"] = sum(consumed)
        if attribution_valid:
            for broker_id, broker in result["brokers"].items():
                broker["leader_incoming_rate"] = sum(rate for rate, p in zip(rates, partitions.values()) if p["leader"] == int(broker_id))
            loads = [broker["leader_incoming_rate"] for broker in result["brokers"].values()]
            mean = sum(loads) / len(loads)
            result["broker_ingress_max_mean"] = max(loads) / mean if mean else None
            result["broker_ingress_cv"] = (sum((v - mean) ** 2 for v in loads) / len(loads)) ** .5 / mean if mean else None
            for broker in result["brokers"].values():
                broker["leader_incoming_share"] = broker["leader_incoming_rate"] / sum(loads) if sum(loads) else None
        else:
            result["errors"].append("Kafka leadership/offset changed: broker ingress attribution unavailable for this interval")

    @staticmethod
    def _derive_docker(raw: dict, previous: dict, result: dict) -> None:
        current, old = raw.get("docker"), previous.get("docker")
        if not current:
            return
        containers, engine = current["containers"], current["engine"]
        result["containers"], result["docker_engine"] = containers, engine
        result["memory_bytes"] = sum(item["memory_bytes"] for item in containers.values())
        result["cpu_percent"] = sum(item["cpu_core_percent"] for item in containers.values()) / engine["cpu_count"]
        result["ram_percent"] = 100 * result["memory_bytes"] / engine["memory_bytes"]
        for name, item in containers.items():
            prior = old["containers"].get(name) if old else None
            for counter in ("network_rx_bytes", "network_tx_bytes", "disk_read_bytes", "disk_write_bytes"):
                delta = counter_delta(item[counter], prior[counter]) if prior and prior["container_id"] == item["container_id"] else None
                interval = current["timestamp"] - old["timestamp"] if old else 0
                item[counter + "_per_second"] = delta / interval if delta is not None and interval > 0 else None
        for broker_id, broker in result["brokers"].items():
            broker.update(containers.get(f"real_estate_kafka_{broker_id}", {}))

    def _derive_workers(self, raw: dict, previous: dict, result: dict) -> None:
        if len(raw["workers"]) != len(self.processor_urls):
            return
        totals: dict[str, float] = {}
        buckets: dict[float, float] = {}
        for url, worker in raw["workers"].items():
            old = previous.get("workers", {}).get(url)
            if old is None:
                return
            if worker.get("process_start_time") != old.get("process_start_time"):
                result["errors"].append(f"worker restarted: {url}; interval histogram and outcome deltas unavailable")
                return
            # Missing series becomes zero only when an endpoint was successfully
            # scraped and the topic label had not yet been created there.
            for key in worker["samples"].keys() | old["samples"].keys():
                current_value = worker["samples"].get(key)
                if current_value is None:
                    result["errors"].append(f"worker counter disappeared/reset: {url}")
                    return
                delta = counter_delta(current_value, old["samples"].get(key, 0))
                if delta is None:
                    result["errors"].append(f"worker counter reset: {url}")
                    return
                name, pairs = key
                labels = dict(pairs)
                if name == "processor_input_end_to_end_seconds_bucket":
                    bound = float(labels["le"])
                    buckets[bound] = buckets.get(bound, 0) + delta
                elif name == "processor_input_outcomes_total":
                    outcome = labels["outcome"]
                    totals[outcome] = totals.get(outcome, 0) + delta
                else:
                    totals[name] = totals.get(name, 0) + delta
        result["latency_p95_seconds"] = histogram_quantile(buckets, .95)
        result["latency_sample_count"] = buckets.get(float("inf"), 0)
        count = totals.get("processor_input_handling_seconds_count", 0)
        if count:
            result["processing_mean_seconds"] = totals["processor_input_handling_seconds_sum"] / count
        denominator = sum(totals.get(key, 0) for key in ("handled", "invalid", "dlq", "failed"))
        if denominator:
            result["error_rate"] = sum(totals.get(key, 0) for key in ("invalid", "dlq", "failed")) / denominator
        result["worker_outcome_counts"] = {key: totals.get(key, 0) for key in ("handled", "invalid", "dlq", "failed")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--bootstrap-servers", default="localhost:9092,localhost:9093,localhost:9094")
    args = parser.parse_args()
    if args.samples < 1 or args.interval <= 0:
        parser.error("samples and interval must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    collector = TelemetryCollector(bootstrap_servers=args.bootstrap_servers)
    try:
        with args.output.open("x", encoding="utf-8") as output:
            for index in range(args.samples):
                started = time.monotonic()
                record = collector.sample()
                output.write(json.dumps(record, allow_nan=False) + "\n")
                output.flush()
                if index + 1 < args.samples:
                    time.sleep(max(0, args.interval - (time.monotonic() - started)))
    finally:
        collector.close()


if __name__ == "__main__":
    main()
