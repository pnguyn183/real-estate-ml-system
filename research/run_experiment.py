"""Finite paired Kafka experiment. Run from repo root: python -m research.run_experiment --help.

Offered demand follows the same seeded schedule in each policy. Admission is a
local rate gate before Kafka; rejected demand is counted, never hidden as extra
capacity. All accepted records use the existing isolated synthetic pipeline.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time
from uuid import uuid4

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient

from agents.generator import ListingGenerator
from agents.stress import stress_topic
from agents.traffic_control import ControlConfig, Observation, TrafficController
from research.telemetry import TelemetryCollector


@contextmanager
def experiment_lock(path=Path("runtime/research/experiment.lock")):
    """Cross-process OS lock; released even when a process terminates unexpectedly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if path.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            raise RuntimeError("another traffic experiment holds the lock; wait for its drain to finish") from exc
        try:
            yield
        finally:
            lock.seek(0)
            release()


class RateGate:
    """Token bucket allowing at most 20 ms of accumulated demand (minimum two tokens)."""

    def __init__(self, limit: float, now: float):
        self.limit, self.updated, self.tokens = limit, now, max(2.0, limit * .02)

    def set_limit(self, limit: float, now: float):
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("limit must be finite and positive")
        self.tokens = min(max(2.0, self.limit * .02), self.tokens + max(0, now - self.updated) * self.limit)
        self.tokens = min(self.tokens, max(2.0, limit * .02))
        self.updated, self.limit = now, limit

    def accept(self, now: float) -> bool:
        self.tokens = min(max(2.0, self.limit * .02), self.tokens + max(0, now - self.updated) * self.limit)
        self.updated = now
        if self.tokens < 1 - 1e-9:
            return False
        self.tokens -= 1
        return True


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def append_json(stream, value):
    stream.write(json.dumps(value, allow_nan=False) + "\n")
    stream.flush()


def validate_config(config):
    profile = config["profile"]
    if not profile or any(not math.isfinite(float(s[k])) or s[k] <= 0 for s in profile for k in ("rate", "seconds")):
        raise ValueError("positive finite rates and durations required")
    if max(s["rate"] for s in profile) > 10000 or sum(s["seconds"] for s in profile) > 3600:
        raise ValueError("hard maximum is 10K records/s and 1 hour per mode")
    if not 1 <= config["max_records"] <= 1_000_000:
        raise ValueError("max_records must be in 1..1,000,000")
    if sum(math.ceil(s["rate"] * s["seconds"]) for s in profile) > config["max_records"]:
        raise ValueError("profile exceeds max_records; refusing a silently truncated comparison")
    if not 1 <= config["sample_seconds"] <= 60 or not config["sample_seconds"] <= config["decision_seconds"] <= 120:
        raise ValueError("sample 1..60s and decision >= sample, <=120s required")
    if not 1 <= config["drain_seconds"] <= 600:
        raise ValueError("drain_seconds must be in 1..600")
    ControlConfig(**config["control"])


def code_identity():
    paths = [Path("agents/traffic_control.py"), Path("agents/generator.py"),
             Path("research/run_experiment.py"), Path("research/telemetry.py"),
             Path("processing/kafka_to_mongo.py"), Path("utils/metrics.py")]
    result = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result["python"] = platform.python_version()
    result["dependencies"] = {name: importlib.metadata.version(name) for name in
                              ("confluent-kafka", "requests", "prometheus-client")}
    try:
        result["containers"] = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.ID}}"], text=True, timeout=10).splitlines()
    except (subprocess.SubprocessError, OSError) as exc:
        result["containers"] = None
        result["container_identity_error"] = type(exc).__name__
    result["worker_source_hashes"] = {}
    for service in ("processor", "processor-2", "processor-3"):
        try:
            result["worker_source_hashes"][service] = subprocess.check_output(
                ["docker", "compose", "exec", "-T", service, "python", "-c",
                 "from pathlib import Path; import hashlib; print(hashlib.sha256(Path('/app/processing/kafka_to_mongo.py').read_bytes()).hexdigest())"],
                text=True, timeout=10).strip()
        except (subprocess.SubprocessError, OSError) as exc:
            result["worker_source_hashes"][service] = {"error": type(exc).__name__}
    return result


