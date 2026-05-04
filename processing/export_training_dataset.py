from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from pymongo import MongoClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Export model candidate records from MongoDB to a training dataset file.")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017/")
    parser.add_argument("--mongo-db", default="real_estate_db")
    parser.add_argument("--collection", default="training_features")
    parser.add_argument("--output", type=Path, default=Path("artifacts") / "training_dataset.parquet")
    parser.add_argument("--format", choices=["parquet", "csv", "jsonl"], default="parquet")
    parser.add_argument("--only-candidates", action="store_true", default=True)
    args = parser.parse_args()

    client = MongoClient(args.mongo_uri)
    try:
        query = {"is_model_candidate": True} if args.only_candidates else {}
        records = list(client[args.mongo_db][args.collection].find(query, {"_id": 0}))
    finally:
        client.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(records)

    if args.format == "parquet":
        frame.to_parquet(args.output, index=False)
    elif args.format == "csv":
        frame.to_csv(args.output, index=False, encoding="utf-8-sig")
    else:
        with args.output.open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Exported {len(records)} records to {args.output}")


if __name__ == "__main__":
    main()
