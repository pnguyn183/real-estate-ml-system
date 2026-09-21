from __future__ import annotations

"""
Module: processing/kafka_to_mongo.py
Purpose: Consume raw scraped listings from Kafka, normalize/validate them, and upsert
documents into MongoDB. Publishes cleaned feature messages and writes DLQ/invalid records.
Algorithms/techniques: localized number parsing, price/unit normalization, idempotent upsert by URL,
dead-letter handling, graceful shutdown via signals, and Prometheus instrumentation.
Inputs: JSON messages from Kafka topic (raw listing fields).
Outputs: MongoDB collections (`listings_raw`, `training_features`, `invalid_records`, `dlq_raw`) and optional Kafka clean topic.
"""

import argparse
import base64
import json
import logging
import math
import os
import re
import sys
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Docker executes this file as __main__. Result validation and extraction reuse
# its parsers by their package name; both names must refer to the same module,
# otherwise Prometheus counters are registered twice on the first AI result.
if __name__ == "__main__":
    sys.modules.setdefault("processing.kafka_to_mongo", sys.modules[__name__])

from confluent_kafka import Consumer, Producer, TopicPartition
from pymongo import MongoClient
from prometheus_client import Counter, Histogram
import signal
import threading
from utils.logging_utils import log_structured, get_logger
from processing.feature_engineering import enrich_geographic_features
from processing.llm_review import OptionalLLMReviewer
from processing.price_anomaly import (
    PriceAnomalyConfig,
    PriceAnomalyDetector,
    annotate_listing_review,
    safe_price_per_m2,
)
from processing.text_enrichment import SQLiteTextEmbeddingCache
from agents.safety import is_synthetic_record
from utils.metrics import (
    start_prometheus_server,
    kafka_messages_consumed,
    kafka_messages_processed,
    kafka_messages_failed,
    kafka_consumer_lag,
    db_writes_success,
    db_writes_failed,
    processing_duration,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

processor_ai_requests = Counter(
    "processor_ai_requests_total", "AI queue delivery outcomes", ["outcome"]
)
processor_ai_enqueue_duration = Histogram(
    "processor_ai_enqueue_duration_seconds", "Time spent awaiting AI request delivery acknowledgment"
)
processor_synthetic_rejected = Counter(
    "processor_synthetic_rejected_total", "Records rejected by live/stress isolation checks"
)

TRACE_FIELDS = (
    "source_type", "is_synthetic", "generated_by", "scenario", "run_id",
    "original_record_id", "processing_method", "ai_status", "ai_attempted",
    "ai_provider", "ai_model", "ai_confidence", "ai_error_code", "ai_attempts",
    "ai_duration_seconds", "ai_event_id", "ai_requested_at", "ai_completed_at",
    "_agent_event_id",
)
AI_TERMINAL_STATUSES = {"success", "failed", "disabled"}


def agent_event_id(payload: Dict[str, Any]) -> str:
    """Stable source-version identity shared with the asynchronous AI worker."""
    source = {key: value for key, value in payload.items() if key not in {"_id", "_agent_event_id"}}
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def extraction_required_errors(record: Dict[str, Any]) -> list[str]:
    """Fields the extractor must complete before its result is accepted."""
    errors = []
    for field in ("price_vnd", "area_m2"):
        value = record.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            errors.append(f"missing_or_invalid_{field}")
    if not safe_text(record.get("property_type")):
        errors.append("missing_property_type")
    _, validation_errors = validate_normalized_record(record)
    errors.extend(error for error in validation_errors if error.startswith(("price_", "area_")))
    return list(dict.fromkeys(errors))


def extraction_routing_errors(record: Dict[str, Any]) -> list[str]:
    """Include geography gaps when deciding whether to ask the AI for help."""
    errors = extraction_required_errors(record)
    if not safe_text(record.get("province_slug")):
        errors.append("missing_province")
    if not safe_text(record.get("district_slug")):
        errors.append("missing_district")
    return list(dict.fromkeys(errors))

# Graceful shutdown event for consumers
_shutdown_event = threading.Event()


def _handle_signal(signum, frame):
    log_structured(logging.INFO, "shutdown_signal_received", signal=signum)
    _shutdown_event.set()


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)*")
PRICE_TY_PATTERN = re.compile(r"(\d+(?:[.,]\d+)*)\s*t(?:ỷ|y)", re.IGNORECASE)
PRICE_TRIEU_PATTERN = re.compile(r"(\d+(?:[.,]\d+)*)\s*tri(?:ệu|eu)", re.IGNORECASE)


