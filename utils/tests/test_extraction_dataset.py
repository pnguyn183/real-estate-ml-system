"""Dataset workflow tests use synthetic rows and do not provide accuracy evidence."""
import json

import pytest

from research.extraction_dataset import finalize_annotations, prepare_dataset


def write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def raw_row(listing_id, *, synthetic, title="Nhà 2 phòng ngủ", source="source-a"):
    return {
        "listing_id": listing_id,
        "source": source,
        "property_type": "house",
        "title": title,
        "description": "Giá 2 tỷ, diện tích 50 m2",
        "is_synthetic": synthetic,
    }


def test_prepare_uses_only_unique_explicit_real_rows_and_blinds_predictions(tmp_path):
    source = tmp_path / "source.jsonl"
    output = tmp_path / "packet"
    write_jsonl(source, [
        raw_row("real", synthetic=False),
        raw_row("duplicate-text", synthetic=False),
        raw_row("unknown", synthetic=None, title="Căn hộ 60 m2"),
        raw_row("synthetic", synthetic=True, title="Đất 100 m2"),
    ])
    manifest = prepare_dataset(
        source,
        output,
        dataset_id="unit-panel",
        reviewer_ids=("reviewer-one", "reviewer-two"),
    )
    candidates = read_jsonl(output / "candidates.jsonl")
    first = read_jsonl(output / "reviewer-a.jsonl")
    second = read_jsonl(output / "reviewer-b.jsonl")
    assert len(candidates) == 1
    assert candidates[0]["data_origin"] == "real_listing_explicit_flag"
    assert first[0]["reviewer_id"] == "reviewer-one"
    assert second[0]["reviewer_id"] == "reviewer-two"
    assert "prediction" not in json.dumps(first + second)
    assert manifest["input"]["provenance_counts"] == {
        "explicit_real": 2,
        "synthetic": 1,
        "unknown": 1,
    }
    assert manifest["selection"]["excluded_duplicate_text_count"] == 1
    assert manifest["annotation"]["accuracy_claim_allowed"] is False
    assert set(manifest["annotation"]["packet_sha256"]) == {
        "candidates.jsonl", "reviewer-a.jsonl", "reviewer-b.jsonl", "adjudication.jsonl"
    }


def test_prepare_redacts_contact_details_but_preserves_origin_hash(tmp_path):
    source = tmp_path / "source.jsonl"
    output = tmp_path / "packet"
    row = raw_row(
        "real",
        synthetic=False,
        title="Căn hộ bán nhanh 0918.722310 owner@example.com",
    )
    row["description"] = "Diện tích 50 m2. Liên hệ chị Lan 0909 123 456"
    write_jsonl(source, [row])
    prepare_dataset(source, output, dataset_id="unit-panel", reviewer_ids=("one", "two"))
    candidate = read_jsonl(output / "candidates.jsonl")[0]
    assert "0918.722310" not in candidate["source_text"]
    assert "owner@example.com" not in candidate["source_text"]
    assert "[REDACTED_PHONE]" in candidate["source_text"]
    assert "[REDACTED_EMAIL]" in candidate["source_text"]
    assert "chị Lan" not in candidate["source_text"]
    assert candidate["record_id"] != "source-a:real"
    assert candidate["source_record_id_sha256"]
    assert candidate["original_source_sha256"] != candidate["source_sha256"]


def _complete_packet(output):
    candidates = read_jsonl(output / "candidates.jsonl")
    first = read_jsonl(output / "reviewer-a.jsonl")
    second = read_jsonl(output / "reviewer-b.jsonl")
    adjudication = read_jsonl(output / "adjudication.jsonl")
    first[0].update(review_status="completed", ground_truth={"price_vnd": 2_000_000_000.0, "area_m2": 50.0})
    second[0].update(review_status="completed", ground_truth={"price_vnd": 2_000_000_000.0, "area_m2": 51.0})
    adjudication[0].update(
        adjudication_status="completed",
        adjudicator_id="reviewer-three",
        disagreement_fields=["area_m2"],
        ground_truth={"price_vnd": 2_000_000_000.0, "area_m2": 50.0},
        numeric_tolerances={"area_m2": {"absolute": 0.5}},
    )
    write_jsonl(output / "reviewer-a.jsonl", first)
    write_jsonl(output / "reviewer-b.jsonl", second)
    write_jsonl(output / "adjudication.jsonl", adjudication)
    return candidates


