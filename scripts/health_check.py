#!/usr/bin/env python
import os
import argparse
import requests
import logging
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB = os.environ.get("MONGO_DB", "real_estate_db")

def check_service_health(name, url):
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            logger.info(f"✓ {name}: OK")
            return True
        else:
            logger.error(f"✗ {name}: HTTP {response.status_code}")
            return False
    except Exception as exc:
        logger.error("%s: unavailable (%s)", name, type(exc).__name__)
        return False

def check_mongodb():
    client = None
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        db = client[MONGO_DB]
        raw_count = db["listings_raw"].count_documents({})
        features_count = db["training_features"].count_documents({})
        invalid_count = db["invalid_records"].count_documents({})
        dlq_count = db["dlq_raw"].count_documents({})
        logger.info(f"✓ MongoDB: OK")
        logger.info(f"  - Raw listings: {raw_count}")
        logger.info(f"  - Training features: {features_count}")
        logger.info(f"  - Invalid records: {invalid_count}")
        logger.info(f"  - DLQ: {dlq_count}")
        return True
    except Exception as exc:
        logger.error("MongoDB: unavailable (%s)", type(exc).__name__)
        return False
    finally:
        if client is not None:
            client.close()


def check_kafka():
    from confluent_kafka.admin import AdminClient
    try:
        admin = AdminClient({"bootstrap.servers": os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092,localhost:9093,localhost:9094")})
        metadata = admin.list_topics(timeout=5)
        required = {"real_estate_raw", "real_estate_features", "real_estate_stress_raw",
                    "real_estate_stress_features", "real_estate_ai_input",
                    "real_estate_ai_results", "real_estate_ai_dlq"}
        healthy = len(metadata.brokers) == 3 and all(
            name in metadata.topics and not metadata.topics[name].error
            and len(metadata.topics[name].partitions) == 3
            and all(part.leader >= 0 and len(part.replicas) == 3 and len(part.isrs) >= 2
                    for part in metadata.topics[name].partitions.values())
            for name in required)
        logger.info("Kafka topology and ISR: %s", 'OK' if healthy else 'NOT READY')
        return healthy
    except Exception as exc:
        logger.error("Kafka: unavailable (%s)", type(exc).__name__)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only local stack health; nonzero exit on failed checks.")
    parser.add_argument('--require-model', action='store_true', help='Also require actual model loading via /ready')
    args = parser.parse_args(argv)
    logger.info("========================================")
    logger.info("Real Estate Pipeline Health Check")
    logger.info("========================================")
    logger.info("")
    
    services = [
        ("Frontend", "http://localhost:3000"),
        ("API", "http://localhost:8000/health"),
        ("Prometheus", "http://localhost:9090/-/healthy"),
        ("Grafana", "http://localhost:3001/api/health"),
        ("Processor Metrics", "http://localhost:8003/metrics"),
        ("Processor 2 Metrics", "http://localhost:8004/metrics"),
        ("Processor 3 Metrics", "http://localhost:8005/metrics"),
        ("Trainer Metrics", "http://localhost:8001/metrics"),
    ]
    if args.require_model:
        services.extend([('API model readiness', 'http://localhost:8000/ready'),
                         ('Predictor model readiness', 'http://localhost:8002/ready')])
    
    results = []
    for name, url in services:
        results.append(check_service_health(name, url))
    
    logger.info("")
    results.append(check_mongodb())
    results.append(check_kafka())
    
    logger.info("")
    logger.info("========================================")
    if all(results):
        logger.info("All requested checks passed. Liveness alone does not prove enabled ingestion or AI.")
    else:
        logger.warning("Some services are not healthy.")
    logger.info("========================================")
    return 0 if all(results) else 1

if __name__ == "__main__":
    raise SystemExit(main())
