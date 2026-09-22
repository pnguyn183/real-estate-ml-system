"""Apply acknowledged AI results through the existing deterministic pipeline.

The result topic is an at-least-once hand-off, not a MongoDB/Kafka transaction.
URL upserts and durable receipts make completed redeliveries idempotent. Source
version checks reject obsolete answers, but cannot provide a cross-collection
transaction when a newer listing is ingested concurrently.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math

from agents.extraction import FIELD_TO_RAW, known_raw_fields
from agents.safety import SYNTHETIC_URL_PREFIX, is_synthetic_record


PROVENANCE_FIELDS = (
    "source_type", "is_synthetic", "generated_by", "scenario", "run_id",
    "original_record_id", "generated_at",
)

AUDIT_FIELDS = tuple(dict.fromkeys((*FIELD_TO_RAW.values(), *PROVENANCE_FIELDS, "title", "description")))


def _audit_snapshot(record: dict) -> dict:
    return {field: record[field] for field in AUDIT_FIELDS if field in record}


def _snapshot_hash(snapshot: dict) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_cleaning_audit(pipeline, original: dict, result: dict, event_id: str, origin: str, cleaned: dict | None = None) -> None:
    """Persist before/after evidence without replacing the immutable raw record."""
    before = _audit_snapshot(original)
    after = _audit_snapshot(cleaned if cleaned is not None else result.get("record") or {})
    changed_fields = sorted(field for field in set(before) | set(after) if before.get(field) != after.get(field))
    audit = {
        "_id": event_id,
        "event_id": event_id,
        "url": original.get("url"),
        "origin": origin,
        "status": result.get("status"),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "before": before,
        "after": after,
        "changed_fields": changed_fields,
        "before_sha256": _snapshot_hash(before),
        "after_sha256": _snapshot_hash(after),
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }
    pipeline.db["ai_cleaning_audit"].update_one({"_id": event_id}, {"$set": audit}, upsert=True)


def validate_result_envelope(envelope):
    # Lazy import: the processor calls this module from its result-topic branch.
    from processing.kafka_to_mongo import agent_event_id

    if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
        raise ValueError("invalid_result_envelope")
    origin, record = envelope.get("origin"), envelope.get("record")
    if origin not in {"real", "stress"} or not isinstance(record, dict):
        raise ValueError("invalid_result_origin_or_record")
    if not isinstance(record.get("url"), str) or not record["url"]:
        raise ValueError("missing_result_url")
    synthetic = is_synthetic_record(record)
    if (origin == "real" and synthetic) or (origin == "stress" and (
        not synthetic or not record["url"].startswith(SYNTHETIC_URL_PREFIX)
    )):
        raise ValueError("result_synthetic_boundary_violation")
    if envelope.get("event_id") != agent_event_id(record):
        raise ValueError("result_event_id_mismatch")
    result = envelope.get("result")
    if not isinstance(result, dict) or result.get("status") not in {"success", "failed", "disabled"}:
        raise ValueError("invalid_result_status")
    if result["status"] == "success" and not isinstance(result.get("record"), dict):
        raise ValueError("missing_extracted_record")
    return origin, record, envelope["event_id"], result


def result_payload(original, result, event_id):
    """Only extraction fields may change; identity and provenance stay original."""
    merged = dict(original)
    trusted = known_raw_fields(original)
    if result["status"] == "success":
        for field in set(FIELD_TO_RAW.values()):
            if field not in result["record"] or field in trusted:
                continue
            value = result["record"][field]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (str, int, float))
                or isinstance(value, (int, float)) and not math.isfinite(value)
            ):
                raise ValueError("invalid_extracted_field_type")
            merged[field] = value
    # Do not accept AI metadata from the original source or arbitrary model keys.
    for field in tuple(merged):
        if field.startswith("ai_") or field == "processing_method":
            merged.pop(field)
    merged.update(
        _agent_event_id=event_id, ai_event_id=event_id,
        ai_status=result["status"], ai_attempted=bool(result.get("attempts")),
        processing_method="ai_extraction", ai_completed_at=datetime.now(timezone.utc).isoformat(),
    )
    for key in ("provider", "model", "error_code"):
        value = result.get(key)
        if isinstance(value, str):
            merged[f"ai_{key}"] = value[:160]
    confidence = result.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(confidence) and 0 <= confidence <= 1:
        merged["ai_confidence"] = confidence
    for source, target in (("attempts", "ai_attempts"), ("duration_seconds", "ai_duration_seconds")):
        value = result.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            merged[target] = value
    for field in PROVENANCE_FIELDS:
        if field in original:
            merged[field] = original[field]
        else:
            merged.pop(field, None)
    return merged


def publish_result_failure(pipeline, envelope, error_code):
    """Terminal failures must be recoverable before the result offset advances."""
    delivered = []
    payload = {
        "schema_version": 1, "event_id": envelope["event_id"],
        "error_code": error_code, "failed_at": datetime.now(timezone.utc).isoformat(),
        "input": envelope,
    }
    pipeline.clean_producer.produce(
        pipeline.ai_dlq_topic, key=envelope["record"]["url"],
        value=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
        callback=lambda error, message: delivered.append(error),
    )
    pending = pipeline.clean_producer.flush(pipeline.ai_delivery_timeout)
    if pending or not delivered or delivered[0] is not None:
        raise RuntimeError("AI result failure DLQ not acknowledged; result offset remains uncommitted")


def handle_ai_result(envelope, pipeline):
    origin, record, event_id, result = validate_result_envelope(envelope)
    if origin != pipeline.origin:
        raise ValueError("result_pipeline_origin_mismatch")
    receipts = pipeline.db["ai_result_receipts"]
    receipt = receipts.find_one({"_id": event_id})
    if receipt and receipt.get("status") == "completed":
        return {"url": record["url"], "ai_status": receipt["outcome"], "processing_method": "ai_result_replay"}

    write_cleaning_audit(pipeline, record, result, event_id, origin)

    latest = pipeline.raw_collection.find_one({"url": record["url"]}, {"_agent_event_id": 1})
    if not latest or latest.get("_agent_event_id") != event_id:
        outcome = "stale"
        normalized = {"url": record["url"], "ai_status": "stale", "processing_method": "ai_result_stale"}
    else:
        try:
            merged = result_payload(record, result, event_id)
            write_cleaning_audit(pipeline, record, result, event_id, origin, merged)
        except ValueError:
            result = {"status": "failed", "error_code": "invalid_extracted_field_type", "attempts": result.get("attempts", 0)}
            merged = result_payload(record, result, event_id)
        normalized = pipeline.process_payload(merged, skip_ai=True, raw_already_saved=True)
        invalid = bool(normalized.get("validation_errors") or normalized.get("listing_review_status") == "INVALID")
        if result["status"] != "success" or invalid:
            error_code = result.get("error_code") or "post_extraction_validation"
            if not isinstance(error_code, str):
                error_code = "extraction_failed"
            error_code = error_code[:160]
            pipeline.db["ai_failures"].update_one({"_id": event_id}, {"$set": {
                "url": record["url"], "event_id": event_id, "record": record,
                "origin": origin, "ai_status": "failed", "error_code": error_code,
                "is_model_candidate": False,
            }}, upsert=True)
            publish_result_failure(pipeline, envelope, error_code)
            outcome = "invalid" if result["status"] == "success" else "failed"
        else:
            outcome = "success"
    receipts.update_one({"_id": event_id}, {"$set": {
        "status": "completed", "outcome": outcome, "url": record["url"],
        "origin": origin, "completed_at": datetime.now(timezone.utc).isoformat(),
    }}, upsert=True)
    return normalized