def preflight(bootstrap, topic):
    meta = AdminClient({"bootstrap.servers": bootstrap}).list_topics(timeout=10)
    if set(meta.brokers) != {1, 2, 3}:
        raise RuntimeError("requires the existing three-broker cluster IDs 1, 2, 3")
    selected = meta.topics.get(topic)
    if selected is None or selected.error or len(selected.partitions) != 3:
        raise RuntimeError("stress topic must already have exactly 3 partitions")
    partitions = []
    for pid, p in selected.partitions.items():
        if set(p.replicas) != {1, 2, 3} or set(p.isrs) != {1, 2, 3}:
            raise RuntimeError("all three stress-topic replicas must be in sync")
        partitions.append({"partition": pid, "leader": p.leader, "replicas": p.replicas, "isrs": p.isrs})
    return partitions


def observation(sample, requested_rate):
    return Observation(**{name: requested_rate if name == "incoming_rate" else sample.get(name)
                          for name in ("timestamp", "incoming_rate", "throughput", "cpu_percent", "ram_percent",
                                       "kafka_lag", "latency_p95_seconds", "error_rate")})


def run_mode(mode, config, output, bootstrap, topic, group):
    run_id = f"traffic-{mode}-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    cfg = ControlConfig(**config["control"], enabled=mode == "adaptive")
    controller = TrafficController(cfg)
    collector = TelemetryCollector(bootstrap_servers=bootstrap, topic=topic, group_id=group)
    report = {"run_id": run_id, "mode": mode, "status": "running", "offered": 0, "admitted": 0,
              "rejected": 0, "acknowledged": 0, "delivery_failed": 0, "enqueue_failed": 0,
              "partitions": {}, "stages": [], "started_at": time.time()}
    write_json(output / "manifest.json", {"run_id": run_id, "mode": mode, "config": config,
               "control": asdict(cfg), "identity": code_identity(),
               "semantics": "requested demand is offered before a local admission gate; incoming_rate is broker-admitted; throughput is committed input handling; no replica autoscaling"})
    generator = ListingGenerator(run_id, "normal", config["seed"], duplicate_ratio=0, unstructured_ratio=0)
    producer = Producer({"bootstrap.servers": bootstrap, "client.id": run_id, "acks": "all",
                         "enable.idempotence": True, "linger.ms": 5, "delivery.timeout.ms": 10000,
                         "request.timeout.ms": 5000, "queue.buffering.max.messages": 10000})

    def delivered(error, message):
        if error:
            report["delivery_failed"] += 1
        else:
            report["acknowledged"] += 1
            key = str(message.partition())
            report["partitions"][key] = report["partitions"].get(key, 0) + 1

    executor = ThreadPoolExecutor(max_workers=1)
    future = None
    last_sample = None
    observation_count = 0
    began = time.monotonic()
    gate = RateGate(cfg.initial_rate, began)
    next_sample, next_decision = began, began
    previous_count_time, previous_admitted, previous_ack = began, 0, 0
    stage_index = -1
    stage_start = 0.0
    requested_rate = 0.0
    last_offer = None
    hard_stop = None

    try:
        report["topology"] = preflight(bootstrap, topic)
        # No offset reset or backlog deletion. Each policy starts with a drained queue.
        initial = collector.sample()
        if initial.get("kafka_lag") != 0 or initial.get("errors") or not initial.get("instrumentation_ready"):
            raise RuntimeError(f"preflight needs drained queue and healthy telemetry: lag={initial.get('kafka_lag')}, errors={initial.get('errors')}")
        idle = collector.sample()
        if idle.get("incoming_rate") != 0 or idle.get("kafka_lag") != 0 or idle.get("errors"):
            raise RuntimeError("preflight detected concurrent traffic or missing telemetry; finish other stress producers first")
        report["initial_partitions"] = idle.get("partitions", {})
        began = time.monotonic()
        report["load_started_at"] = time.time()
        next_sample, next_decision = began, began + config["decision_seconds"]
        previous_count_time = began
        gate = RateGate(cfg.initial_rate, began)
        total_seconds = sum(s["seconds"] for s in config["profile"])
        with (output / "observations.jsonl").open("x", encoding="utf-8") as samples, \
                (output / "actions.jsonl").open("x", encoding="utf-8") as actions:

            def collect(sample, phase):
                nonlocal last_sample, next_decision, previous_count_time, previous_admitted, previous_ack, observation_count, hard_stop
                now = time.monotonic()
                dt = max(now - previous_count_time, 1e-9)
                row = {**sample, "run_id": run_id, "mode": mode, "elapsed_seconds": now - began,
                       "phase": phase, "stage": stage_index, "requested_rate": requested_rate if phase == "load" else 0,
                       "current_limit": gate.limit, "offered_total": report["offered"],
                       "admitted_total": report["admitted"], "rejected_total": report["rejected"],
                       "acknowledged_total": report["acknowledged"],
                       "accepted_rate": (report["admitted"] - previous_admitted) / dt,
                       "acknowledged_rate": (report["acknowledged"] - previous_ack) / dt}
                previous_count_time, previous_admitted, previous_ack = now, report["admitted"], report["acknowledged"]
                controller.observe(observation(row, row["requested_rate"]))
                append_json(samples, row)
                observation_count += 1
                last_sample = row
                if now >= next_decision and phase == "load":
                    decision = controller.decide(time.time())
                    applied_at = None
                    if decision.changed:
                        gate.set_limit(decision.new_limit, time.monotonic())
                        applied_at = time.time()
                        controller.acknowledge(decision, applied_at)
                    append_json(actions, {**asdict(decision), "applied_at": applied_at,
                                          "elapsed_seconds": time.monotonic() - began, "mode": mode, "run_id": run_id})
                    next_decision = now + config["decision_seconds"]
                for key, bound in (("kafka_lag", config["hard_lag"]), ("cpu_percent", config["hard_cpu_percent"]),
                                   ("ram_percent", config["hard_ram_percent"])):
                    if row.get(key) is not None and row[key] >= bound:
                        hard_stop = f"hard safety threshold: {key} >= {bound}"
                if row.get("errors"):
                    hard_stop = "telemetry unavailable: " + str(row["errors"])

            while time.monotonic() - began < total_seconds and not hard_stop:
                now = time.monotonic()
                elapsed = now - began
                if stage_index < 0 or elapsed >= stage_start + config["profile"][stage_index]["seconds"]:
                    if stage_index >= 0:
                        report["stages"][-1].update(ended_at=time.time(), completed=True)
                        stage_start += config["profile"][stage_index]["seconds"]
                    stage_index += 1
                    if stage_index >= len(config["profile"]):
                        break
                    requested_rate = float(config["profile"][stage_index]["rate"])
                    report["stages"].append({"index": stage_index, "requested_rate": requested_rate,
                                             "started_at": time.time(), "start_elapsed": stage_start,
                                             "seconds": config["profile"][stage_index]["seconds"],
                                             "planned": math.ceil(requested_rate * config["profile"][stage_index]["seconds"]),
                                             "offered": 0, "admitted": 0, "completed": False})
                    last_offer = began + stage_start
                if future is not None and future.done():
                    collect(future.result(), "load")
                    future = None
                if future is None and now >= next_sample:
                    future = executor.submit(collector.sample)
                    next_sample = now + config["sample_seconds"]
                stage = report["stages"][-1]
                planned = stage["planned"]
                # Schedule from absolute deadlines; do not turn scheduler lateness into an unbounded burst.
                if now >= last_offer and stage["offered"] < planned:
                    item = generator.next(report["offered"])
                    report["offered"] += 1
                    stage["offered"] += 1
                    if gate.accept(now):
                        payload = dict(item.payload, stress_sent_at=time.time())
                        try:
                            # Same logical key in both modes preserves offered partition assignments.
                            producer.produce(topic, key=payload["listing_id"], value=json.dumps(payload, ensure_ascii=False).encode(), callback=delivered)
                            report["admitted"] += 1
                            stage["admitted"] += 1
                        except BufferError:
                            report["enqueue_failed"] += 1
                            hard_stop = "producer local queue exhausted"
                    else:
                        report["rejected"] += 1
                    last_offer = began + stage_start + stage["offered"] / requested_rate
                producer.poll(0)
                if report["delivery_failed"]:
                    hard_stop = "Kafka delivery failure"
                time.sleep(min(0.001, max(0, last_offer - time.monotonic())))
            report["load_ended_at"] = time.time()
            report["load_seconds"] = time.monotonic() - began
            if report["stages"]:
                report["stages"][-1].update(ended_at=time.time(), completed=hard_stop is None)
            report["undelivered"] = producer.flush(12)
            if future is not None:
                collect(future.result(), "drain")
                future = None
            drain_began = time.monotonic()
            # Continue measuring recovery after input stops. Label drain recovery separately.
            while time.monotonic() - drain_began < config["drain_seconds"]:
                collect(collector.sample(), "drain")
                if last_sample.get("kafka_lag") == 0 and time.monotonic() - drain_began >= cfg.recovery_window_seconds:
                    break
                time.sleep(min(config["sample_seconds"], 1))
            report["drain_seconds"] = time.monotonic() - drain_began
            report["final_lag"] = last_sample.get("kafka_lag") if last_sample else None
            report["status"] = "safety_stopped" if hard_stop else "completed"
            report["stop_reason"] = hard_stop
            if report["delivery_failed"] or report["enqueue_failed"] or report.get("undelivered"):
                report["status"] = "failed"
            if report["final_lag"] != 0:
                report["status"] = "drain_timeout"
    except KeyboardInterrupt:
        report["status"] = "interrupted"
    except Exception as exc:
        report["status"], report["error"] = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        producer.flush(12)
        executor.shutdown(wait=True)
        collector.close()
        report["ended_at"] = time.time()
        report["observation_count"] = observation_count
        report["final_partitions"] = last_sample.get("partitions", {}) if last_sample else {}
        initial_parts, final_parts = report.get("initial_partitions", {}), report["final_partitions"]
        if initial_parts and initial_parts.keys() == final_parts.keys():
            report["observed_topic_offset_growth"] = sum(final_parts[k]["high_offset"] - v["high_offset"]
                                                          for k, v in initial_parts.items())
            report["exclusive_topic_confirmed"] = report["observed_topic_offset_growth"] == report["acknowledged"]
            if not report["exclusive_topic_confirmed"]:
                report["status"] = "invalid_concurrent_traffic"
        for stage in report["stages"]:
            stage["scheduler_shortfall"] = stage["planned"] - stage["offered"]
        report["episodes"] = controller.episode_reports()
        write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("research/experiment.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bootstrap", default="localhost:9092,localhost:9093,localhost:9094")
    parser.add_argument("--topic", default="real_estate_stress_raw")
    parser.add_argument("--group", default="real_estate_training_pipeline")
    parser.add_argument("--modes", nargs="+", choices=("baseline", "adaptive"), default=["baseline", "adaptive"])
    args = parser.parse_args()
    stress_topic(args.topic)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    validate_config(config)
    output = args.output or Path("runtime/research") / datetime.now(timezone.utc).strftime("traffic-%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config.json", config)
    summaries = []
    with experiment_lock():
        for mode in args.modes:
            result = run_mode(mode, config, output / mode, args.bootstrap, args.topic, args.group)
            summaries.append(result)
            print(json.dumps({k: result.get(k) for k in ("mode", "status", "offered", "admitted", "rejected", "acknowledged", "final_lag", "error")}), flush=True)
            if result["status"] in {"failed", "interrupted", "drain_timeout", "invalid_concurrent_traffic"}:
                break
    write_json(output / "runs.json", summaries)
    return int(any(r["status"] != "completed" for r in summaries))


if __name__ == "__main__":
    raise SystemExit(main())
