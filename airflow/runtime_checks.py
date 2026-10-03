"""Bounded task helpers, run by the application's interpreter, not Airflow's.

The offset check only reads Kafka metadata and committed offsets. It never
subscribes, joins the Processor group, consumes records, or commits offsets.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


def wait_for_processing(consumer, topic, *, timeout_seconds=600, poll_seconds=5,
                        clock=time.monotonic, sleep=time.sleep):
    """Wait for the Processor group to commit the initial raw-topic snapshot.

    An acknowledged AI handoff completes a raw offset. This is NOT a barrier
    for AI extraction/result completion and is not a per-record quality check.
    """
    from confluent_kafka import TopicPartition

    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Processing wait timeout and poll interval must be positive")
    deadline = clock() + timeout_seconds

    def remaining():
        seconds = deadline - clock()
        if seconds <= 0:
            raise TimeoutError("Processor offsets did not reach the raw-topic snapshot in time")
        return min(seconds, 10)

    metadata = consumer.list_topics(topic=topic, timeout=remaining())
    topic_metadata = metadata.topics.get(topic)
    if topic_metadata is None or topic_metadata.error or not topic_metadata.partitions:
        raise RuntimeError(f"Raw topic metadata unavailable: {topic}")
    targets = {}
    for partition_id in sorted(topic_metadata.partitions):
        _, high = consumer.get_watermark_offsets(
            TopicPartition(topic, partition_id), timeout=remaining(), cached=False,
        )
        targets[partition_id] = high
    logger.info("Waiting for Processor committed offsets: topic=%s targets=%s", topic, targets)
    while True:
        offsets = consumer.committed(
            [TopicPartition(topic, partition_id) for partition_id in targets],
            timeout=remaining(),
        )
        committed = {}
        for partition in offsets:
            if partition.error:
                raise RuntimeError(f"Committed offset unavailable for partition {partition.partition}")
            committed[partition.partition] = max(partition.offset, 0)
        if len(committed) != len(targets):
            raise RuntimeError("Kafka did not return every requested partition offset")
        lag = {partition: max(target - committed[partition], 0)
               for partition, target in targets.items()}
        if not any(lag.values()):
            logger.info("Processor group reached the raw-topic snapshot")
            return targets
        logger.info("Waiting for Processor raw-topic lag: %s", lag)
        sleep(min(poll_seconds, remaining()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["wait-for-processing", "train"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.action == "train":
        from pymongo import MongoClient
        from scripts.auto_train import (
            MIN_RECORDS, MONGO_DB, MONGO_FEATURE_COLLECTION, MONGO_URI,
            run_trainer, training_query,
        )

        # Unlike the long-running scheduler, a DAG must distinguish an empty
        # dataset (skip) from an unavailable database (failed/retriable task).
        with MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000) as client:
            count = client[MONGO_DB][MONGO_FEATURE_COLLECTION].count_documents(training_query())
        if count < MIN_RECORDS:
            logger.info("Training skipped: %s eligible real records; need %s", count, MIN_RECORDS)
            return 99
        return 0 if run_trainer() else 1

    from confluent_kafka import Consumer

    consumer = Consumer({
        "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092,kafka2:29093,kafka3:29094"),
        "group.id": os.environ.get("KAFKA_GROUP_ID", "real_estate_training_pipeline"),
        "client.id": "airflow-processor-offset-check",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "allow.auto.create.topics": False,
    })
    try:
        wait_for_processing(
            consumer,
            os.environ.get("KAFKA_RAW_TOPIC", "real_estate_raw"),
            timeout_seconds=float(os.environ.get("AIRFLOW_PROCESSING_WAIT_SECONDS", "600")),
        )
    finally:
        consumer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
