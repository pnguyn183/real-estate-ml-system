"""Opt-in, finite synthetic load runner. Disabled/finished services remain healthy."""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable, Mapping

from agents.generator import ListingGenerator, SCENARIOS, SEEDS
from agents.stress_metrics import StressMetrics

LOG = logging.getLogger(__name__)


def stress_topic(topic: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,249}", topic) or not (
        topic.startswith("stress_") or "_stress_" in topic or topic.endswith("_stress")
    ) or topic in {"real_estate_raw", "real_estate_features"}:
        raise ValueError("stress producer requires a dedicated stress-namespaced Kafka topic")
    return topic


@dataclass(frozen=True)
class StressConfig:
    enabled: bool = False
    scenario: str = "normal"
    load_profile: str = "normal"
    multiplier: float = 1
    rate: float = 1
    duration: float = 60
    max_records: int = 1000
    burst_rate: float = 5
    burst_duration: float = 10
    max_rate: float = 100
    duplicate_ratio: float = 0.2
    unstructured_ratio: float = 0.3
    seed: int = 42
    run_id: str = "default"
    topic: str = "real_estate_stress_raw"
    bootstrap: str = "kafka:29092,kafka2:29093,kafka3:29094"
    state_root: str = "runtime/stress"
    template_source: str = "builtin"
    template_limit: int = 100
    metrics_port: int = 8007

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "StressConfig":
        e = os.environ if env is None else env
        enabled_value = e.get("STRESS_ENABLED", "false").lower()
        if enabled_value not in {"true", "false"}:
            raise ValueError("STRESS_ENABLED must be explicitly true or false")
        config = cls(
            enabled=enabled_value == "true", scenario=e.get("STRESS_SCENARIO", "normal"),
            load_profile=e.get("STRESS_LOAD_PROFILE", "normal"), multiplier=float(e.get("STRESS_MULTIPLIER", "1")),
            rate=float(e.get("STRESS_RATE_PER_SECOND", "1")), duration=float(e.get("STRESS_DURATION_SECONDS", "60")),
            max_records=int(e.get("STRESS_MAX_RECORDS", "1000")), burst_rate=float(e.get("STRESS_BURST_RATE", "5")),
            burst_duration=float(e.get("STRESS_BURST_DURATION", "10")), max_rate=float(e.get("STRESS_MAX_RATE", "100")),
            duplicate_ratio=float(e.get("STRESS_DUPLICATE_RATIO", "0.2")), unstructured_ratio=float(e.get("STRESS_UNSTRUCTURED_RATIO", "0.3")),
            seed=int(e.get("STRESS_SEED", "42")), run_id=e.get("STRESS_RUN_ID", "default"),
            topic=e.get("KAFKA_STRESS_TOPIC", "real_estate_stress_raw"),
            bootstrap=e.get("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap), state_root=e.get("STRESS_STATE_DIR", "runtime/stress"),
            template_source=e.get("STRESS_TEMPLATE_SOURCE", "builtin"), template_limit=int(e.get("STRESS_TEMPLATE_LIMIT", "100")),
            metrics_port=int(e.get("PROMETHEUS_METRICS_PORT", "8007")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.scenario not in SCENARIOS or self.load_profile not in {"normal", "medium", "high"}:
            raise ValueError("unsupported stress scenario/load profile")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.run_id):
            raise ValueError("invalid STRESS_RUN_ID")
        stress_topic(self.topic)
        for value in (self.multiplier, self.rate, self.duration, self.burst_rate, self.burst_duration, self.max_rate):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("stress limits must be positive finite numbers")
        if self.duration > 3600 or not 1 <= self.max_records <= 1_000_000 or self.max_rate > 10000:
            raise ValueError("run must fit hard safety limits: 1 hour, 1M records, 10K messages/second")
        if self.effective_rate > self.max_rate or self.burst_rate * self.multiplier > self.max_rate:
            raise ValueError("requested message rate exceeds STRESS_MAX_RATE")
        if not 0 <= self.duplicate_ratio <= 1 or not 0 <= self.unstructured_ratio <= 1:
            raise ValueError("ratios must be in [0,1]")
        if self.template_source not in {"builtin", "mongo"} or not 1 <= self.template_limit <= 1000:
            raise ValueError("template source must be builtin/mongo and bounded to 1-1000 samples")
        if not 1 <= self.metrics_port <= 65535:
            raise ValueError("invalid metrics port")

    @property
    def effective_rate(self) -> float:
        return self.rate * self.multiplier * {"normal": 1, "medium": 5, "high": 10}[self.load_profile]


def load_templates(config: StressConfig) -> list[dict[str, Any]]:
    if config.template_source == "builtin":
        return list(SEEDS)
    # Only public structural attributes are sampled; never URLs, contacts or prose.
    from pymongo import MongoClient
    from agents.safety import real_data_query
    from processing.kafka_to_mongo import parse_number, parse_price_to_vnd
    fields = ("area_text", "price_text", "bedroom_text", "bathroom_text", "property_type", "province_slug", "district_slug")
    projection = {field: 1 for field in fields}
    projection["_id"] = 0
    templates = []
    with MongoClient(os.environ.get("MONGO_URI", "mongodb://mongodb:27017/"), serverSelectionTimeoutMS=5000) as client:
        cursor = client[os.environ.get("MONGO_DB", "real_estate_db")]["listings_raw"].find(real_data_query(), projection).limit(config.template_limit).max_time_ms(5000)
        for item in cursor:
            area = parse_number(item.get("area_text"))
            price, _ = parse_price_to_vnd(item.get("price_text"), area)
            if not area or not price or not math.isfinite(area) or not math.isfinite(price) or not 1 <= area <= 10000 or not 1 <= price <= 500_000_000_000:
                continue
            if item.get("property_type") not in {"apartment", "house", "land", "villa_townhouse", "shophouse", "warehouse"}:
                continue
            if any(not re.fullmatch(r"[a-z0-9-]{1,80}", str(item.get(key) or "")) for key in ("province_slug", "district_slug")):
                continue
            rooms = []
            for key in ("bedroom_text", "bathroom_text"):
                value = parse_number(item.get(key))
                rooms.append(int(value) if value is not None and math.isfinite(value) and 0 <= value <= 100 else 0)
            templates.append({"area_m2": area, "price_vnd": price, "bedroom_count": rooms[0], "bathroom_count": rooms[1],
                              **{key: item[key] for key in ("property_type", "province_slug", "district_slug")}})
    if not templates:
        raise ValueError("Mongo snapshot contains no usable structural templates; use builtin explicitly")
    return templates


def run_once(config: StressConfig, metrics: StressMetrics, stop: Event,
             producer_factory: Callable[..., Any] | None = None) -> dict[str, Any]:
    config.validate()
    if not config.enabled:
        return {"status": "disabled", "generated": 0, "delivered": 0}
    run_dir = Path(config.state_root) / config.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    marker = run_dir / "run-state.json"
    try:
        with marker.open("x", encoding="utf-8") as output:
            json.dump({"status": "claimed", "run_id": config.run_id, "started_at": time.time()}, output)
    except FileExistsError:
        return {"status": "already_claimed", "run_id": config.run_id, "generated": 0, "delivered": 0}

    report: dict[str, Any] = {"run_id": config.run_id, "scenario": config.scenario, "status": "running", "generated": 0,
                              "delivered": 0, "failed": 0, "undelivered": 0, "partitions": {}, "duplicates": 0, "unstructured": 0}
    # Secrets, bootstrap addresses and Mongo URI are not written to the manifest.
    manifest = {key: value for key, value in asdict(config).items() if key not in {"bootstrap", "state_root"}}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    started, producer = time.monotonic(), None
    metrics.active.set(1)

    def delivered(error, message):
        if error:
            report["failed"] += 1
            metrics.failed.labels("delivery").inc()
            LOG.error("stress_delivery_failed run=%s error_class=%s", config.run_id, type(error).__name__)
        else:
            report["delivered"] += 1
            partition = str(message.partition())
            report["partitions"][partition] = report["partitions"].get(partition, 0) + 1
            metrics.delivered.labels(config.scenario).inc()

    try:
        if producer_factory is None:
            from confluent_kafka import Producer
            producer_factory = Producer
        producer = producer_factory({"bootstrap.servers": config.bootstrap, "client.id": "real-estate-stress-agent",
                                     "enable.idempotence": True, "acks": "all", "linger.ms": 20,
                                     "delivery.timeout.ms": 10000, "request.timeout.ms": 5000,
                                     "queue.buffering.max.messages": min(config.max_records, 10000)})
        metadata = producer.list_topics(topic=config.topic, timeout=5)
        topic = metadata.topics.get(config.topic)
        if topic is None or topic.error is not None or not topic.partitions:
            raise RuntimeError("dedicated stress topic must be provisioned before producing")
        generator = ListingGenerator(config.run_id, config.scenario, config.seed, config.duplicate_ratio,
                                     config.unstructured_ratio, load_templates(config))
        started = time.monotonic()
        next_due = started
        with (run_dir / "ground_truth.jsonl").open("x", encoding="utf-8") as truth:
            for index in range(config.max_records):
                now = time.monotonic()
                if stop.is_set() or now - started >= config.duration:
                    break
                if now < next_due and stop.wait(min(next_due - now, max(config.duration - (now - started), 0))):
                    break
                now = time.monotonic()
                if now - started >= config.duration:
                    break
                burst = config.scenario == "burst" and now - started < config.burst_duration
                rate = config.burst_rate * config.multiplier if burst else config.effective_rate
                metrics.burst.set(int(burst))
                generated = generator.next(index)
                truth.write(json.dumps(generated.ground_truth, ensure_ascii=False) + "\n")
                truth.flush()
                report["generated"] += 1
                metrics.generated.labels(config.scenario).inc()
                if generated.duplicate:
                    metrics.duplicates.inc()
                    report["duplicates"] += 1
                if generated.unstructured:
                    metrics.unstructured.inc()
                    report["unstructured"] += 1
                try:
                    producer.produce(config.topic, key=generated.payload["url"],
                                     value=json.dumps(generated.payload, ensure_ascii=False).encode("utf-8"), callback=delivered)
                except BufferError:
                    report["failed"] += 1
                    metrics.failed.labels("backpressure").inc()
                    report["status"] = "backpressure_stopped"
                    break
                except Exception:
                    report["failed"] += 1
                    metrics.failed.labels("enqueue").inc()
                    raise
                producer.poll(0)
                if report["failed"]:
                    report["status"] = "delivery_failed"
                    break
                elapsed = max(time.monotonic() - started, 1e-6)
                metrics.duration.set(elapsed)
                metrics.rate.set(report["delivered"] / elapsed)
                next_due = max(next_due + 1 / rate, time.monotonic())
        if report["status"] == "running":
            report["status"] = "interrupted" if stop.is_set() else "completed"
    except Exception as exc:
        report["status"], report["error_class"] = "failed", type(exc).__name__
        LOG.error("stress_run_failed run=%s error_class=%s", config.run_id, type(exc).__name__)
    finally:
        if producer is not None:
            try:
                report["undelivered"] = producer.flush(12)
            except Exception as exc:
                report["status"], report["error_class"] = "failed", type(exc).__name__
        if report["failed"] or report["undelivered"]:
            report["status"] = "failed"
        report["duration_seconds"] = max(time.monotonic() - started, 1e-6)
        report["delivered_per_second"] = report["delivered"] / report["duration_seconds"]
        metrics.duration.set(report["duration_seconds"])
        metrics.rate.set(report["delivered_per_second"])
        metrics.active.set(0)
        metrics.burst.set(0)
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        marker.write_text(json.dumps({"status": report["status"], "run_id": config.run_id}), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Exit after one run/disabled check; does not implicitly enable load")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config = StressConfig.from_env()
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_args: stop.set())
    metrics = StressMetrics()
    server = metrics.serve(config.metrics_port)
    try:
        report = run_once(config, metrics, stop)
        LOG.info("stress_run_result %s", json.dumps(report))
        if not args.once:
            while not stop.wait(1):
                pass
        return 1 if report["status"] in {"failed", "backpressure_stopped", "delivery_failed"} else 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
