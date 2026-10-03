"""Shared synthetic-data boundary for ingestion and every training entry point."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

SYNTHETIC_SOURCES = ("synthetic", "stress_agent", "stress", "test")
SYNTHETIC_URL_PREFIX = "https://synthetic.invalid/"


def is_synthetic_record(record: Mapping[str, Any]) -> bool:
    return (
        record.get("is_synthetic") in (True, "true", "True", 1)
        or record.get("source_type") in SYNTHETIC_SOURCES
        or record.get("generated_by") == "stress_agent"
        or str(record.get("url") or "").startswith(SYNTHETIC_URL_PREFIX)
    )


def real_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fail closed for tagged synthetic records, including JSON/manual training."""
    return [record for record in records if not is_synthetic_record(record)
            and not record.get("training_excluded", False)
            and ("schema_version" not in record or
                 (record.get("schema_version") == 2 and record.get("is_model_candidate") is True
                  and not record.get("validation_errors")))]


def real_data_query(query: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compose with existing anomaly/candidate predicates without replacing them."""
    return {"$and": [query or {}, {
        "is_synthetic": {"$nin": [True, "true", "True", 1]},
        "source_type": {"$nin": list(SYNTHETIC_SOURCES)},
        "generated_by": {"$ne": "stress_agent"},
        "training_excluded": {"$ne": True},
        "$or": [{"schema_version": {"$exists": False}},
                {"schema_version": 2, "is_model_candidate": True, "validation_errors": {"$in": [[], None]}}],
        "url": {"$not": {"$regex": r"^https://synthetic\.invalid/"}},
    }]}
