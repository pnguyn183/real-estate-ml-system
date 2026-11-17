"""Synthetic unit fixtures verify metric math; they are not extraction evidence."""
import json
import hashlib

import pytest

from research.extraction_evaluation import evaluate_extraction


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def annotation(record_id, price, area):
    source_text = f"Giá {price} đồng, diện tích {area} m2"
    return {
        "record_id": record_id,
        "annotation_status": "adjudicated",
        "reviewer_ids": ["unit-reviewer-a", "unit-reviewer-b"],
        "adjudicator_id": "unit-adjudicator",
        "source_text": source_text,
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "ground_truth": {"price_vnd": price, "area_m2": area, "bedroom_count": None},
        "numeric_tolerances": {"price_vnd": {"relative": 0.01}, "area_m2": {"absolute": 0.5}},
    }


def prediction(record_id, price, area, *, status="success", attempts=1):
    row = {"record_id": record_id, "status": status, "attempts": attempts, "latency_seconds": 2.0}
    if status == "success":
        row["output"] = {
            "fields": {"price_vnd": price, "area_m2": area},
            "confidence": 0.9,
            "evidence": {"price_vnd": f"Giá {price} đồng", "area_m2": f"diện tích {area} m2"},
        }
    return row


def test_evaluation_reports_field_quality_operations_and_tolerance(tmp_path):
    annotations = tmp_path / "annotations.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    write_jsonl(annotations, [annotation("one", 1000, 50), annotation("two", 2000, 60)])
    rows = [prediction("one", 1005, 50.4, attempts=2), prediction("two", 1000, 60)]
    rows[0]["output"]["evidence"]["price_vnd"] = "Giá 1000 đồng"
    rows[0]["output"]["evidence"]["area_m2"] = "diện tích 50 m2"
    rows[1]["output"]["evidence"]["price_vnd"] = "Giá 2000 đồng"
    write_jsonl(predictions, rows)
    result = evaluate_extraction(annotations, predictions)
    assert result["dataset"]["annotation_count"] == 2
    assert result["quality"]["micro_precision"] == pytest.approx(3 / 4)
    assert result["quality"]["micro_recall"] == pytest.approx(3 / 4)
    assert result["quality"]["numeric_tolerance_accuracy"] == pytest.approx(3 / 4)
    assert result["quality"]["record_exact_match_rate"] == pytest.approx(0.5)
    assert result["quality"]["evidence_validity_rate"] == pytest.approx(1.0)
    assert result["operations"]["processing_success_rate"] == 1.0
    assert result["operations"]["retry_record_rate"] == pytest.approx(0.5)


def test_non_adjudicated_labels_are_rejected(tmp_path):
    annotations = tmp_path / "annotations.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    row = annotation("one", 1000, 50)
    row["annotation_status"] = "draft"
    write_jsonl(annotations, [row])
    write_jsonl(predictions, [prediction("one", 1000, 50)])
    with pytest.raises(ValueError, match="adjudicated"):
        evaluate_extraction(annotations, predictions)


def test_invalid_ground_truth_or_source_hash_is_rejected(tmp_path):
    annotations = tmp_path / "annotations.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    row = annotation("one", 1000, 50)
    row["ground_truth"]["property_type"] = "not-a-schema-value"
    write_jsonl(annotations, [row])
    write_jsonl(predictions, [prediction("one", 1000, 50)])
    with pytest.raises(ValueError, match="ground_truth violates extraction schema"):
        evaluate_extraction(annotations, predictions)
    row = annotation("one", 1000, 50)
    row["source_sha256"] = "0" * 64
    write_jsonl(annotations, [row])
    with pytest.raises(ValueError, match="source_sha256"):
        evaluate_extraction(annotations, predictions)


def test_invalid_success_output_counts_as_failure(tmp_path):
    annotations = tmp_path / "annotations.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    write_jsonl(annotations, [annotation("one", 1000, 50)])
    write_jsonl(predictions, [{"record_id": "one", "status": "success", "output": {"fields": {}}}])
    result = evaluate_extraction(annotations, predictions)
    assert result["operations"]["invalid_output_rate"] == 1.0
    assert result["operations"]["processing_success_rate"] == 0.0
    assert result["quality"]["micro_recall"] == 0.0


def test_false_positive_does_not_make_completeness_exceed_one(tmp_path):
    annotations = tmp_path / "annotations.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    truth = annotation("one", 1000, 50)
    truth["source_text"] += ", 2 phòng ngủ"
    truth["source_sha256"] = hashlib.sha256(truth["source_text"].encode("utf-8")).hexdigest()
    output = prediction("one", 1000, 50)
    output["output"]["fields"]["bedroom_count"] = 2
    output["output"]["evidence"]["bedroom_count"] = "2 phòng ngủ"
    write_jsonl(annotations, [truth])
    write_jsonl(predictions, [output])
    result = evaluate_extraction(annotations, predictions)
    assert result["quality"]["completeness"] == 1.0


def test_unmatched_predictions_do_not_bias_operational_rates(tmp_path):
    annotations = tmp_path / "annotations.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    write_jsonl(annotations, [annotation("one", 1000, 50)])
    write_jsonl(predictions, [prediction("one", 1000, 50), {"record_id": "extra", "status": "unknown"}])
    result = evaluate_extraction(annotations, predictions)
    assert result["dataset"]["unmatched_prediction_count"] == 1
    assert result["operations"]["invalid_output_rate"] == 0.0
    assert result["operations"]["retry_record_rate"] == 0.0
