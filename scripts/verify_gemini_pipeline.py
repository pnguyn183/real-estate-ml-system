"""One real Gemini call through the isolated Kafka/AI/Mongo pipeline.

--prepare only creates a non-personal synthetic input. --run publishes that
input through the normal stress-topic fallback; it never injects a response,
changes routing, resets offsets, or reads the provider key. Operators must opt
the processors and AI worker into AI_STRESS_ENABLED and enable event-scoped
provider auditing before running. A failed run writes partial evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from confluent_kafka import Consumer, Producer, TopicPartition
from pymongo import MongoClient

from agents.safety import SYNTHETIC_URL_PREFIX, is_synthetic_record
from processing.kafka_to_mongo import (
    agent_event_id, extraction_routing_errors, normalize_listing,
    validate_normalized_record,
)


MODEL = "gemini-2.5-flash"


class VerificationBlocked(RuntimeError):
    """A bounded code suitable for an artifact, never an exception body."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_model(model: object) -> str:
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", model):
        raise VerificationBlocked("expected_model_invalid")
    if any(part in {"", ".", ".."} for part in model.split("/")):
        raise VerificationBlocked("expected_model_invalid")
    return model


def build_case(model: str = MODEL) -> dict:
    model = validate_model(model)
    run_id = "gemini-live-" + uuid4().hex
    record = {
        "url": f"{SYNTHETIC_URL_PREFIX}{run_id}/explicit-facts",
        "title": "Synthetic Gemini pipeline verification",
        "description": "Căn hộ bán, diện tích 80 m2, tổng giá 8 tỷ đồng, 2 phòng ngủ và 2 phòng tắm.",
        "property_type": "apartment", "listing_type": "sell",
        "province_slug": "ho-chi-minh", "district_slug": "quan-1",
        "source_type": "synthetic", "is_synthetic": True,
        "generated_by": "gemini_pipeline_verification", "scenario": "gemini_live_audit",
        "run_id": run_id, "generated_at": now(), "verified": 0,
    }
    return {
        "schema_version": 1, "run_id": run_id, "prepared_at": now(),
        "event_id": agent_event_id(record), "record": record,
        "input_kind": "non-personal synthetic fixture; provider response must be real",
        "expected_model": model,
        "routing_errors_before": extraction_routing_errors(normalize_listing(record)),
    }


def validate_case(case: dict) -> dict:
    record = case.get("record")
    if not isinstance(record, dict) or not is_synthetic_record(record):
        raise VerificationBlocked("test_record_must_be_synthetic")
    if not str(record.get("url", "")).startswith(SYNTHETIC_URL_PREFIX):
        raise VerificationBlocked("test_url_must_use_synthetic_domain")
    if record.get("generated_by") != "gemini_pipeline_verification":
        raise VerificationBlocked("not_a_prepared_gemini_fixture")
    if case.get("event_id") != agent_event_id(record) or case.get("run_id") != record.get("run_id"):
        raise VerificationBlocked("prepared_event_identity_mismatch")
    normalized = normalize_listing(record)
    valid, errors = validate_normalized_record(normalized)
    if not valid or errors or not extraction_routing_errors(normalized):
        raise VerificationBlocked("fixture_does_not_meet_existing_fallback_conditions")
    validate_model(case.get("expected_model"))
    return record


def stages(audit: dict, stage: str) -> list[dict]:
    return [entry.get("payload", {}) for entry in audit.get("stages", [])
            if entry.get("stage") == stage]


