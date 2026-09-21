"""Read-only Mongo snapshot and quantitative audit; never repairs source records.

Run: python -m research.data_audit --output runtime/research/data-audit
Snapshots retain Mongo cursor order so the legacy random split is reproducible.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pymongo import MongoClient

NUMERIC = ["price_vnd", "area_m2", "price_per_m2_vnd", "bedroom_count", "bathroom_count", "floor_count", "front_width_m", "road_width_m"]


def json_value(value: Any):
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [json_value(v) for v in value]
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def write_json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_value(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, float) and np.isnan(value))


def parse_utc_timestamp(value):
    """Support collector epoch seconds and source ISO timestamps explicitly."""
    if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        return pd.to_datetime(value, unit="s", utc=True, errors="coerce")
    return pd.to_datetime(value, utc=True, errors="coerce")


def profile_records(records: list[dict], numeric_fields=None) -> dict:
    columns = sorted(set().union(*(r.keys() for r in records))) if records else []
    report = {"row_count": len(records), "column_count": len(columns), "columns": {}, "numeric": {}, "duplicates": {}}
    for column in columns:
        values = [r.get(column) for r in records]
        present = [v for v in values if not missing(v)]
        encoded = pd.Series([json.dumps(json_value(v), sort_keys=True, ensure_ascii=False) for v in present], dtype="str")
        counts = encoded.value_counts()
        info = {"types": sorted({type(v).__name__ for v in present}), "missing_count": len(values) - len(present),
                "missing_fraction": (len(values) - len(present)) / len(values), "distinct_present": len(counts),
                "constant_present": len(counts) <= 1, "dominant_present_fraction": float(counts.iloc[0] / len(present)) if present else None}
        if len(counts) <= 30 or column in ("property_type", "listing_type", "source", "province_slug", "district_slug"):
            info["top_values"] = counts.head(15).to_dict()
        report["columns"][column] = info
    for column in NUMERIC if numeric_fields is None else numeric_fields:
        if column not in columns:
            continue
        raw = pd.Series([r.get(column) for r in records])
        numeric = pd.to_numeric(raw, errors="coerce")
        valid = numeric[np.isfinite(numeric)]
        q1, q3 = valid.quantile([.25, .75]) if len(valid) else (np.nan, np.nan)
        lower, upper = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        report["numeric"][column] = {
            "finite_count": len(valid), "nonfinite_count": int(np.isinf(numeric).sum()),
            "unparseable_present_count": sum(not missing(a) and pd.isna(b) for a, b in zip(raw, numeric)),
            "nonpositive_count": int((valid <= 0).sum()), "skewness": float(valid.skew()),
            "quantiles": valid.quantile([0, .01, .1, .25, .5, .75, .9, .99, 1]).to_dict(),
            "iqr_lower": lower, "iqr_upper": upper,
            "iqr_outlier_count": int(((valid < lower) | (valid > upper)).sum()),
        }
    for column in ("url", "listing_fingerprint", "title", "description", "text_features", "text_content_hash"):
        if column in columns:
            values = [r[column] for r in records if not missing(r.get(column))]
            report["duplicates"][column] = len(values) - len(set(values))
    price_pattern = r"(?i)\b(?:tỷ|tỉ|triệu|ty|trieu)\b"
    report["price_unit_tokens_in_text"] = {column: int(pd.Series([r.get(column) or "" for r in records], dtype="str").str.contains(price_pattern, regex=True).sum()) for column in ("title", "description", "text_features") if column in columns}
    for column in ("scraped_at", "updated_at", "timestamp"):
        if column in columns:
            stamps = pd.to_datetime(pd.Series([r.get(column) for r in records]).map(parse_utc_timestamp), utc=True, errors="coerce")
            report.setdefault("timestamps", {})[column] = {"valid_count": int(stamps.notna().sum()), "min": str(stamps.min()), "max": str(stamps.max())}
    if records and "price_vnd" in columns:
        from processing.kafka_to_mongo import validate_normalized_record
        reasons: dict[str, int] = {}
        for record in records:
            _, errors = validate_normalized_record(record)
            for error in errors:
                reasons[error] = reasons.get(error, 0) + 1
        report["deterministic_validation_errors"] = reasons
    return report


def plots(records: list[dict], report: dict, output: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for column in report["numeric"]:
        values = pd.to_numeric(pd.Series([r.get(column) for r in records]), errors="coerce")
        values = values[np.isfinite(values)]
        if values.empty:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(11, 4))
        axes[0].hist(values, bins=50)
        axes[0].set(title=f"{column}: full observed distribution", ylabel="Records", xlabel=column)
        axes[1].boxplot(values, orientation="horizontal", showfliers=True)
        axes[1].set(title="1.5 IQR boxplot (outliers retained)", xlabel=column)
        fig.tight_layout()
        path = output / f"{column}_distribution.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(str(path))
    fields = [k for k in list(report["numeric"]) + ["property_type", "province_slug", "district_slug", "ward_slug", "latitude", "longitude", "project_hint"] if k in report["columns"]]
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(fields, [report["columns"][k]["missing_fraction"] * 100 for k in fields])
    ax.set(xlabel="Missing (%)", title="Observed feature missingness", xlim=(0, 100))
    fig.tight_layout()
    path = output / "missing_values.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(str(path))
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017/")
    parser.add_argument("--mongo-db", default="real_estate_db")
    parser.add_argument("--output", type=Path, default=Path("runtime/research/data-audit"))
    parser.add_argument("--input-jsonl", type=Path, help="Re-audit an existing snapshot without MongoDB")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.input_jsonl:
        records = [json.loads(line) for line in args.input_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
        collections, candidate_records = {}, None
    else:
        from scripts.auto_train import training_query
        with MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            db = client[args.mongo_db]
            collections = {name: db[name].count_documents({}) for name in db.list_collection_names()}
            records = list(db.training_features.find({}, {"_id": 0}))
            # Mongo is read-only here; the two cursors can differ if live ingestion is active.
            candidate_records = list(db.training_features.find(training_query(), {"_id": 0}))
    snapshots = {}
    for name, rows in (("features", records), ("candidates", candidate_records)):
        if rows is None:
            continue
        path = args.output / f"{name}.jsonl"
        if path.exists():
            raise SystemExit(f"Refusing to replace snapshot {path}; choose a fresh output directory")
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(json_value(row), ensure_ascii=False, allow_nan=False) + "\n")
        snapshots[name] = {"path": str(path), "rows": len(rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    report = profile_records(records)
    report.update(captured_at=datetime.now(timezone.utc).isoformat(), collection_counts=collections, snapshots=snapshots,
                  snapshot_consistency="Read-only sequential cursors, not a transactional database snapshot; active ingestion can change counts.",
                  interpretation="Price diagnostic only. IQR flags are statistical extremes, not proof of invalidity. No raw records were removed or repaired.")
    if candidate_records is not None:
        report["training_candidates"] = profile_records(candidate_records)
    report["charts"] = plots(records, report, args.output / "charts")
    write_json(args.output / "audit.json", report)
    print(json.dumps({"output": str(args.output), "rows": len(records), "candidate_rows": len(candidate_records) if candidate_records is not None else None, "charts": len(report["charts"])}))


if __name__ == "__main__":
    main()