def test_finalize_requires_two_completed_reviews_and_independent_adjudication(tmp_path):
    source = tmp_path / "source.jsonl"
    output = tmp_path / "packet"
    write_jsonl(source, [raw_row("real", synthetic=False)])
    prepare_dataset(source, output, dataset_id="unit-panel", reviewer_ids=("reviewer-one", "reviewer-two"))
    final = tmp_path / "final.jsonl"
    with pytest.raises(ValueError, match="both independent reviews"):
        finalize_annotations(
            output / "candidates.jsonl",
            output / "reviewer-a.jsonl",
            output / "reviewer-b.jsonl",
            output / "adjudication.jsonl",
            final,
        )
    _complete_packet(output)
    rows = finalize_annotations(
        output / "candidates.jsonl",
        output / "reviewer-a.jsonl",
        output / "reviewer-b.jsonl",
        output / "adjudication.jsonl",
        final,
    )
    assert rows[0]["annotation_status"] == "adjudicated"
    assert rows[0]["reviewer_ids"] == ["reviewer-one", "reviewer-two"]
    assert rows[0]["adjudicator_id"] == "reviewer-three"
    assert read_jsonl(final) == rows


def test_finalize_rejects_incorrect_disagreement_declaration(tmp_path):
    source = tmp_path / "source.jsonl"
    output = tmp_path / "packet"
    write_jsonl(source, [raw_row("real", synthetic=False)])
    prepare_dataset(source, output, dataset_id="unit-panel", reviewer_ids=("reviewer-one", "reviewer-two"))
    _complete_packet(output)
    adjudication = read_jsonl(output / "adjudication.jsonl")
    adjudication[0]["disagreement_fields"] = []
    write_jsonl(output / "adjudication.jsonl", adjudication)
    with pytest.raises(ValueError, match="disagreement_fields"):
        finalize_annotations(
            output / "candidates.jsonl",
            output / "reviewer-a.jsonl",
            output / "reviewer-b.jsonl",
            output / "adjudication.jsonl",
            tmp_path / "final.jsonl",
        )


def test_finalize_rejects_invalid_numeric_tolerance(tmp_path):
    source = tmp_path / "source.jsonl"
    output = tmp_path / "packet"
    write_jsonl(source, [raw_row("real", synthetic=False)])
    prepare_dataset(source, output, dataset_id="unit-panel", reviewer_ids=("reviewer-one", "reviewer-two"))
    _complete_packet(output)
    adjudication = read_jsonl(output / "adjudication.jsonl")
    adjudication[0]["numeric_tolerances"] = {"area_m2": {"absolute": -1}}
    write_jsonl(output / "adjudication.jsonl", adjudication)
    with pytest.raises(ValueError, match="invalid absolute tolerance"):
        finalize_annotations(
            output / "candidates.jsonl",
            output / "reviewer-a.jsonl",
            output / "reviewer-b.jsonl",
            output / "adjudication.jsonl",
            tmp_path / "final.jsonl",
        )


def test_finalize_requires_panel_wide_reviewer_and_adjudicator_identity(tmp_path):
    source = tmp_path / "source.jsonl"
    output = tmp_path / "packet"
    write_jsonl(source, [
        raw_row("first", synthetic=False, title="Nhà thứ nhất"),
        raw_row("second", synthetic=False, title="Nhà thứ hai"),
    ])
    prepare_dataset(source, output, dataset_id="unit-panel", reviewer_ids=("reviewer-one", "reviewer-two"))
    first = read_jsonl(output / "reviewer-a.jsonl")
    second = read_jsonl(output / "reviewer-b.jsonl")
    adjudication = read_jsonl(output / "adjudication.jsonl")
    for index in range(2):
        first[index].update(review_status="completed", ground_truth={"area_m2": 50.0})
        second[index].update(review_status="completed", ground_truth={"area_m2": 50.0})
        adjudication[index].update(
            adjudication_status="completed",
            adjudicator_id="reviewer-three",
            disagreement_fields=[],
            ground_truth={"area_m2": 50.0},
        )
    first[1]["reviewer_id"] = "different-reviewer"
    adjudication[1]["reviewer_ids"] = ["different-reviewer", "reviewer-two"]
    write_jsonl(output / "reviewer-a.jsonl", first)
    write_jsonl(output / "reviewer-b.jsonl", second)
    write_jsonl(output / "adjudication.jsonl", adjudication)
    with pytest.raises(ValueError, match="reviewer IDs must stay consistent"):
        finalize_annotations(
            output / "candidates.jsonl", output / "reviewer-a.jsonl",
            output / "reviewer-b.jsonl", output / "adjudication.jsonl",
            tmp_path / "final.jsonl",
        )

    first[1]["reviewer_id"] = "reviewer-one"
    adjudication[1]["reviewer_ids"] = ["reviewer-one", "reviewer-two"]
    adjudication[1]["adjudicator_id"] = "different-adjudicator"
    write_jsonl(output / "reviewer-a.jsonl", first)
    write_jsonl(output / "adjudication.jsonl", adjudication)
    with pytest.raises(ValueError, match="adjudicator ID must stay consistent"):
        finalize_annotations(
            output / "candidates.jsonl", output / "reviewer-a.jsonl",
            output / "reviewer-b.jsonl", output / "adjudication.jsonl",
            tmp_path / "final.jsonl",
        )
