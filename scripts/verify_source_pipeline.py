"""Real Kafka/Mongo smoke with fabricated HTML fixtures, NEVER real-source data.

Uses existing stress topic/database. Does not call a website or external LLM,
reset offsets, delete data, or enable any Agent. Run with processors running.
"""
from pathlib import Path
import copy
import json
import os
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from confluent_kafka import Producer
from pymongo import MongoClient
from scraper.sources import get_adapter
from scraper.kafka_producer import publish_records
from agents.safety import real_records
from modeling.price_model import build_feature_frame


def main():
    run_id = "source-contract-" + uuid4().hex[:12]
    fixtures = ROOT / "utils/tests/fixtures/sources"
    urls = {"alonhadat": "https://alonhadat.com.vn/nha-kiem-thu-101.html",
            "homedy": "https://homedy.com/ban-nha-rieng-ha-noi/nha-kiem-thu-es101",
            "guland": "https://guland.vn/post/dat-kiem-thu-101"}
    records = []
    for source, url in urls.items():
        row = get_adapter(source).parse((fixtures / f"{source}-detail.html").read_text(encoding="utf-8"), url)
        row.update(url=f"https://synthetic.invalid/{run_id}/{source}", is_synthetic=True,
                   source_type="synthetic", generated_by="stress_agent", run_id=run_id)
        records.append(row)
    bad = copy.deepcopy(records[0])
    bad.update(url=f"https://synthetic.invalid/{run_id}/bad-units", area_raw="4 x 20 m", price_raw="50 triệu/m ngang")
    records += [bad, copy.deepcopy(records[0])]
    producer = Producer({"bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092,kafka2:29093,kafka3:29094"),
                         "enable.idempotence": True, "acks": "all", "delivery.timeout.ms": 10000})
    acknowledged = publish_records(producer, iter(records), "real_estate_stress_raw", 10)
    report = {"run_id": run_id, "kind": "fabricated fixtures, real Kafka/Mongo, no live crawling", "acknowledged": acknowledged}
    with MongoClient(os.environ.get("MONGO_URI", "mongodb://mongodb:27017/"), serverSelectionTimeoutMS=5000) as client:
        db = client[os.environ.get("MONGO_STRESS_DB", "real_estate_stress_db")]
        query = {"run_id": run_id}
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            features = list(db.training_features.find(query, {"_id": 0}))
            invalid = list(db.invalid_records.find(query, {"_id": 0}))
            if len(features) == 2 and len(invalid) == 2:
                break
            time.sleep(.5)
        else:
            raise RuntimeError("Expected 2 valid and 2 quarantined fixture records; inspect processors")
        assert db.listings_raw.count_documents(query) == 4, "Duplicate URL was not idempotent"
        assert real_records(features) == [], "Synthetic fixtures reached training eligibility"
        for row in features:
            assert row["price_vnd"] == 2.5e9 and row["area_m2"] == 80
            assert row["source_listing_id"] == "101" and row["source_url"] == urls[row["source"]]
            assert not row["is_model_candidate"] and row["is_synthetic"]
        assert build_feature_frame(features).shape[0] == 2  # preparation only, not training
        primary = client[os.environ.get("MONGO_DB", "real_estate_db")]
        assert all(primary[name].count_documents(query) == 0 for name in ("listings_raw", "training_features", "invalid_records"))
        report.update(status="passed", valid=2, invalid=2, raw_unique=4, primary_writes=0,
                      sources={source:{"valid":sum(r["source"]==source for r in features),
                                       "invalid":sum(r["source"]==source for r in invalid)} for source in urls})
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
