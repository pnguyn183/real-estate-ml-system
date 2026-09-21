"""Bounded Kafka/Mongo integration smoke; simulated LLM transport, never a live API.

Run only on an idle local AI queue, with all three processors running and their
AI_STRESS_ENABLED gate enabled. Leave the regular AI worker disabled. Every test
record is isolated under a unique synthetic.invalid run; no live database writes
or offset resets are performed. Test documents remain available for inspection.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, ConfigResource, ResourceType
from pymongo import MongoClient

from agents.extraction import ExtractionConfig, ExtractionService
from agents.providers import ProviderError
from agents.worker import AIWorker, WorkerConfig
from processing.kafka_to_mongo import agent_event_id


class ScriptedSmokeProvider:
    """Test-only transport injected into the real extraction/validation service."""

    name = "integration_test_transport"
    model = "deterministic_test_fixture"
    enabled = True

    def __init__(self):
        self.calls = {"success": 0, "invalid_json": 0, "timeout": 0}

    def extract(self, messages, timeout):
        text = messages[-1]["content"]
        if "SMOKE_TIMEOUT" in text:
            self.calls["timeout"] += 1
            raise ProviderError("provider_timeout", retryable=True)
        if "SMOKE_INVALID_JSON" in text:
            self.calls["invalid_json"] += 1
            return "this is deliberately not JSON"
        self.calls["success"] += 1
        return json.dumps({
            "fields": {"price_vnd": 8_000_000_000, "area_m2": 80,
                       "bedroom_count": 2, "property_type": "apartment"},
            "confidence": .95,
            "evidence": {"price_vnd": "8 tỷ", "area_m2": "80m2",
                         "bedroom_count": "2 phòng ngủ", "property_type": "Căn hộ"},
        }, ensure_ascii=False)


def fixtures(run_id):
    """Choose URL keys whose default librdkafka CRC32 partitioner spans 3 lanes."""
    common = {
        "source_type": "synthetic", "is_synthetic": True, "generated_by": "stress_agent",
        "run_id": run_id, "scenario": "integration_smoke",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "title": "Synthetic integration smoke", "listing_type": "Ban", "verified": 0,
        "province_slug": "ho-chi-minh", "district_slug": "quan-1",
    }
    normal = []
    for index in range(12):
        suffix = 0
        while True:
            url = f"https://synthetic.invalid/{run_id}/structured-{index}-{suffix}"
            if zlib.crc32(url.encode()) % 3 == index % 3:
                break
            suffix += 1
        normal.append({**common, "url": url, "property_type": "apartment",
                       "price_text": "8 tỷ", "area_text": "80m2", "bedroom_text": "2",
                       "bathroom_text": "2", "description": "Synthetic structured record"})
    difficult = [
        {**common, "url": f"https://synthetic.invalid/{run_id}/ai-{kind}",
         "format": "semi_structured" if kind == "success" else "unstructured",
         "description": f"Căn hộ 80m2, 2 phòng ngủ, tổng giá 8 tỷ. {marker}"}
        for kind, marker in (("success", "SMOKE_SUCCESS"), ("invalid", "SMOKE_INVALID_JSON"),
                             ("timeout", "SMOKE_TIMEOUT"))
    ]
    difficult[0]["gia"] = "8 tỷ"
    difficult[0]["dien_tich"] = "80m2"
    return normal, difficult


def group_description(admin, group):
    try:
        return admin.describe_consumer_groups([group], request_timeout=5)[group].result(timeout=5)
    except KafkaException as error:
        if error.args[0].code() == KafkaError.GROUP_ID_NOT_FOUND:
            return None
        raise


def verify_topology(admin, probe, cfg, stress_topic, results_topic):
    metadata = admin.list_topics(timeout=5)
    if not {1, 2, 3}.issubset(metadata.brokers):
        raise RuntimeError("Expected the existing three brokers, IDs 1/2/3")
    names = (stress_topic, cfg.topic, results_topic, cfg.dlq_topic)
    for name in names:
        topic = metadata.topics.get(name)
        if topic is None or topic.error or len(topic.partitions) != 3:
            raise RuntimeError(f"Topic must already exist with three partitions: {name}")
        if any(len(p.replicas) != 3 or len(p.isrs) != 3 for p in topic.partitions.values()):
            raise RuntimeError(f"All three replicas must be in sync before the smoke test: {name}")
    resources = [ConfigResource(ResourceType.TOPIC, name) for name in names]
    for resource, future in admin.describe_configs(resources, request_timeout=5).items():
        if future.result(timeout=5)["min.insync.replicas"].value != "2":
            raise RuntimeError(f"Expected min.insync.replicas=2: {resource.name}")

    existing = group_description(admin, cfg.group)
    if existing and existing.members:
        raise RuntimeError("Refusing to compete with another active AI consumer; disable it first")
    offsets = probe.committed([TopicPartition(cfg.topic, i) for i in range(3)], timeout=5)
    for partition in offsets:
        low, high = probe.get_watermark_offsets(partition, timeout=5)
        position = partition.offset if partition.offset >= 0 else low
        if high > position:
            raise RuntimeError("Refusing pre-existing AI request backlog; inspect/recover it first")

    processors = group_description(admin, os.getenv("KAFKA_GROUP_ID", "real_estate_training_pipeline"))
    if not processors or len(processors.members) != 3:
        raise RuntimeError("Expected all three existing Processor workers in their shared group")
    assigned = {p.topic for member in processors.members for p in member.assignment.topic_partitions}
    if stress_topic not in assigned or results_topic not in assigned:
        raise RuntimeError("Processor group must already subscribe to stress input and AI results")
    return {"broker_ids": sorted(metadata.brokers), "topics": list(names),
            "partitions": 3, "replication_factor": 3, "minimum_isr": 2,
            "processor_members": len(processors.members)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=90, help="Overall polling budget, 20-90 seconds")
    args = parser.parse_args()
    if not 20 <= args.timeout <= 90:
        parser.error("--timeout must be between 20 and 90 seconds")
    if os.getenv("AI_ENABLED", "false").lower() not in {"false", "0", "no"}:
        parser.error("Run with AI_ENABLED=false: this smoke never enables the external API")

    started = time.monotonic()
    deadline = started + args.timeout
    run_id = "smoke-" + uuid4().hex[:16]
    cfg = replace(WorkerConfig.from_env(), stress_enabled=True, delivery_timeout=5)
    stress_topic = os.getenv("KAFKA_STRESS_TOPIC", "real_estate_stress_raw")
    results_topic = cfg.result_topic
    if cfg.real_db == cfg.stress_db or "stress" not in stress_topic:
        raise RuntimeError("Live and stress storage/input must remain isolated")
    normal, difficult = fixtures(run_id)
    provider = ScriptedSmokeProvider()
    extraction = ExtractionService(provider, ExtractionConfig(
        timeout_seconds=1, max_retries=1, rate_limit_per_minute=20,
        max_concurrent=1, total_budget_seconds=5,
    ))
    worker = AIWorker(cfg, extraction)
    admin = AdminClient({"bootstrap.servers": cfg.bootstrap})
    probe = Consumer({"bootstrap.servers": cfg.bootstrap, "group.id": cfg.group,
                      "enable.auto.commit": False, "enable.auto.offset.store": False})
    producer = Producer({"bootstrap.servers": cfg.bootstrap, "enable.idempotence": True,
                         "acks": "all", "delivery.timeout.ms": 5000})
    mongo = MongoClient(cfg.mongo_uri, serverSelectionTimeoutMS=5000,
                        connectTimeoutMS=3000, socketTimeoutMS=3000)
    report = {"run_id": run_id, "transport": "simulated provider; no external LLM calls",
              "status": "failed", "published_partitions": {}, "request_messages_handled": 0}
    publish_errors = []

    def delivered(error, message):
        if error:
            publish_errors.append(str(error))
            return
        key = str(message.partition())
        report["published_partitions"][key] = report["published_partitions"].get(key, 0) + 1

    def publish(records):
        for record in records:
            producer.produce(stress_topic, key=record["url"].encode(),
                             value=json.dumps(record, ensure_ascii=False).encode(), on_delivery=delivered)
        if producer.flush(5) or publish_errors:
            raise RuntimeError("Smoke input publication was not acknowledged")

    try:
        report["topology"] = verify_topology(admin, probe, cfg, stress_topic, results_topic)
        mongo.admin.command("ping")
        probe.close()
        probe = None
        worker.connect()
        publish(normal + [normal[0]] + difficult)
        duplicate_sent = False
        event_ids = [agent_event_id(record) for record in difficult]
        database = mongo[cfg.stress_db]
        query = {"run_id": run_id}
        receipts = database["ai_result_receipts"]
        while time.monotonic() < deadline:
            message = worker.consumer.poll(.2)
            if message is not None:
                if message.error():
                    raise RuntimeError("AI smoke consumer encountered Kafka error")
                envelope = json.loads(message.value().decode())
                if envelope.get("record", {}).get("run_id") != run_id:
                    raise RuntimeError("Unexpected AI input: refusing to process or commit another run's record")
                worker.handle_message(message)
                report["request_messages_handled"] += 1
            completed = list(receipts.find({"_id": {"$in": event_ids}, "status": "completed"}))
            if len(completed) == 3 and not duplicate_sent:
                publish([difficult[0]])
                duplicate_sent = True
            if not duplicate_sent or report["request_messages_handled"] < 4:
                continue
            features = list(database["training_features"].find(query))
            invalid = list(database["invalid_records"].find(query))
            if len(features) != 13 or len(invalid) != 2:
                continue
            if any(not row.get("is_synthetic") or row.get("is_model_candidate") for row in features):
                raise AssertionError("Synthetic features must remain explicitly excluded from training")
            if any(mongo[cfg.real_db][name].count_documents(query)
                   for name in ("listings_raw", "training_features", "invalid_records")):
                raise AssertionError("Synthetic smoke records reached the primary database")
            if database["listings_raw"].count_documents(query) != 15:
                raise AssertionError("Exact duplicate created an additional raw record")
            if database["ai_failures"].count_documents({"_id": {"$in": event_ids}}) != 2:
                raise AssertionError("Failed extractions must remain recoverable as AI failure records")
            if provider.calls != {"success": 1, "invalid_json": 1, "timeout": 2}:
                raise AssertionError(f"Unexpected transport calls/cache/retry behavior: {provider.calls}")
            if set(report["published_partitions"]) != {"0", "1", "2"}:
                raise AssertionError("Input keys did not exercise every stress-topic partition")
            report.update(status="passed", synthetic_features=13, invalid_records=2,
                          raw_records=15, primary_database_writes=0, provider_calls=provider.calls,
                          result_outcomes=sorted(row["outcome"] for row in completed),
                          exact_duplicate_upsert="passed", cached_difficult_input="passed",
                          timeout_retry="passed", invalid_json_recovery="passed")
            break
        else:
            raise TimeoutError("Pipeline smoke did not finish within its budget; check Processor AI_STRESS_ENABLED and logs")
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        worker.close()
        if probe is not None:
            probe.close()
        producer.flush(5)
        mongo.close()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
