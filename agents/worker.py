"""Kafka-only AI worker: durable result cache, shared validation, explicit commits.

There is intentionally no mock provider or public extraction endpoint. Offline
tests inject an ExtractionService; the runtime uses the configured external API.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import logging
import os
import signal
import threading
import time

from confluent_kafka import Consumer, Producer
from prometheus_client import Counter, Gauge

from agents.extraction import ExtractionService, FIELD_TO_RAW, source_text
from agents.provider_audit import capture, emit
from agents.safety import is_synthetic_record, SYNTHETIC_URL_PREFIX
from processing.kafka_to_mongo import (
    KafkaToMongoPipeline, agent_event_id, validate_normalized_record,
    normalize_listing, extraction_required_errors,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerConfig:
    bootstrap: str = "localhost:9092,localhost:9093,localhost:9094"
    topic: str = "real_estate_ai_input"
    result_topic: str = "real_estate_ai_results"
    dlq_topic: str = "real_estate_ai_dlq"
    group: str = "real_estate_ai_extraction"
    mongo_uri: str = "mongodb://localhost:27017/"
    real_db: str = "real_estate_db"
    stress_db: str = "real_estate_stress_db"
    stress_enabled: bool = False
    delivery_timeout: float = 30
    metrics_port: int = 8006

    @classmethod
    def from_env(cls):
        config = cls(
            bootstrap=os.getenv("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap),
            topic=os.getenv("KAFKA_AI_TOPIC", cls.topic),
            result_topic=os.getenv("KAFKA_AI_RESULT_TOPIC", cls.result_topic),
            dlq_topic=os.getenv("KAFKA_AI_DLQ_TOPIC", cls.dlq_topic),
            group=os.getenv("KAFKA_AI_GROUP_ID", cls.group),
            mongo_uri=os.getenv("MONGO_URI", cls.mongo_uri),
            real_db=os.getenv("MONGO_DB", cls.real_db),
            stress_db=os.getenv("MONGO_STRESS_DB", cls.stress_db),
            stress_enabled=os.getenv("AI_STRESS_ENABLED", "false").lower() == "true",
            delivery_timeout=float(os.getenv("AI_KAFKA_DELIVERY_TIMEOUT_SECONDS", "30")),
            metrics_port=int(os.getenv("PROMETHEUS_METRICS_PORT", "8006")),
        )
        if config.real_db == config.stress_db:
            raise ValueError("AI worker real and stress databases must differ")
        if len({config.topic, config.result_topic, config.dlq_topic}) != 3 or not 1 <= config.delivery_timeout <= 60:
            raise ValueError("Invalid AI queue/delivery configuration")
        return config


class AIWorker:
    def __init__(self, config: WorkerConfig, extraction: ExtractionService):
        self.config, self.extraction = config, extraction
        self.stopped = threading.Event()
        self.healthy = True
        self.consumer = None
        self.producer = None
        self.pipelines = {}
        registry = extraction.metrics.registry
        self.enabled_metric = Gauge("ai_agent_enabled", "Configured external extraction enabled", registry=registry)
        self.enabled_metric.set(extraction.enabled)
        self.lag = Gauge("ai_consumer_lag", "High watermark minus committed next offset", ["partition"], registry=registry)
        self.assigned = Gauge("ai_assigned_partitions", "Partitions assigned to this AI worker", registry=registry)
        self.completed = Counter("ai_records_completed", "Durably handled Kafka records", ["outcome"], registry=registry)
        self.dlq_count = Counter("ai_dlq", "Acknowledged terminal failure messages (may replay)", registry=registry)
        self.cached = Counter("ai_cached_results", "Reused durable results without another API call", registry=registry)

    def connect(self):
        cfg = self.config
        # Storage-only shared pipeline: no extra processor consumer group, and
        # no change to the trainer's MongoDB or artifact destination.
        for origin, database, clean_topic in (
            ("real", cfg.real_db, "real_estate_features"),
            ("stress", cfg.stress_db, "real_estate_stress_features"),
        ):
            self.pipelines[origin] = KafkaToMongoPipeline(
                cfg.bootstrap, cfg.topic, clean_topic, cfg.group, cfg.mongo_uri, database,
                consumer_enabled=False, allow_synthetic=origin == "stress", origin=origin,
            )
        self.producer = Producer({
            "bootstrap.servers": cfg.bootstrap, "enable.idempotence": True,
            "acks": "all", "delivery.timeout.ms": int(cfg.delivery_timeout * 1000),
        })
        self.consumer = Consumer({
            "bootstrap.servers": cfg.bootstrap, "group.id": cfg.group,
            "client.id": os.getenv("HOSTNAME", "ai-agent"),
            "enable.auto.commit": False, "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest", "max.poll.interval.ms": 900000,
        })
        self.consumer.subscribe([cfg.topic], on_assign=self.on_assign, on_revoke=self.on_revoke)

    def on_assign(self, consumer, partitions):
        self.assigned.set(len(partitions))
        log.info("ai_partitions_assigned partitions=%s", [p.partition for p in partitions])

    def on_revoke(self, consumer, partitions):
        self.assigned.set(0)
        for partition in partitions:
            self.lag.remove(str(partition.partition))
        log.info("ai_partitions_revoked partitions=%s", [p.partition for p in partitions])

    def publish_dlq(self, event_id, envelope, error_code):
        payload = {"schema_version": 1, "event_id": event_id, "error_code": error_code,
                   "failed_at": datetime.now(timezone.utc).isoformat(), "input": envelope}
        self.publish_acknowledged(self.config.dlq_topic, event_id, payload)
        self.dlq_count.inc()

    def publish_acknowledged(self, topic, key, payload):
        delivered = []
        self.producer.produce(
            topic, key=key.encode(),
            value=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
            on_delivery=lambda error, message: delivered.append(error),
        )
        remaining = self.producer.flush(self.config.delivery_timeout)
        if remaining or not delivered or delivered[0] is not None:
            raise RuntimeError("AI output delivery not acknowledged; input offset remains uncommitted")

    def publish_result(self, envelope, result):
        self.publish_acknowledged(self.config.result_topic, envelope["record"]["url"], {
            "schema_version": 1, "origin": envelope["origin"],
            "event_id": envelope["event_id"], "record": envelope["record"], "result": result,
        })

    def response_cache_key(self, origin, record):
        # Ignore scrape/generation timestamps, not source facts. Keep URL and
        # provider/model boundaries; this is exact-content reuse, not fuzzy dedup.
        inputs = {"version": 1, "origin": origin, "url": record["url"],
                  "text": source_text(record),
                  "raw_fields": {key: record.get(key) for key in sorted(set(FIELD_TO_RAW.values()))},
                  "provider": self.extraction.provider.name, "model": self.extraction.provider.model,
                  "min_confidence": self.extraction.config.confidence_threshold}
        return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    @staticmethod
    def validate_envelope(envelope):
        if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
            raise ValueError("invalid_envelope")
        record = envelope.get("record")
        origin = envelope.get("origin")
        if origin not in ("real", "stress") or not isinstance(record, dict):
            raise ValueError("invalid_origin_or_record")
        if not isinstance(record.get("url"), str) or not record["url"]:
            raise ValueError("missing_url")
        synthetic = is_synthetic_record(record)
        if (origin == "real" and synthetic) or (origin == "stress" and (
            not synthetic or not record["url"].startswith(SYNTHETIC_URL_PREFIX)
        )):
            raise ValueError("synthetic_boundary_violation")
        if envelope.get("event_id") != agent_event_id(record):
            raise ValueError("event_id_mismatch")
        return origin, record, envelope["event_id"]

    def handle(self, envelope):
        origin, record, event_id = self.validate_envelope(envelope)
        pipeline = self.pipelines[origin]
        state = pipeline.db["ai_extractions"]
        previous = state.find_one({"_id": event_id})
        latest = pipeline.raw_collection.find_one({"url": record["url"]}, {"_agent_event_id": 1})
        if not latest or latest.get("_agent_event_id") != event_id:
            state.update_one({"_id": event_id}, {"$set": {
                "status": "completed", "outcome": "stale", "url": record["url"],
            }}, upsert=True)
            return "stale"

        if previous and previous.get("status") == "published":
            self.cached.inc()
            return previous["outcome"]

        if previous and "result" in previous:
            self.cached.inc()
            result = previous["result"]
        else:
            cache_key = self.response_cache_key(origin, record)
            response_cache = pipeline.db["ai_response_cache"]
            reusable = response_cache.find_one({"_id": cache_key})
            if origin == "stress" and not self.config.stress_enabled:
                result = {"status": "failed", "error_code": "stress_ai_disabled", "attempts": 0}
            elif reusable:
                self.cached.inc()
                result = dict(reusable["result"])
                result["record"] = {**record, **{
                    key: value for key, value in result["record"].items()
                    if key in FIELD_TO_RAW.values()
                }}
            else:
                with capture(pipeline.db, event_id, record):
                    result = self.extraction.extract(record)
                    emit("extraction_result", {"result": result})
                if result.get("status") == "success":
                    # Validate before Kafka delivery; result handler independently
                    # repeats validation before writing any training features.
                    normalized = normalize_listing(result["record"])
                    valid, errors = validate_normalized_record(normalized)
                    errors += extraction_required_errors(normalized)
                    if not valid or errors:
                        result = {**result, "status": "failed", "error_code": "post_extraction_validation"}
                    else:
                        response_cache.update_one({"_id": cache_key}, {"$set": {"result": result}}, upsert=True)
            # Persist before publishing. Redelivery reuses this output; a crash
            # between the external call and the first DB save can still repeat it.
            state.update_one({"_id": event_id}, {"$set": {
                "status": "extracted", "result": result, "url": record["url"],
                "origin": origin, "updated_at": datetime.now(timezone.utc).isoformat(),
            }}, upsert=True)

        # Check again after the slow external request; old answers should not
        # intentionally replace a newer scraped listing. This is not a
        # cross-collection transaction: latest-source races remain possible.
        latest = pipeline.raw_collection.find_one({"url": record["url"]}, {"_agent_event_id": 1})
        if not latest or latest.get("_agent_event_id") != event_id:
            outcome = "stale"
        else:
            self.publish_result(envelope, result)
            outcome = "success" if result.get("status") == "success" else "failed"
        state.update_one({"_id": event_id}, {"$set": {"status": "published", "outcome": outcome}})
        return outcome

    def handle_message(self, message):
        data = message.value() or b""
        try:
            envelope = json.loads(data.decode("utf-8"))
            self.validate_envelope(envelope)
        except (UnicodeError, ValueError, TypeError):
            event_id = hashlib.sha256(data).hexdigest()
            self.publish_dlq(event_id, {"raw_base64": base64.b64encode(data).decode()}, "malformed_message")
            outcome = "malformed"
        else:
            outcome = self.handle(envelope)
        self.consumer.commit(message=message, asynchronous=False)
        self.completed.labels(outcome).inc()
        log.info("ai_record_completed topic=%s partition=%s offset=%s outcome=%s",
                 message.topic(), message.partition(), message.offset(), outcome)
        return outcome

    def update_lag(self):
        assignments = self.consumer.assignment()
        if assignments:
            for committed in self.consumer.committed(assignments, timeout=5):
                low, high = self.consumer.get_watermark_offsets(committed, timeout=5)
                offset = committed.offset if committed.offset >= 0 else low
                self.lag.labels(str(committed.partition)).set(max(0, high - offset))

    def run(self):
        if not self.extraction.enabled:
            log.info("ai_agent_disabled no Kafka consumption and no external API calls")
            while not self.stopped.wait(1):
                pass
            return
        self.connect()
        last_lag = 0.0
        try:
            while not self.stopped.is_set():
                message = self.consumer.poll(1)
                if message is not None:
                    if message.error():
                        raise RuntimeError("AI Kafka consumer error")
                    self.handle_message(message)
                if time.monotonic() - last_lag > 15:
                    self.update_lag()
                    last_lag = time.monotonic()
        finally:
            self.healthy = False
            self.close()

    def close(self):
        if self.consumer is not None:
            self.consumer.close()
        if self.producer is not None:
            self.producer.flush(self.config.delivery_timeout)
        for pipeline in self.pipelines.values():
            pipeline.clean_producer.flush(self.config.delivery_timeout)
            pipeline.mongo_client.close()


def start_health_server(worker):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/metrics":
                body = worker.extraction.metrics.export()
                status, content_type = 200, "text/plain; version=0.0.4"
            elif self.path == "/health":
                body = json.dumps({"alive": worker.healthy, "enabled": worker.extraction.enabled}).encode()
                status, content_type = (200 if worker.healthy else 503), "application/json"
            else:
                body, status, content_type = b"not found", 404, "text/plain"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", worker.config.metrics_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = AIWorker(WorkerConfig.from_env(), ExtractionService.from_env())
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: worker.stopped.set())
    server = start_health_server(worker)
    try:
        worker.run()
    finally:
        worker.healthy = False
        server.shutdown()


if __name__ == "__main__":
    main()