def safe_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def parse_localized_number_token(token: str) -> float | None:
    token = token.strip()
    if not token:
        return None

    dot_count = token.count(".")
    comma_count = token.count(",")
    if dot_count and comma_count:
        decimal_sep = "." if token.rfind(".") > token.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        token = token.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif dot_count or comma_count:
        sep = "." if dot_count else ","
        parts = token.split(sep)
        if len(parts) > 2:
            token = "".join(parts)
        elif len(parts[-1]) == 3 and len(parts[0]) <= 3:
            token = "".join(parts)
        else:
            token = token.replace(sep, ".")

    try:
        return float(token)
    except ValueError:
        return None


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    match = NUMBER_PATTERN.search(text)
    if not match:
        return None

    return parse_localized_number_token(match.group())


def parse_int(value: Any) -> int | None:
    number = parse_number(value)
    if number is None:
        return None
    return int(round(number))


def parse_price_to_vnd(value: Any, area_m2: float | None = None) -> tuple[float | None, float | None]:
    if value is None:
        return None, None

    text = str(value).lower().strip()
    total_price_vnd = None
    price_per_m2_vnd = None

    ty_match = PRICE_TY_PATTERN.search(text)
    trieu_match = PRICE_TRIEU_PATTERN.search(text)

    if "/m²" in text or "/m2" in text or "/mÂ²" in text:
        if ty_match:
            ty_amount = parse_localized_number_token(ty_match.group(1))
            price_per_m2_vnd = ty_amount * 1_000_000_000 if ty_amount is not None else None
        elif trieu_match:
            trieu_amount = parse_localized_number_token(trieu_match.group(1))
            price_per_m2_vnd = trieu_amount * 1_000_000 if trieu_amount is not None else None
        if price_per_m2_vnd is not None and area_m2 and area_m2 > 0:
            total_price_vnd = price_per_m2_vnd * area_m2
        return total_price_vnd, price_per_m2_vnd

    total_price_vnd = 0
    if ty_match:
        ty_amount = parse_localized_number_token(ty_match.group(1))
        if ty_amount is not None:
            total_price_vnd += ty_amount * 1_000_000_000
    if trieu_match:
        trieu_amount = parse_localized_number_token(trieu_match.group(1))
        if trieu_amount is not None:
            total_price_vnd += trieu_amount * 1_000_000
    if total_price_vnd == 0:
        total_price_vnd = None

    if total_price_vnd is not None and area_m2 and area_m2 > 0:
        price_per_m2_vnd = total_price_vnd / area_m2

    return total_price_vnd, price_per_m2_vnd


def build_text_features(record: Dict[str, Any]) -> str:
    parts = [
        safe_text(record.get("title")),
        safe_text(record.get("property_type")),
        safe_text(record.get("province_slug")),
        safe_text(record.get("district_slug")),
        safe_text(record.get("ward_slug")),
        safe_text(record.get("project_hint")),
        safe_text(record.get("furniture")),
        safe_text(record.get("legal")),
        safe_text(record.get("direction")),
        safe_text(record.get("description")),
    ]
    return " | ".join(part for part in parts if part)


