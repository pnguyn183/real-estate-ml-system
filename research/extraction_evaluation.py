"""Evaluate extraction predictions against separately adjudicated human labels.

This module never calls a model and never derives ground truth from predictions.
Both inputs are explicit JSONL files so evaluation provenance can be reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from agents.extraction import ExtractionFields, ExtractionOutput, FIELD_TO_RAW, location_matches_evidence


KNOWN_FIELDS = set(ExtractionFields.model_fields)
NUMERIC_FIELDS = {
    "price_vnd", "area_m2", "bedroom_count", "bathroom_count", "floor_count",
    "front_width_m", "road_width_m",
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from None
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: each JSONL row must be an object")
        records.append(value)
    if not records:
        raise ValueError(f"{path}: no records")
    return records


def _annotations(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(_jsonl(path), 1):
        record_id = row.get("record_id")
        truth = row.get("ground_truth")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"annotation row {index}: non-empty record_id required")
        if record_id in result:
            raise ValueError(f"annotation row {index}: duplicate record_id {record_id}")
        if row.get("annotation_status") != "adjudicated":
            raise ValueError(f"annotation row {index}: annotation_status must be adjudicated")
        reviewers = row.get("reviewer_ids")
        if not isinstance(reviewers, list) or len(reviewers) < 2 or any(not isinstance(value, str) or not value.strip() for value in reviewers):
            raise ValueError(f"annotation row {index}: at least two distinct reviewer_ids required")
        if len(set(reviewers)) < 2:
            raise ValueError(f"annotation row {index}: at least two distinct reviewer_ids required")
        if not isinstance(row.get("adjudicator_id"), str) or not row["adjudicator_id"].strip():
            raise ValueError(f"annotation row {index}: adjudicator_id required")
        source_text = row.get("source_text")
        if not isinstance(source_text, str) or not source_text.strip():
            raise ValueError(f"annotation row {index}: source_text required")
        expected_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        if row.get("source_sha256") != expected_hash:
            raise ValueError(f"annotation row {index}: source_sha256 does not match source_text")
        if not isinstance(truth, dict) or not truth:
            raise ValueError(f"annotation row {index}: non-empty ground_truth required")
        unknown = set(truth) - KNOWN_FIELDS
        if unknown:
            raise ValueError(f"annotation row {index}: unknown ground-truth fields {sorted(unknown)}")
        try:
            ExtractionFields.model_validate(truth)
        except ValueError as exc:
            raise ValueError(f"annotation row {index}: ground_truth violates extraction schema: {exc}") from None
        tolerances = row.get("numeric_tolerances", {})
        if not isinstance(tolerances, dict) or set(tolerances) - NUMERIC_FIELDS:
            raise ValueError(f"annotation row {index}: invalid numeric_tolerances")
        for field, tolerance in tolerances.items():
            if not isinstance(tolerance, dict):
                raise ValueError(f"annotation row {index}: tolerance for {field} must be an object")
            for key in ("absolute", "relative"):
                value = tolerance.get(key, 0)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"annotation row {index}: invalid {key} tolerance for {field}")
        result[record_id] = row
    return result


def _predictions(path: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    result: dict[str, dict[str, Any]] = {}
    invalid_ids: set[str] = set()
    for index, row in enumerate(_jsonl(path), 1):
        record_id = row.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"prediction row {index}: non-empty record_id required")
        if record_id in result:
            raise ValueError(f"prediction row {index}: duplicate record_id {record_id}")
        normalized = dict(row)
        normalized["validated_output"] = None
        if row.get("status") == "success":
            try:
                output = ExtractionOutput.model_validate(row.get("output"))
                if set(output.evidence) - set(FIELD_TO_RAW):
                    raise ValueError("unknown evidence field")
                normalized["validated_output"] = output
            except (ValueError, TypeError):
                invalid_ids.add(record_id)
        elif row.get("status") != "failed":
            invalid_ids.add(record_id)
        result[record_id] = normalized
    return result, invalid_ids


def _matches(field: str, truth: Any, predicted: Any, tolerance: dict[str, float] | None) -> bool:
    if field in NUMERIC_FIELDS:
        if isinstance(truth, bool) or isinstance(predicted, bool):
            return False
        if not isinstance(truth, (int, float)) or not isinstance(predicted, (int, float)):
            return False
        if not math.isfinite(float(truth)) or not math.isfinite(float(predicted)):
            return False
        tolerance = tolerance or {}
        allowed = max(float(tolerance.get("absolute", 0)), abs(float(truth)) * float(tolerance.get("relative", 0)))
        return abs(float(truth) - float(predicted)) <= allowed
    return isinstance(truth, str) and isinstance(predicted, str) and truth.strip().casefold() == predicted.strip().casefold()


def _safe_ratio(numerator: int | float, denominator: int | float) -> float | None:
    return float(numerator / denominator) if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate_extraction(annotations_path: Path, predictions_path: Path) -> dict[str, Any]:
    annotations = _annotations(annotations_path)
    predictions, invalid_ids = _predictions(predictions_path)
    counts = {field: {"tp": 0, "fp": 0, "fn": 0, "truth_present": 0, "predicted_present": 0} for field in KNOWN_FIELDS}
    exact_records = successful = retried = failures = 0
    numeric_correct = numeric_total = evidence_valid = evidence_total = covered_truth_present = 0
    latencies: list[float] = []

    for record_id, annotation in annotations.items():
        prediction = predictions.get(record_id)
        if prediction is None:
            failures += 1
            for field, truth in annotation["ground_truth"].items():
                if truth is not None:
                    counts[field]["fn"] += 1
                    counts[field]["truth_present"] += 1
                    numeric_total += int(field in NUMERIC_FIELDS)
            continue
        attempts = prediction.get("attempts", 0)
        if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts > 1:
            retried += 1
        latency = prediction.get("latency_seconds")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(latency) and latency >= 0:
            latencies.append(float(latency))
        output: ExtractionOutput | None = prediction["validated_output"]
        if prediction.get("status") != "success" or output is None:
            failures += 1
            for field, truth in annotation["ground_truth"].items():
                if truth is not None:
                    counts[field]["fn"] += 1
                    counts[field]["truth_present"] += 1
                    numeric_total += int(field in NUMERIC_FIELDS)
            continue
        successful += 1
        predicted_fields = output.fields.model_dump()
        record_exact = True
        for field, truth in annotation["ground_truth"].items():
            predicted = predicted_fields.get(field)
            truth_present = truth is not None
            predicted_present = predicted is not None
            counts[field]["truth_present"] += int(truth_present)
            counts[field]["predicted_present"] += int(predicted_present)
            tolerance = annotation.get("numeric_tolerances", {}).get(field)
            correct = truth_present and predicted_present and _matches(field, truth, predicted, tolerance)
            if correct:
                counts[field]["tp"] += 1
            elif truth_present and predicted_present:
                counts[field]["fp"] += 1
                counts[field]["fn"] += 1
                record_exact = False
            elif truth_present:
                counts[field]["fn"] += 1
                record_exact = False
            elif predicted_present:
                counts[field]["fp"] += 1
                record_exact = False
            if field in NUMERIC_FIELDS and truth_present:
                numeric_total += 1
                numeric_correct += int(correct)
            covered_truth_present += int(truth_present and predicted_present)
        for field, predicted in predicted_fields.items():
            if predicted is None:
                continue
            evidence_total += 1
            quote = output.evidence.get(field)
            quote_valid = isinstance(quote, str) and quote.casefold() in annotation["source_text"].casefold()
            if quote_valid and field in {"province", "district", "address"}:
                quote_valid = location_matches_evidence(str(predicted), quote, field)
            evidence_valid += int(quote_valid)
        exact_records += int(record_exact)

    tp = sum(value["tp"] for value in counts.values())
    fp = sum(value["fp"] for value in counts.values())
    fn = sum(value["fn"] for value in counts.values())
    truth_present = sum(value["truth_present"] for value in counts.values())
    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    per_field = {}
    for field, value in sorted(counts.items()):
        if not (value["truth_present"] or value["predicted_present"]):
            continue
        field_precision = _safe_ratio(value["tp"], value["tp"] + value["fp"])
        field_recall = _safe_ratio(value["tp"], value["tp"] + value["fn"])
        field_f1 = 2 * field_precision * field_recall / (field_precision + field_recall) if field_precision is not None and field_recall is not None and field_precision + field_recall else None
        per_field[field] = {**value, "precision": field_precision, "recall": field_recall, "f1": field_f1}

    annotation_count = len(annotations)
    matched_ids = set(annotations) & set(predictions)
    matched_prediction_count = len(matched_ids)
    return {
        "status": "measured_from_human_ground_truth",
        "dataset": {
            "annotation_count": annotation_count,
            "prediction_count": len(predictions),
            "matched_count": matched_prediction_count,
            "missing_prediction_count": len(set(annotations) - set(predictions)),
            "unmatched_prediction_count": len(set(predictions) - set(annotations)),
        },
        "quality": {
            "micro_precision": precision,
            "micro_recall": recall,
            "micro_f1": f1,
            "record_exact_match_rate": _safe_ratio(exact_records, annotation_count),
            "completeness": _safe_ratio(covered_truth_present, truth_present),
            "numeric_tolerance_accuracy": _safe_ratio(numeric_correct, numeric_total),
            "evidence_validity_rate": _safe_ratio(evidence_valid, evidence_total),
            "per_field": per_field,
        },
        "operations": {
            "processing_success_rate": _safe_ratio(successful, annotation_count),
            "failure_rate": _safe_ratio(failures, annotation_count),
            "invalid_output_rate": _safe_ratio(len(invalid_ids & matched_ids), matched_prediction_count),
            "retry_record_rate": _safe_ratio(retried, matched_prediction_count),
            "latency_seconds_mean": mean(latencies) if latencies else None,
            "latency_seconds_p95": _percentile(latencies, .95),
            "latency_sample_count": len(latencies),
        },
        "definitions": {
            "field_mismatch": "counts as one false positive and one false negative",
            "completeness": "predicted non-null annotated fields / non-null ground-truth fields",
            "evidence_validity": "literal case-insensitive source quote; location fields also use production location matching",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate extraction against adjudicated human ground truth")
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = evaluate_extraction(args.annotations, args.predictions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
