from __future__ import annotations

import argparse
import json
from pathlib import Path

from pymongo import MongoClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a dataset quality report from MongoDB training features.")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017/")
    parser.add_argument("--mongo-db", default="real_estate_db")
    parser.add_argument("--collection", default="training_features")
    parser.add_argument("--output", type=Path, default=Path("artifacts") / "dataset_quality_report.json")
    args = parser.parse_args()

    client = MongoClient(args.mongo_uri)
    collection = client[args.mongo_db][args.collection]
    try:
        total = collection.count_documents({})
        candidates = collection.count_documents({"is_model_candidate": True})
        with_price = collection.count_documents({"price_vnd": {"$gt": 0}})
        by_type = list(
            collection.aggregate(
                [
                    {"$group": {"_id": "$property_type", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ]
            )
        )
        by_province = list(
            collection.aggregate(
                [
                    {"$group": {"_id": "$province_slug", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                    {"$limit": 20},
                ]
            )
        )
    finally:
        client.close()

    report = {
        "total_records": total,
        "model_candidates": candidates,
        "records_with_target_price": with_price,
        "candidate_ratio": (candidates / total) if total else 0,
        "price_ratio": (with_price / total) if total else 0,
        "property_type_distribution": by_type,
        "top_provinces": by_province,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote quality report to {args.output}")


if __name__ == "__main__":
    main()
