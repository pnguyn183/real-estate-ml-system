#!/usr/bin/env python
import os
import requests
import logging
import json
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
        logger.error(f"✗ {name}: {exc}")
        return False

def check_mongodb():
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
        logger.error(f"✗ MongoDB: {exc}")
        return False
    finally:
        try:
            client.close()
        except:
            pass

def main():
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
        ("Trainer Metrics", "http://localhost:8001/metrics"),
    ]
    
    results = []
    for name, url in services:
        results.append(check_service_health(name, url))
    
    logger.info("")
    check_mongodb()
    
    logger.info("")
    logger.info("========================================")
    if all(results) and check_mongodb():
        logger.info("All services are healthy!")
    else:
        logger.warning("Some services are not healthy.")
    logger.info("========================================")

if __name__ == "__main__":
    main()