def verify_evidence(report: dict) -> None:
    """Reject enabled/configured/cached/partial runs as proof of a live call."""
    result = (report.get("kafka_messages", {}).get("ai_result", {}).get("value", {}).get("result", {})
              or (report.get("extraction_state") or {}).get("result", {}))
    if result.get("status") in {"failed", "disabled"}:
        code = result.get("error_code", "unknown")
        code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,80}", code) else "unknown"
        raise VerificationBlocked("extraction_failed_" + code)
    expected_model = validate_model(report.get("expected_model"))
    audit = report.get("provider_audit") or {}
    requests = stages(audit, "request")
    responses = stages(audit, "response")
    parsed = stages(audit, "parsed")
    if not requests:
        raise VerificationBlocked("actual_provider_request_not_audited")
    if not all(request.get("request", {}).get("model") == expected_model for request in requests):
        raise VerificationBlocked("actual_request_model_mismatch")
    if not any(request.get("provider") == "openai_compatible"
               and request.get("base_url", "").rstrip("/") == "https://generativelanguage.googleapis.com/v1beta/openai"
               and request.get("endpoint") == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
               for request in requests):
        raise VerificationBlocked("actual_google_gemini_endpoint_not_proven")
    if not any(response.get("http_status") == 200 and response.get("raw_response") for response in responses):
        raise VerificationBlocked("actual_successful_provider_response_not_audited")
    if not parsed or not any(item.get("parsed_response") for item in parsed):
        raise VerificationBlocked("validated_parsed_response_not_audited")
    if set(report.get("kafka_messages", {})) != {"raw_input", "ai_input", "ai_result"}:
        raise VerificationBlocked("incomplete_kafka_path_evidence")
    envelope = report["kafka_messages"]["ai_result"]["value"]
    result = envelope.get("result", {})
    if result.get("status") != "success":
        raise VerificationBlocked("provider_or_extraction_failed")
    if result.get("model") != expected_model:
        raise VerificationBlocked("actual_result_model_mismatch")
    if result.get("attempts", 0) < 1:
        raise VerificationBlocked("no_fresh_successful_gemini_extraction")
    receipt = report.get("receipt") or {}
    if receipt.get("status") != "completed" or receipt.get("outcome") != "success":
        raise VerificationBlocked("final_processor_validation_not_successful")
    final = report.get("final_record") or {}
    if not final.get("is_synthetic") or final.get("is_model_candidate") is not False:
        raise VerificationBlocked("synthetic_training_exclusion_not_proven")
    if final.get("ai_status") != "success" or final.get("ai_event_id") != report.get("event_id"):
        raise VerificationBlocked("final_record_identity_or_status_mismatch")
    if not report.get("cleaning_audit") or not report.get("raw_record_preserved"):
        raise VerificationBlocked("before_after_archive_not_proven")
    counts = report.get("primary_matching_documents")
    if not counts or any(count != 0 for count in counts.values()):
        raise VerificationBlocked("primary_data_isolation_not_proven")


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def capture_document(db, collection: str, query: dict) -> dict | None:
    return db[collection].find_one(query, {"_id": 0})


