#!/usr/bin/env python
import os
import time
import logging
import sys
from datetime import datetime
from pathlib import Path

from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modeling.price_model import RealEstatePriceModel
from utils.metrics import (
    model_train_duration,
    start_prometheus_server,
    trainer_last_run_timestamp,
    trainer_last_success_timestamp,
    trainer_runs,
    trainer_runs_failed,
    update_metrics_from_result,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TRAIN_INTERVAL = int(os.environ.get("TRAIN_INTERVAL", 21600))  # 6 hours default
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB = os.environ.get("MONGO_DB", "real_estate_db")
MONGO_FEATURE_COLLECTION = os.environ.get("MONGO_FEATURE_COLLECTION", "training_features")
MIN_RECORDS = int(os.environ.get("MIN_RECORDS_FOR_TRAINING", 500))
MODEL_PATH = os.environ.get("MODEL_PATH", "artifacts/models/price_model.joblib")
METRICS_PATH = os.environ.get("METRICS_PATH", "artifacts/price_model_metrics.json")

def check_training_data():
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB]
        count = db["training_features"].count_documents({"is_model_candidate": True, "has_target_price": True})
        logger.info(f"Found {count} training candidates")
        return count >= MIN_RECORDS
    except Exception as exc:
        logger.error(f"Error checking training data: {exc}")
        return False
    finally:
        try:
            client.close()
        except:
            pass

def run_trainer():
    trainer_runs.inc()
    trainer_last_run_timestamp.set(time.time())
    start_time = time.time()
    try:
        logger.info("Starting trainer...")
        client = MongoClient(MONGO_URI)
        try:
            records = list(
                client[MONGO_DB][MONGO_FEATURE_COLLECTION].find(
                    {"price_vnd": {"$gt": 0}, "is_model_candidate": True},
                    {"_id": 0},
                )
            )
        finally:
            client.close()

        model = RealEstatePriceModel()
        result = model.train(records, model_path=MODEL_PATH, metrics_path=METRICS_PATH)
        train_duration = time.time() - start_time
        model_train_duration.observe(train_duration)
        update_metrics_from_result(result)
        trainer_last_success_timestamp.set(time.time())
        logger.info("Training completed successfully: samples=%s duration=%.2fs", result.sample_count, train_duration)
    except Exception as exc:
        trainer_runs_failed.inc()
        logger.error(f"Training error: {exc}")

def main():
    prometheus_port = int(os.environ.get("PROMETHEUS_METRICS_PORT", 8001))
    start_prometheus_server(prometheus_port)
    logger.info(f"Auto-trainer started, interval={TRAIN_INTERVAL}s, min_records={MIN_RECORDS}")
    while True:
        try:
            if check_training_data():
                run_trainer()
            else:
                logger.info(f"Not enough training data (need {MIN_RECORDS}), skipping training")
            logger.info(f"Next training in {TRAIN_INTERVAL}s (at {datetime.fromtimestamp(time.time() + TRAIN_INTERVAL)})")
            time.sleep(TRAIN_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Auto-trainer stopped")
            break
        except Exception as exc:
            logger.error(f"Unexpected error: {exc}")
            time.sleep(60)

if __name__ == "__main__":
    main()
