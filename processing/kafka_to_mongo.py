from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from confluent_kafka import Consumer, Producer, TopicPartition
from pymongo import MongoClient
import signal
import threading
from utils.logging_utils import log_structured, get_logger
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

    if "/m²" in text or "/m2" in text:
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
    area_m2 = parse_number(raw.get("area_text"))
    bedroom_count = parse_int(raw.get("bedroom_text"))
    bathroom_count = parse_int(raw.get("bathroom_text"))
    floor_count = parse_int(raw.get("floor_text"))
    front_width_m = parse_number(raw.get("front_width_text"))
    road_width_m = parse_number(raw.get("road_width_text"))
    price_vnd, price_per_m2_vnd = parse_price_to_vnd(raw.get("price_text"), area_m2=area_m2)

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
        "source": safe_text(raw.get("source")) or "batdongsan_verified",
        "scraped_at": raw.get("scraped_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    normalized["text_features"] = build_text_features(normalized)
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
    )
    return normalized


def validate_normalized_record(record: Dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    url = record.get("url")
    if not url:
        errors.append("missing_url")

    pv = record.get("price_vnd")
    if pv is not None:
        try:
            if pv <= 0:
                errors.append("price_non_positive")
            if pv > 500_000_000_000:
                errors.append("price_too_large")
        except Exception:
            errors.append("price_invalid")

    area = record.get("area_m2")
    if area is not None:
        try:
            if area <= 0:
                errors.append("area_non_positive")
            if area > 10000:
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
    ) -> None:
        self.raw_topic = raw_topic
        self.clean_topic = clean_topic
        self.group_id = group_id
        self.consumer = Consumer(
            {
                "bootstrap.servers": kafka_bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
                "max.poll.interval.ms": 900000,
            }
        )
        self.consumer.subscribe([self.raw_topic])
        self.clean_producer = Producer({"bootstrap.servers": kafka_bootstrap_servers, "linger.ms": 50})
        self.mongo_client = MongoClient(mongo_uri)
        self.db = self.mongo_client[mongo_db]
        self.raw_collection = self.db["listings_raw"]
        self.feature_collection = self.db["training_features"]
        self.invalid_collection = self.db["invalid_records"]
        self.dlq_collection = self.db["dlq_raw"]
        self._logger = get_logger(__name__)
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self.raw_collection.create_index("url", unique=True)
        self.feature_collection.create_index("url", unique=True)
        self.feature_collection.create_index("price_vnd")
        self.feature_collection.create_index("price_per_m2_vnd")
        self.feature_collection.create_index("property_type")
        self.feature_collection.create_index("province_slug")
        self.feature_collection.create_index("district_slug")
        self.feature_collection.create_index("is_model_candidate")
        self.feature_collection.create_index("feature_coverage_score")
        self.db["offset_checkpoint"].create_index(
            [("group_id", 1), ("topic", 1), ("partition", 1)],
            unique=True,
        )

    def process_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        import time
        start_time = time.time()
        normalized = normalize_listing(payload)
        # Save raw payload
        try:
            self.raw_collection.update_one({"url": payload.get("url")}, {"$set": payload}, upsert=True)
            db_writes_success.inc()
        except Exception as exc:
            log_structured(logging.ERROR, "raw_db_write_failed", url=payload.get("url"), error=str(exc))
            db_writes_failed.inc()
            # store to DLQ raw collection
            try:
                self.dlq_collection.insert_one({"payload": payload, "error": str(exc)})
            except Exception:
                pass

        # Validate before storing features
        is_valid, errors = validate_normalized_record(normalized)
        if not is_valid:
            normalized["validation_errors"] = errors
            try:
                self.invalid_collection.update_one({"url": normalized.get("url")}, {"$set": normalized}, upsert=True)
                db_writes_success.inc()
            except Exception as exc:
                log_structured(logging.ERROR, "invalid_collection_write_failed", url=normalized.get("url"), error=str(exc))
                db_writes_failed.inc()
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

        # Store normalized features
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
                pass

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
                try:
                    payload = json.loads(message.value().decode("utf-8"))
                except Exception as exc:
                    log_structured(logging.ERROR, "kafka_deserialize_failed", error=str(exc))
                    kafka_messages_failed.inc()
                    # move raw message to DLQ and commit offset
                    try:
                        self.dlq_collection.insert_one({"raw": message.value().decode("utf-8"), "error": str(exc)})
                    except Exception:
                        pass
                    try:
                        self.consumer.commit(message=message, asynchronous=False)
                    except Exception:
                        pass
                    continue

                try:
                    normalized = self.process_payload(payload)
                    count += 1
                    log_structured(logging.INFO, "prepared_features", url=normalized.get("url"))
                    try:
                        self.consumer.commit(message=message, asynchronous=False)
                        self.commit_offset_checkpoint(message)
                    except Exception as exc:
                        log_structured(logging.WARNING, "commit_failed", error=str(exc))
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
        self.clean_producer.flush()
        self.consumer.close()
        self.mongo_client.close()

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