def run_case(case: dict, output: Path, timeout_seconds: float) -> dict:
    record = validate_case(case)
    event_id = case["event_id"]
    report = {
        "schema_version": 1, "status": "running", "started_at": now(),
        "run_id": case["run_id"], "event_id": event_id,
        "expected_model": case["expected_model"],
        "input_kind": case["input_kind"], "before": record,
        "deterministic_before": normalize_listing(record),
        "routing_errors_before": extraction_routing_errors(normalize_listing(record)),
        "kafka_messages": {},
    }
    consumer = producer = mongo = None
    try:
        real_db = os.getenv("MONGO_DB", "real_estate_db")
        stress_db = os.getenv("MONGO_STRESS_DB", "real_estate_stress_db")
        if real_db == stress_db:
            raise VerificationBlocked("stress_and_primary_databases_must_differ")
        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092,localhost:9093,localhost:9094")
        topics = {
            "raw_input": os.getenv("KAFKA_STRESS_TOPIC", "real_estate_stress_raw"),
            "ai_input": os.getenv("KAFKA_AI_TOPIC", "real_estate_ai_input"),
            "ai_result": os.getenv("KAFKA_AI_RESULT_TOPIC", "real_estate_ai_results"),
        }
        if len(set(topics.values())) != 3 or topics["raw_input"] == os.getenv("KAFKA_RAW_TOPIC", "real_estate_raw"):
            raise VerificationBlocked("invalid_isolated_topic_configuration")
        report["topics"] = topics
        report["storage"] = {"stress_db": stress_db, "primary_db": real_db}
        mongo = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=5000)
        db = mongo[stress_db]
        if db.listings_raw.find_one({"url": record["url"]}, {"_id": 1}):
            raise VerificationBlocked("case_already_published_prepare_a_new_case")
        consumer = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": "gemini-audit-observer-" + uuid4().hex,
            "enable.auto.commit": False, "enable.auto.offset.store": False,
            "auto.offset.reset": "error", "allow.auto.create.topics": False,
        })
        metadata = consumer.list_topics(timeout=10)
        assignments, initial_offsets = [], []
        for topic in topics.values():
            item = metadata.topics.get(topic)
            if item is None or item.error or not item.partitions:
                raise VerificationBlocked("required_topic_unavailable")
            for partition in sorted(item.partitions):
                _, high = consumer.get_watermark_offsets(TopicPartition(topic, partition), timeout=5)
                assignments.append(TopicPartition(topic, partition, high))
                initial_offsets.append({"topic": topic, "partition": partition, "next_offset": high})
        # Manual assignment never joins/commits the production consumer groups.
        consumer.assign(assignments)
        report["observer_initial_offsets"] = initial_offsets
        producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True,
                             "acks": "all", "delivery.timeout.ms": 15000})
        delivery = []

        def acknowledged(error, message):
            delivery.append({"success": error is None, "topic": message.topic(),
                             "partition": message.partition(), "offset": message.offset()})

        producer.produce(topics["raw_input"], key=record["url"],
                         value=json.dumps(record, ensure_ascii=False).encode("utf-8"), callback=acknowledged)
        if producer.flush(20) or not delivery or not delivery[0]["success"]:
            raise VerificationBlocked("raw_kafka_delivery_not_acknowledged")
        report["producer_ack"] = delivery[0]
        report["published_at"] = now()
        write_report(output, report)
        deadline = time.monotonic() + timeout_seconds
        lookup = {"_id": event_id}
        url_query = {"url": record["url"]}
        while time.monotonic() < deadline:
            message = consumer.poll(.25)
            if message is not None and not message.error():
                try:
                    value = json.loads(message.value())
                except (TypeError, ValueError, UnicodeError):
                    value = None
                if isinstance(value, dict) and (value.get("event_id") == event_id or value == record):
                    label = next(key for key, topic in topics.items() if topic == message.topic())
                    report["kafka_messages"][label] = {
                        "topic": message.topic(), "partition": message.partition(), "offset": message.offset(),
                        "captured_at": now(), "value": value,
                    }
            report["provider_audit"] = capture_document(db, "ai_provider_audit", lookup)
            report["cleaning_audit"] = capture_document(db, "ai_cleaning_audit", lookup)
            report["receipt"] = capture_document(db, "ai_result_receipts", lookup)
            report["extraction_state"] = capture_document(db, "ai_extractions", lookup)
            report["raw_archive"] = capture_document(db, "listings_raw", url_query)
            report["final_record"] = capture_document(db, "training_features", url_query)
            report["invalid_record"] = capture_document(db, "invalid_records", url_query)
            report["final_storage_collection"] = "training_features" if report["final_record"] else None
            if not report["final_record"] and report["invalid_record"]:
                report["final_record"] = report["invalid_record"]
                report["final_storage_collection"] = "invalid_records"
            receipt = report["receipt"] or {}
            if receipt.get("status") == "completed" and len(report["kafka_messages"]) == 3:
                break
        report["primary_matching_documents"] = {
            collection: mongo[real_db][collection].count_documents({"$or": [
                url_query, {"event_id": event_id}, {"_id": event_id},
            ]})
            for collection in ("listings_raw", "training_features", "invalid_records", "ai_cleaning_audit", "ai_provider_audit")
        }
        archived = report.get("raw_archive") or {}
        report["raw_record_preserved"] = {
            key: value for key, value in archived.items() if key != "_agent_event_id"
        } == record
        wire_audit = report.get("provider_audit") or {}
        report["gemini_input"] = stages(wire_audit, "request")
        report["gemini_output"] = stages(wire_audit, "response")
        report["parsed_response"] = stages(wire_audit, "parsed")
        result = (report.get("extraction_state") or {}).get("result", {})
        report["extraction_error_code"] = result.get("error_code")
        merged = result.get("record") or {}
        report["after_extraction"] = merged or None
        report["field_changes"] = {
            key: {"before": record.get(key), "after": merged.get(key)}
            for key in sorted(set(record) | set(merged)) if record.get(key) != merged.get(key)
        } if merged else {}
        verify_evidence(report)
        report["status"] = "verified"
    except Exception as exc:
        report["status"] = "blocked"
        report["blocker"] = str(exc) if isinstance(exc, VerificationBlocked) else type(exc).__name__
    finally:
        report["finished_at"] = now()
        write_report(output, report)
        if consumer is not None:
            consumer.close()
        if mongo is not None:
            mongo.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--case", type=Path, default=Path("runtime/research/gemini-case.json"))
    parser.add_argument("--output", type=Path, default=Path("runtime/research/gemini-clean-audit.json"))
    parser.add_argument("--model", help=f"Expected model from provider listing (--prepare only; default: {MODEL})")
    parser.add_argument("--timeout-seconds", type=float, default=180)
    args = parser.parse_args()
    if not math.isfinite(args.timeout_seconds) or not 1 <= args.timeout_seconds <= 600:
        parser.error("timeout-seconds must be between 1 and 600")
    if args.run and args.model is not None:
        parser.error("--model is only accepted with --prepare; --run uses the prepared case")
    if args.prepare:
        if args.case.exists():
            parser.error("case already exists; choose a new case path")
        try:
            case = build_case(MODEL if args.model is None else args.model)
        except VerificationBlocked as exc:
            parser.error(str(exc))
        validate_case(case)
        write_report(args.case, case)
        print(json.dumps({"status": "prepared", "event_id": case["event_id"], "case": str(args.case)}))
        return 0
    case = json.loads(args.case.read_text(encoding="utf-8"))
    report = run_case(case, args.output, args.timeout_seconds)
    print(json.dumps({key: report.get(key) for key in ("status", "event_id", "blocker", "extraction_error_code")}, ensure_ascii=False))
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
