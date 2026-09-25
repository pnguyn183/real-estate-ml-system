"""Summarize auditable AI before/after records from MongoDB.

Run after an AI extraction workload:
    python scripts/audit_gemini_clean.py --output runtime/research/gemini-clean-summary.json

This summary is not a substitute for the wire-level verify_gemini_pipeline
artifact. Older cleaning audits without final_outcome remain unverified here.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient


def summarize(records: list[dict], provider: str | None = None) -> dict:
    selected = [record for record in records if provider is None or record.get("provider") == provider]
    status_counts = Counter(record.get("status", "unknown") for record in selected)
    outcome_counts = Counter(record.get("final_outcome", "unknown") for record in selected)
    applied = [record for record in selected
               if record.get("final_outcome") == "success" and record.get("applied") is True]
    changed_counts = Counter(field for record in selected for field in record.get("changed_fields", []))
    changed = sum(bool(record.get("changed_fields")) for record in selected)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_filter": provider,
        "records": len(selected),
        "status_counts": dict(sorted(status_counts.items())),
        "final_outcome_counts": dict(sorted(outcome_counts.items())),
        "records_applied": len(applied),
        "records_without_final_outcome": outcome_counts.get("unknown", 0),
        "applied_changed_fields": dict(Counter(field for record in applied
                                              for field in record.get("changed_fields", [])).most_common()),
        "records_with_changes": changed,
        "records_without_changes": len(selected) - changed,
        "changed_fields": dict(changed_counts.most_common()),
        "providers_models": sorted({
            f"{record.get('provider') or 'unknown'}:{record.get('model') or 'unknown'}"
            for record in selected
        }),
        "records_detail": [
            {
                "event_id": record.get("event_id"),
                "url": record.get("url"),
                "status": record.get("status"),
                "final_outcome": record.get("final_outcome", "unknown"),
                "applied": record.get("applied"),
                "final_storage_collection": record.get("final_storage_collection"),
                "provider": record.get("provider"),
                "model": record.get("model"),
                "changed_fields": record.get("changed_fields", []),
                "before_sha256": record.get("before_sha256"),
                "after_sha256": record.get("after_sha256"),
            }
            for record in selected
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", default=None, help="Optional provider name filter, e.g. openai_compatible")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))
    parser.add_argument("--mongo-db", default=os.environ.get("MONGO_DB", "real_estate_db"))
    args = parser.parse_args()
    with MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000) as client:
        records = list(client[args.mongo_db]["ai_cleaning_audit"].find({}, {"_id": 0}))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summarize(records, args.provider), indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in summarize(records, args.provider).items() if key != "records_detail"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
