from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from modeling.price_model import RealEstatePriceModel
except ImportError:
    from price_model import RealEstatePriceModel
from utils.metrics import start_prometheus_server, update_metrics_from_result, model_train_duration


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_records_from_mongo(mongo_uri: str, mongo_db: str, collection_name: str):
    client = MongoClient(mongo_uri)
    try:
        collection = client[mongo_db][collection_name]
        return list(collection.find({"price_vnd": {"$gt": 0}}, {"_id": 0}))
    finally:
        client.close()


def load_records_from_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the property price model from MongoDB feature records.")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))
    parser.add_argument("--mongo-db", default=os.environ.get("MONGO_DB", "real_estate_db"))
    parser.add_argument("--collection", default=os.environ.get("MONGO_FEATURE_COLLECTION", "training_features"))
    parser.add_argument("--input-json", type=Path, default=None)
    parser.add_argument("--model-path", default="artifacts/price_model.joblib")
    parser.add_argument("--metrics-path", default="artifacts/price_model_metrics.json")
    args = parser.parse_args()

    # Start Prometheus metrics server
    prometheus_port = int(os.environ.get("PROMETHEUS_METRICS_PORT", 8001))
    start_prometheus_server(prometheus_port)

    if args.input_json:
        records = load_records_from_json(args.input_json)
        logger.info("Loaded %s records from %s", len(records), args.input_json)
    else:
        records = load_records_from_mongo(args.mongo_uri, args.mongo_db, args.collection)
        logger.info("Loaded %s records from MongoDB %s.%s", len(records), args.mongo_db, args.collection)

    start_time = time.time()
    trainer = RealEstatePriceModel()
    result = trainer.train(records, model_path=args.model_path, metrics_path=args.metrics_path)
    train_duration = time.time() - start_time
    
    # Update Prometheus metrics
    model_train_duration.observe(train_duration)
    update_metrics_from_result(result)
    
    logger.info("Training complete with %s samples in %.2fs", result.sample_count, train_duration)
    logger.info("Model saved to %s", result.model_path)
    logger.info("Metrics: %s", result.metrics)


if __name__ == "__main__":
    main()