def normalize_listing(raw: Dict[str, Any]) -> Dict[str, Any]:
    canonical = None
    if "schema_version" in raw:
        from processing.source_contract import normalize_contract
        canonical = normalize_contract(raw)
        raw = canonical
    area_m2 = parse_number(raw.get("area_text"))
    bedroom_count = parse_int(raw.get("bedroom_text"))
    bathroom_count = parse_int(raw.get("bathroom_text"))
    floor_count = parse_int(raw.get("floor_text"))
    front_width_m = parse_number(raw.get("front_width_text"))
    road_width_m = parse_number(raw.get("road_width_text"))
    price_vnd, price_per_m2_vnd = parse_price_to_vnd(raw.get("price_text"), area_m2=area_m2)
    # Keep the existing price parser as the source of units, then reject NaN/Infinity/invalid divisions.
    price_per_m2_vnd = safe_price_per_m2(price_vnd, area_m2)

    normalized = {
        "url": raw.get("url"),
        "listing_id": safe_text(raw.get("listing_id")),
        "title": safe_text(raw.get("title")),
        "property_type": safe_text(raw.get("property_type")),
        "listing_type": safe_text(raw.get("listing_type")),
        "province_slug": safe_text(raw.get("province_slug")),
        "district_slug": safe_text(raw.get("district_slug")),
        "ward_slug": safe_text(raw.get("ward_slug")),
        "location_slug": safe_text(raw.get("location_slug")),
        "direction": safe_text(raw.get("direction_text")),
        "legal": safe_text(raw.get("legal_text")),
        "furniture": safe_text(raw.get("furniture_text")),
        "project_hint": safe_text(raw.get("project_hint")),
        "description": safe_text(raw.get("description")),
        "verified": int(raw.get("verified", 0) or 0),
        "posted_date_text": safe_text(raw.get("posted_date_text")),
        "raw_price_text": safe_text(raw.get("price_text")),
        "raw_area_text": safe_text(raw.get("area_text")),
        "raw_bedroom_text": safe_text(raw.get("bedroom_text")),
        "raw_bathroom_text": safe_text(raw.get("bathroom_text")),
        "raw_floor_text": safe_text(raw.get("floor_text")),
        "raw_front_width_text": safe_text(raw.get("front_width_text")),
        "raw_road_width_text": safe_text(raw.get("road_width_text")),
        "area_m2": area_m2,
        "bedroom_count": bedroom_count,
        "bathroom_count": bathroom_count,
        "floor_count": floor_count,
        "front_width_m": front_width_m,
        "road_width_m": road_width_m,
        "price_vnd": price_vnd,
        "price_per_m2_vnd": price_per_m2_vnd,
        "has_target_price": bool(price_vnd and price_vnd > 0),
        "text_features": "",
        "source": safe_text(raw.get("source")) or "legacy_unknown",
        "scraped_at": raw.get("scraped_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    normalized.update({key: raw[key] for key in TRACE_FIELDS if key in raw})
    if canonical is not None:
        for key in (
            "schema_version", "source_listing_id", "source_url", "canonical_url", "crawl_timestamp",
            "raw_payload_hash", "price_raw", "price_value", "price_unit", "price_is_negotiable",
            "area_raw", "address_raw", "address_old", "address_current", "address_version",
            "province", "district", "ward", "street", "posted_at", "raw_data", "source_errors",
            "validation_errors", "validation_warnings", "extraction_status", "training_excluded",
            "price_vnd", "price_per_m2_vnd", "area_m2", "bedroom_count", "bathroom_count",
            "floor_count", "front_width_m", "road_width_m",
        ):
            normalized[key] = canonical.get(key)
        normalized["has_target_price"] = bool(normalized["price_vnd"] and normalized["price_vnd"] > 0)
    normalized["is_synthetic"] = is_synthetic_record(raw)
    normalized.setdefault("processing_method", "deterministic")
    normalized["text_features"] = build_text_features(normalized)
    normalized.update(enrich_geographic_features(raw))
    # Local hashing is deterministic and cacheable. It does not call an external
    # embedding/LLM service or introduce a secret into this ingestion path.
    normalized.update(SQLiteTextEmbeddingCache().enrich_many([normalized])[0])
    fingerprint_source = {
        key: normalized.get(key)
        for key in ("url", "price_vnd", "area_m2", "property_type", "title", "description")
    }
    normalized["listing_fingerprint"] = hashlib.sha256(
        json.dumps(fingerprint_source, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    normalized["feature_coverage_score"] = sum(
        value is not None
        for value in [
            normalized["area_m2"],
            normalized["price_vnd"],
            normalized["bedroom_count"],
            normalized["bathroom_count"],
            normalized["floor_count"],
            normalized["front_width_m"],
            normalized["road_width_m"],
            normalized["property_type"],
            normalized["province_slug"],
            normalized["district_slug"],
        ]
    )
    normalized["is_model_candidate"] = bool(
        normalized["has_target_price"]
        and normalized["area_m2"]
        and normalized["area_m2"] > 0
        and normalized["property_type"]
        and normalized["feature_coverage_score"] >= 5
        and not normalized["is_synthetic"]
        and not normalized.get("training_excluded", False)
    )
    return normalized


def validate_normalized_record(record: Dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = list(record.get("validation_errors") or [])
    url = record.get("url")
    if not url:
        errors.append("missing_url")

    pv = record.get("price_vnd")
    if pv is not None:
        try:
            if not isinstance(pv, (int, float)) or isinstance(pv, bool) or not math.isfinite(float(pv)):
                errors.append("price_invalid")
            elif pv <= 0:
                errors.append("price_non_positive")
            elif pv > 500_000_000_000:
                errors.append("price_too_large")
        except Exception:
            errors.append("price_invalid")

    area = record.get("area_m2")
    if area is not None:
        try:
            if not isinstance(area, (int, float)) or isinstance(area, bool) or not math.isfinite(float(area)):
                errors.append("area_invalid")
            elif area <= 0:
                errors.append("area_non_positive")
            elif area > 10000:
                errors.append("area_too_large")
        except Exception:
            errors.append("area_invalid")

    ppm = record.get("price_per_m2_vnd")
    if pv and area and ppm:
        expected = pv / area
        if expected and abs(expected - ppm) > max(1.0, expected * 0.05):
            errors.append("price_per_m2_inconsistent")

    return (len(errors) == 0, errors)


class KafkaToMongoPipeline:
    def __init__(
        self,
        kafka_bootstrap_servers: str,
        raw_topic: str,
        clean_topic: str,
        group_id: str,
        mongo_uri: str,
        mongo_db: str,
        *,
        consumer_enabled: bool = True,
        allow_synthetic: bool = False,
        origin: str = "real",
    ) -> None:
        stress_db = os.environ.get("MONGO_STRESS_DB", "real_estate_stress_db")
        if origin not in {"real", "stress"}:
            raise ValueError("Pipeline origin must be real or stress")
        if stress_db == os.environ.get("MONGO_DB", "real_estate_db"):
            raise ValueError("Live and stress databases must be distinct")
        if allow_synthetic and (origin != "stress" or mongo_db != stress_db):
            raise ValueError("Synthetic records require the isolated configured stress database")
        if origin == "real" and mongo_db == stress_db:
            raise ValueError("Live pipeline cannot use the stress database")
        if origin == "stress" and not allow_synthetic:
            raise ValueError("Stress pipeline must explicitly allow synthetic records")
        self.raw_topic = raw_topic
        self.clean_topic = clean_topic
        self.group_id = group_id
        self.origin = origin
        self.allow_synthetic = allow_synthetic
        self.ai_enabled = os.environ.get(
            "AI_STRESS_ENABLED" if origin == "stress" else "AI_FALLBACK_ENABLED", "false"
        ).lower() in {"1", "true", "yes"}
        self.ai_topic = os.environ.get("KAFKA_AI_TOPIC", "real_estate_ai_input")
        self.ai_result_topic = os.environ.get("KAFKA_AI_RESULT_TOPIC", "real_estate_ai_results")
        self.ai_dlq_topic = os.environ.get("KAFKA_AI_DLQ_TOPIC", "real_estate_ai_dlq")
        self.ai_delivery_timeout = float(os.environ.get("KAFKA_AI_DELIVERY_TIMEOUT_SECONDS", "15"))
        if not math.isfinite(self.ai_delivery_timeout) or not 0 < self.ai_delivery_timeout <= 120:
            raise ValueError("KAFKA_AI_DELIVERY_TIMEOUT_SECONDS must be in (0, 120]")
        self.stress_topic = os.environ.get("KAFKA_STRESS_TOPIC", "real_estate_stress_raw")
        if len({self.raw_topic, self.stress_topic, self.ai_topic, self.ai_result_topic, self.ai_dlq_topic}) != 5 and consumer_enabled:
            raise ValueError("Live, stress, AI request, result and DLQ topics must be distinct")
        self.consumer = None
        self.stress_pipeline = None
        if consumer_enabled:
            self.consumer = Consumer(
                {
                    "bootstrap.servers": kafka_bootstrap_servers,
                    "group.id": group_id,
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": False,
                    "enable.auto.offset.store": False,
                    "max.poll.interval.ms": 900000,
                }
            )
            self.consumer.subscribe(
                [self.raw_topic, self.stress_topic, self.ai_result_topic],
                on_assign=self._on_assign, on_revoke=self._on_revoke,
            )
        self.clean_producer = Producer({
            "bootstrap.servers": kafka_bootstrap_servers, "linger.ms": 50,
            "enable.idempotence": True, "acks": "all",
            "delivery.timeout.ms": int(self.ai_delivery_timeout * 1000),
        })
        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client[mongo_db]
        self.raw_collection = self.db["listings_raw"]
        self.feature_collection = self.db["training_features"]
        self.invalid_collection = self.db["invalid_records"]
        self.dlq_collection = self.db["dlq_raw"]
        self.anomaly_threshold_collection = self.db["price_anomaly_thresholds"]
        self.price_anomaly_detector = PriceAnomalyDetector(PriceAnomalyConfig.from_env())
        self.llm_reviewer = OptionalLLMReviewer()
        self._logger = get_logger(__name__)
        self._ensure_indexes()
        if consumer_enabled:
            self.stress_pipeline = KafkaToMongoPipeline(
                kafka_bootstrap_servers, self.stress_topic,
                os.environ.get("KAFKA_STRESS_CLEAN_TOPIC", "real_estate_stress_features"),
                group_id, mongo_uri, stress_db,
                consumer_enabled=False, allow_synthetic=True, origin="stress",
            )

    def _on_assign(self, consumer, partitions) -> None:
        log_structured(
            logging.INFO, "processor_partitions_assigned", group_id=self.group_id,
            partitions=[{"topic": item.topic, "partition": item.partition} for item in partitions],
        )
        consumer.assign(partitions)

    def _on_revoke(self, consumer, partitions) -> None:
        log_structured(
            logging.INFO, "processor_partitions_revoked", group_id=self.group_id,
            partitions=[{"topic": item.topic, "partition": item.partition} for item in partitions],
        )

    def _ensure_indexes(self) -> None:
        self.raw_collection.create_index("url", unique=True)
        self.feature_collection.create_index("url", unique=True)
        # Lookup provenance without imposing a new uniqueness constraint on
        # historical data. URL remains the existing idempotent upsert key.
        self.raw_collection.create_index([("source", 1), ("source_listing_id", 1)])
        self.feature_collection.create_index([("source", 1), ("source_listing_id", 1)])
        self.feature_collection.create_index("price_vnd")
        self.feature_collection.create_index("price_per_m2_vnd")
        self.feature_collection.create_index("property_type")
        self.feature_collection.create_index("province_slug")
        self.feature_collection.create_index("district_slug")
        self.feature_collection.create_index("is_model_candidate")
        self.feature_collection.create_index("feature_coverage_score")
        self.feature_collection.create_index("listing_fingerprint")
        self.feature_collection.create_index(
            [("province_slug", 1), ("district_slug", 1), ("property_type", 1), ("price_per_m2_vnd", 1)]
        )
        self.anomaly_threshold_collection.create_index([("config_id", 1), ("group_key", 1)], unique=True)
        self.db["offset_checkpoint"].create_index(
            [("group_id", 1), ("topic", 1), ("partition", 1)],
            unique=True,
        )

    def _enqueue_ai(self, payload: Dict[str, Any], event_id: str) -> Dict[str, Any]:
        """Never acknowledge the input offset before the AI request is delivered."""
        requested_at = datetime.now(timezone.utc).isoformat()
        envelope = {
            "schema_version": 1,
            "origin": self.origin,
            "record": payload,
            "event_id": event_id,
            "requested_at": requested_at,
        }
        delivery = {"done": False, "error": None}

        def delivered(error, message):
            delivery["done"] = True
            delivery["error"] = error

        started = time.monotonic()
        try:
            self.clean_producer.produce(
                self.ai_topic, key=payload.get("url"),
                value=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
                callback=delivered,
            )
            deadline = started + self.ai_delivery_timeout
            while not delivery["done"] and time.monotonic() < deadline:
                self.clean_producer.poll(min(0.25, max(0, deadline - time.monotonic())))
            if not delivery["done"]:
                raise TimeoutError("AI request delivery acknowledgment timed out")
            if delivery["error"] is not None:
                raise RuntimeError(f"AI request delivery failed: {delivery['error']}")
        except Exception:
            processor_ai_requests.labels(outcome="failed").inc()
            raise
        finally:
            processor_ai_enqueue_duration.observe(time.monotonic() - started)
        processor_ai_requests.labels(outcome="queued").inc()
        return {
            "ai_status": "queued", "ai_attempted": False,
            "ai_event_id": event_id, "ai_requested_at": requested_at,
            "processing_method": "ai_pending", "is_model_candidate": False,
        }

    def _reject_origin(self, payload: Dict[str, Any], event_id: str, reason: str) -> Dict[str, Any]:
        # A deterministic key makes isolation rejection idempotent across replays.
        try:
            self.dlq_collection.update_one(
                {"event_id": event_id, "reason": reason},
                {"$set": {"payload": payload, "event_id": event_id, "reason": reason}},
                upsert=True,
            )
            db_writes_success.inc()
        except Exception:
            db_writes_failed.inc()
            raise
        processor_synthetic_rejected.inc()
        log_structured(logging.WARNING, "pipeline_origin_rejected", reason=reason, event_id=event_id)
        return {
            "url": payload.get("url"), "is_synthetic": is_synthetic_record(payload),
            "is_model_candidate": False, "listing_review_status": "INVALID",
            "validation_errors": [reason], "processing_method": "origin_rejected",
        }

    def process_payload(
        self, payload: Dict[str, Any], *, skip_ai: bool = False, raw_already_saved: bool = False
    ) -> Dict[str, Any]:
        start_time = time.time()
        if not isinstance(payload, dict):
            raise ValueError("Listing payload must be a JSON object")
        event_id = payload.get("_agent_event_id") if raw_already_saved else None
        event_id = event_id or agent_event_id(payload)
        if not self.allow_synthetic and is_synthetic_record(payload):
            return self._reject_origin(payload, event_id, "synthetic_record_in_live_pipeline")
        if self.origin == "stress":
            host = (urlparse(str(payload.get("url") or "")).hostname or "").lower()
            marked = is_synthetic_record({key: value for key, value in payload.items() if key != "url"})
            if not marked or not (host == "synthetic.invalid" or host.endswith(".synthetic.invalid")):
                return self._reject_origin(payload, event_id, "unmarked_or_non_synthetic_url_in_stress_pipeline")

        # Preserve the original source, never overwrite it with AI-returned values.
        if not raw_already_saved:
            try:
                self.raw_collection.update_one(
                    {"url": payload.get("url")}, {"$set": dict(payload, _agent_event_id=event_id)}, upsert=True
                )
                db_writes_success.inc()
            except Exception as exc:
                log_structured(logging.ERROR, "raw_db_write_failed", url=payload.get("url"), error=str(exc))
                db_writes_failed.inc()
                try:
                    self.dlq_collection.insert_one({"payload": payload, "error": type(exc).__name__})
                except Exception:
                    log_structured(logging.ERROR, "raw_failure_dlq_write_failed", event_id=event_id)
                raise

        normalization_errors = []
        try:
            normalized = normalize_listing(payload)
        except (ValueError, TypeError, OverflowError) as exc:
            normalization_errors = [f"normalization_{type(exc).__name__}"]
            normalized = {
                "url": payload.get("url"), "is_model_candidate": False,
                "is_synthetic": is_synthetic_record(payload),
                **{key: payload[key] for key in TRACE_FIELDS if key in payload},
            }
        normalized["_agent_event_id"] = event_id
        # A newer version must never leave the old feature silently trainable
        # while validation or asynchronous extraction is pending. Preserve its
        # historical values for review, but exclude it from every trainer.
        if normalized.get("schema_version") is not None:
            previous = self.feature_collection.find_one({"url": payload.get("url")})
            if previous and previous.get("_agent_event_id") != event_id:
                self.feature_collection.update_one({"url": payload.get("url")}, {"$set": {
                    "training_excluded": True, "is_model_candidate": False,
                    "feature_status": "superseded_pending_validation",
                }})

        # Validate before storing features
        _, errors = validate_normalized_record(normalized)
        errors.extend(normalization_errors)
        required_errors = extraction_routing_errors(normalized)
        terminal_status = str(payload.get("ai_status") or "").lower()
        identity_errors = [error for error in errors if error not in {"area_unparseable_or_out_of_range", "price_unparseable_or_out_of_range"}]
        if self.ai_enabled and not skip_ai and terminal_status not in AI_TERMINAL_STATUSES and required_errors and not normalization_errors and not identity_errors and not normalized.get("price_is_negotiable"):
            # Route exactly the scalar facts the extractor can read. A separate
            # field list can reject supported aliases or enqueue unusable objects.
            # Import lazily because extraction reuses this module's parsers.
            from agents.extraction import source_text

            if payload.get("url") and source_text(payload):
                normalized.update(self._enqueue_ai(payload, event_id))
                processing_duration.observe(time.time() - start_time)
                return normalized
            normalized.update({"ai_status": "failed", "ai_error_code": "no_extractable_text_or_url", "ai_attempted": False})

        # AI-enabled incomplete records must not silently fall through to training.
        # With AI disabled, historical optional/missing-value behavior stays intact.
        if self.ai_enabled or skip_ai or terminal_status in AI_TERMINAL_STATUSES:
            errors.extend(required_errors)
        if terminal_status in {"failed", "disabled"}:
            errors.append(f"ai_extraction_{terminal_status}")
        errors = list(dict.fromkeys(errors))
        is_valid = not errors
        existing_feature = self.feature_collection.find_one(
            {"url": normalized.get("url")}, {"_id": 0, "listing_fingerprint": 1}
        )
        is_duplicate = bool(
            existing_feature and existing_feature.get("listing_fingerprint") == normalized.get("listing_fingerprint")
        )
        if not is_valid:
            normalized["is_model_candidate"] = False
            normalized["training_excluded"] = True
            normalized["validation_errors"] = errors
            annotate_listing_review(normalized, validation_errors=errors, is_duplicate=is_duplicate)
            normalized.update(self.llm_reviewer.review(normalized))
            try:
                self.invalid_collection.update_one({"url": normalized.get("url")}, {"$set": normalized}, upsert=True)
                db_writes_success.inc()
            except Exception as exc:
                log_structured(logging.ERROR, "invalid_collection_write_failed", url=normalized.get("url"), error=str(exc))
                db_writes_failed.inc()
                try:
                    self.dlq_collection.insert_one({"payload": payload, "normalized": normalized, "error": type(exc).__name__})
                except Exception:
                    log_structured(logging.ERROR, "invalid_failure_dlq_write_failed", event_id=event_id)
                raise
            # still produce to features topic for auditing (optional)
            try:
                self.clean_producer.produce(
                    self.clean_topic,
                    key=normalized.get("url"),
                    value=json.dumps(normalized, ensure_ascii=False).encode("utf-8"),
                )
                self.clean_producer.poll(0)
            except Exception as exc:
                log_structured(logging.WARNING, "produce_invalid_failed", url=normalized.get("url"), error=str(exc))
                kafka_messages_failed.inc()
            processing_duration.observe(time.time() - start_time)
            return normalized

        # Build thresholds from historical features before this listing is upserted, then flag the Silver record.
        try:
            refreshed = self.price_anomaly_detector.refresh_if_needed(
                self.feature_collection,
                self.anomaly_threshold_collection,
                exclude_url=normalized.get("url"),
            )
            self.price_anomaly_detector.annotate(normalized)
            if refreshed or normalized.get("is_price_anomaly"):
                log_structured(
                    logging.INFO,
                    "price_anomaly_detection_result",
                    url=normalized.get("url"),
                    refreshed_baseline=refreshed,
                    is_price_anomaly=normalized.get("is_price_anomaly"),
                    anomaly_type=normalized.get("price_anomaly_type"),
                    anomaly_status=normalized.get("price_anomaly_status"),
                    anomaly_reason=normalized.get("price_anomaly_reason"),
                )
        except Exception as exc:
            # Detection must not stop ingestion; preserve an explicit audit state rather than silently omitting it.
            log_structured(logging.ERROR, "price_anomaly_detection_failed", url=normalized.get("url"), error=str(exc))
            normalized.update(
                {
                    "is_price_anomaly": False,
                    "price_anomaly_status": "UNAVAILABLE",
                    "price_anomaly_reason": "detector_error",
                }
            )

        annotate_listing_review(normalized, is_duplicate=is_duplicate)
        normalized.update(self.llm_reviewer.review(normalized))

        # Store normalized features
        normalized["feature_status"] = "current"
        try:
            self.feature_collection.update_one({"url": normalized.get("url")}, {"$set": normalized}, upsert=True)
            db_writes_success.inc()
        except Exception as exc:
            log_structured(logging.ERROR, "feature_db_write_failed", url=normalized.get("url"), error=str(exc))
            db_writes_failed.inc()
            # Put raw payload in DLQ for manual replay
            try:
                self.dlq_collection.insert_one({"payload": payload, "normalized": normalized, "error": str(exc)})
            except Exception:
                log_structured(logging.ERROR, "feature_failure_dlq_write_failed", event_id=event_id)
            raise

        # Best-effort: publish normalized message
        try:
            self.clean_producer.produce(
                self.clean_topic,
                key=normalized.get("url"),
                value=json.dumps(normalized, ensure_ascii=False).encode("utf-8"),
            )
            self.clean_producer.poll(0)
            kafka_messages_processed.inc()
        except Exception as exc:
            log_structured(logging.WARNING, "produce_features_failed", url=normalized.get("url"), error=str(exc))
            kafka_messages_failed.inc()

        processing_duration.observe(time.time() - start_time)
        return normalized

    def consume_forever(self, max_messages: int | None = None) -> None:
        if self.consumer is None:
            raise RuntimeError("Persistence-only pipeline has no Kafka consumer")
        count = 0
        try:
            while not _shutdown_event.is_set():
                message = self.consumer.poll(1.0)
                if message is None:
                    continue
                if message.error():
                    log_structured(logging.ERROR, "kafka_consumer_error", error=str(message.error()))
                    continue

                kafka_messages_consumed.inc()
                self.update_consumer_lag(message)
                is_ai_result = message.topic() == self.ai_result_topic
                if message.topic() == self.raw_topic or is_ai_result:
                    pipeline = self
                elif message.topic() == self.stress_topic and self.stress_pipeline is not None:
                    pipeline = self.stress_pipeline
                else:
                    raise RuntimeError("Consumer received an unconfigured topic")
                try:
                    payload = json.loads(message.value().decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("Listing payload must be a JSON object")
                    if is_ai_result:
                        from agents.results import validate_result_envelope
                        origin, _, _, _ = validate_result_envelope(payload)
                        pipeline = self.stress_pipeline if origin == "stress" else self
                        if pipeline is None:
                            raise ValueError("Missing isolated pipeline for stress AI result")
                except Exception as exc:
                    log_structured(logging.ERROR, "kafka_deserialize_failed", error=str(exc))
                    kafka_messages_failed.inc()
                    # Preserve arbitrary bytes, including invalid UTF-8/tombstones.
                    # Failed DLQ persistence must leave the input offset uncommitted.
                    try:
                        identity = {"topic": message.topic(), "partition": message.partition(), "offset": message.offset()}
                        pipeline.dlq_collection.update_one(
                            identity,
                            {"$set": dict(
                                identity,
                                raw_base64=base64.b64encode(message.value() or b"").decode("ascii"),
                                is_tombstone=message.value() is None,
                                error=type(exc).__name__,
                            )},
                            upsert=True,
                        )
                        db_writes_success.inc()
                    except Exception:
                        db_writes_failed.inc()
                        log_structured(logging.ERROR, "malformed_message_dlq_failed", topic=message.topic())
                        raise
                    self.consumer.commit(message=message, asynchronous=False)
                    pipeline.commit_offset_checkpoint(message)
                    count += 1
                    if max_messages and count >= max_messages:
                        break
                    continue

                try:
                    if is_ai_result:
                        from agents.results import handle_ai_result
                        normalized = handle_ai_result(payload, pipeline)
                    else:
                        normalized = pipeline.process_payload(payload)
                    count += 1
                    log_structured(
                        logging.INFO, "prepared_features", url=normalized.get("url"),
                        topic=message.topic(), partition=message.partition(), offset=message.offset(),
                        group_id=self.group_id, origin=pipeline.origin,
                        processing_method=normalized.get("processing_method", "deterministic"),
                        ai_status=normalized.get("ai_status"),
                        listing_review_status=normalized.get("listing_review_status"),
                    )
                    try:
                        self.consumer.commit(message=message, asynchronous=False)
                        pipeline.commit_offset_checkpoint(message)
                    except Exception as exc:
                        log_structured(logging.WARNING, "commit_failed", error=str(exc))
                        # Do not poll/commit a higher offset after a failed commit.
                        # Restart resumes from Kafka's last durable checkpoint.
                        raise
                except Exception as exc:
                    # processing failed; do not commit so message can be retried
                    log_structured(logging.ERROR, "processing_failed", error=str(exc))
                    kafka_messages_failed.inc()
                    raise
                if max_messages and count >= max_messages:
                    break
        finally:
            self.close()

    def close(self) -> None:
        log_structured(logging.INFO, "price_anomaly_detection_summary", **self.price_anomaly_detector.metrics())
        try:
            remaining = self.clean_producer.flush(self.ai_delivery_timeout)
            if remaining:
                log_structured(logging.WARNING, "producer_shutdown_pending_messages", count=remaining)
        finally:
            if self.consumer is not None:
                self.consumer.close()
            self.mongo_client.close()
            if self.stress_pipeline is not None:
                self.stress_pipeline.close()

    def update_consumer_lag(self, message) -> None:
        try:
            partition = TopicPartition(message.topic(), message.partition())
            low, high = self.consumer.get_watermark_offsets(partition, timeout=1.0)
            kafka_consumer_lag.set(max(high - message.offset() - 1, 0))
        except Exception as exc:
            log_structured(logging.DEBUG, "consumer_lag_update_failed", error=str(exc))

    def commit_offset_checkpoint(self, message):
        """Save the committed offset checkpoint to MongoDB for replay."""
        try:
            topic = message.topic()
            partition = message.partition()
            offset = message.offset()
            self.db["offset_checkpoint"].update_one(
                {"group_id": self.group_id, "topic": topic, "partition": partition},
                {
                    "$set": {
                        "last_processed_offset": offset,
                        "committed_offset": offset + 1,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
                upsert=True
            )
        except Exception as exc:
            log_structured(logging.WARNING, "checkpoint_write_failed", error=str(exc))


def normalize_file(input_path: Path, output_path: Path | None = None) -> List[Dict[str, Any]]:
    records = json.loads(input_path.read_text(encoding="utf-8"))
    normalized = [normalize_listing(record) for record in records]
    if output_path is not None:
        output_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Consume raw listings from Kafka and build Mongo training features.")
    parser.add_argument("--bootstrap-servers", default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"))
    parser.add_argument("--raw-topic", default=os.environ.get("KAFKA_RAW_TOPIC", "real_estate_raw"))
    parser.add_argument("--clean-topic", default=os.environ.get("KAFKA_CLEAN_TOPIC", "real_estate_features"))
    parser.add_argument("--group-id", default=os.environ.get("KAFKA_GROUP_ID", "real_estate_training_pipeline"))
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))
    parser.add_argument("--mongo-db", default=os.environ.get("MONGO_DB", "real_estate_db"))
    parser.add_argument("--max-messages", type=int, default=None)
    parser.add_argument("--input-json", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args()

    # Start Prometheus metrics server
    prometheus_port = int(os.environ.get("PROMETHEUS_METRICS_PORT", 8000))
    start_prometheus_server(prometheus_port)

    if args.input_json:
        normalized = normalize_file(args.input_json, args.output_json)
        logger.info("Prepared %s normalized records from %s", len(normalized), args.input_json)
        return

    pipeline = KafkaToMongoPipeline(
        kafka_bootstrap_servers=args.bootstrap_servers,
        raw_topic=args.raw_topic,
        clean_topic=args.clean_topic,
        group_id=args.group_id,
        mongo_uri=args.mongo_uri,
        mongo_db=args.mongo_db,
    )
    pipeline.consume_forever(max_messages=args.max_messages)


if __name__ == "__main__":
    main()
