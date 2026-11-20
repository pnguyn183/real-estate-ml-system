"""Prepare and finalize blinded, two-reviewer extraction annotations.

Predictions are deliberately excluded from every annotation packet.  The prepare
command accepts only records explicitly marked ``is_synthetic: false`` by default;
records with unknown provenance must not silently become real-data evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import re
from typing import Any, Iterable

from agents.extraction import ExtractionFields, source_text
from research.extraction_evaluation import KNOWN_FIELDS, NUMERIC_FIELDS


PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?84|0)(?:[ .-]?\d){8,10}(?!\d)")
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CONTACT_SEGMENT_PATTERN = re.compile(
    r"(?:📞|☎️?|📱)?\s*(?:liên\s*hệ|lien\s*he|contact|zalo)\s*[:\-]?[^\r\n]*",
    re.IGNORECASE,
)


def redact_annotation_text(value: str) -> str:
    """Remove contact details that are irrelevant to the extraction labels."""
    value = CONTACT_SEGMENT_PATTERN.sub("[REDACTED_CONTACT]", value)
    value = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
    return PHONE_PATTERN.sub("[REDACTED_PHONE]", value)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from None
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: each JSONL row must be an object")
        rows.append(value)
    if not rows:
        raise ValueError(f"{path}: no records")
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_record_id(row: dict[str, Any], text_hash: str) -> str:
    source = str(row.get("source") or "unknown").strip().lower()
    source_id = str(row.get("listing_id") or "").strip()
    if source_id:
        digest = _sha256_text(source + "\0" + source_id)[:20]
        return f"{source}:id-{digest}"
    return f"{source}:sha256-{text_hash[:20]}"


def _stratum(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("source") or "unknown").strip().lower(),
        str(row.get("property_type") or "unknown").strip().lower(),
    )


def prepare_dataset(
    input_path: Path,
    output_dir: Path,
    *,
    dataset_id: str,
    reviewer_ids: tuple[str, str],
    seed: int = 20261118,
    sample_size: int | None = None,
) -> dict[str, Any]:
    """Create blinded annotation packets from the explicitly-real sampling frame."""
    if not dataset_id.strip():
        raise ValueError("dataset_id must be non-empty")
    if len(set(reviewer_ids)) != 2 or any(not value.strip() for value in reviewer_ids):
        raise ValueError("exactly two distinct non-empty reviewer IDs are required")
    if sample_size is not None and sample_size < 1:
        raise ValueError("sample_size must be positive")

    input_rows = _read_jsonl(input_path)
    provenance_counts = Counter(
        "explicit_real" if row.get("is_synthetic") is False
        else "synthetic" if row.get("is_synthetic") is True
        else "unknown"
        for row in input_rows
    )
    eligible: list[tuple[dict[str, Any], str, str, str]] = []
    excluded_empty_text = 0
    duplicate_text_count = 0
    seen_text_hashes: set[str] = set()
    seen_record_ids: set[str] = set()
    for row in input_rows:
        if row.get("is_synthetic") is not False:
            continue
        raw_text = source_text(row)
        if not raw_text.strip():
            excluded_empty_text += 1
            continue
        original_text_hash = _sha256_text(raw_text)
        text = redact_annotation_text(raw_text)
        text_hash = _sha256_text(text)
        if text_hash in seen_text_hashes:
            duplicate_text_count += 1
            continue
        record_id = _stable_record_id(row, text_hash)
        if record_id in seen_record_ids:
            raise ValueError(f"duplicate record_id after preparation: {record_id}")
        seen_text_hashes.add(text_hash)
        seen_record_ids.add(record_id)
        eligible.append((row, text, text_hash, original_text_hash))
    if not eligible:
        raise ValueError("no unique records explicitly marked is_synthetic=false")

    rng = random.Random(seed)
    ordered = sorted(eligible, key=lambda item: (_stratum(item[0]), _stable_record_id(item[0], item[2])))
    if sample_size is not None and sample_size < len(ordered):
        selected_indexes = sorted(rng.sample(range(len(ordered)), sample_size))
        selected = [ordered[index] for index in selected_indexes]
        selection_method = "seeded_simple_random_sample_of_explicit_real_unique_frame"
    else:
        selected = ordered
        selection_method = "complete_explicit_real_unique_frame"

    candidates: list[dict[str, Any]] = []
    for row, text, text_hash, original_text_hash in selected:
        candidates.append({
            "dataset_id": dataset_id,
            "record_id": _stable_record_id(row, text_hash),
            "annotation_status": "awaiting_two_independent_reviews",
            "data_origin": "real_listing_explicit_flag",
            "source": str(row.get("source") or "unknown"),
            "property_type_stratum": str(row.get("property_type") or "unknown"),
            "source_record_id_sha256": _sha256_text(
                str(row.get("source") or "unknown") + "\0" + str(row.get("listing_id") or "")
            ),
            "source_text": text,
            "source_sha256": text_hash,
            "original_source_sha256": original_text_hash,
        })

    candidates_path = output_dir / "candidates.jsonl"
    _write_jsonl(candidates_path, candidates)
    for reviewer_id, filename in zip(reviewer_ids, ("reviewer-a.jsonl", "reviewer-b.jsonl"), strict=True):
        _write_jsonl(output_dir / filename, ({
            "record_id": row["record_id"],
            "source_sha256": row["source_sha256"],
            "reviewer_id": reviewer_id,
            "review_status": "pending",
            "ground_truth": {},
            "numeric_tolerances": {},
            "notes": "",
        } for row in candidates))
    _write_jsonl(output_dir / "adjudication.jsonl", ({
        "record_id": row["record_id"],
        "source_sha256": row["source_sha256"],
        "reviewer_ids": list(reviewer_ids),
        "adjudicator_id": "",
        "adjudication_status": "pending",
        "disagreement_fields": [],
        "ground_truth": {},
        "numeric_tolerances": {},
        "notes": "",
    } for row in candidates))

    stratum_counts = Counter(f"{_stratum(row)[0]}|{_stratum(row)[1]}" for row, _, _, _ in selected)
    manifest = {
        "dataset_id": dataset_id,
        "status": "awaiting_two_human_reviews_and_adjudication",
        "input": {
            "path": input_path.as_posix(),
            "sha256": _sha256_file(input_path),
            "row_count": len(input_rows),
            "provenance_counts": dict(sorted(provenance_counts.items())),
        },
        "selection": {
            "method": selection_method,
            "seed": seed,
            "eligible_unique_explicit_real_count": len(eligible),
            "selected_count": len(candidates),
            "excluded_empty_text_count": excluded_empty_text,
            "excluded_duplicate_text_count": duplicate_text_count,
            "stratum_counts": dict(sorted(stratum_counts.items())),
        },
        "annotation": {
            "reviewer_ids": list(reviewer_ids),
            "independent_review_files": ["reviewer-a.jsonl", "reviewer-b.jsonl"],
            "adjudication_file": "adjudication.jsonl",
            "packet_sha256": {
                name: _sha256_file(output_dir / name)
                for name in ("candidates.jsonl", "reviewer-a.jsonl", "reviewer-b.jsonl", "adjudication.jsonl")
            },
            "predictions_visible_to_reviewers": False,
            "contact_redaction": "Contact segments, Vietnamese-format phone numbers and email addresses are replaced before review; source listing IDs are hashed",
            "accuracy_claim_allowed": False,
        },
        "limitations": [
            "Only records explicitly marked is_synthetic=false are eligible.",
            "Rows with unknown provenance are excluded and may make the panel unrepresentative of the full corpus.",
            "No accuracy metric is valid until both human reviews and adjudication are completed.",
        ],
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _index(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        record_id = row.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"{label}: every row requires record_id")
        if record_id in result:
            raise ValueError(f"{label}: duplicate record_id {record_id}")
        result[record_id] = row
    return result


def _validate_truth(row: dict[str, Any], label: str) -> None:
    truth = row.get("ground_truth")
    if not isinstance(truth, dict) or not truth:
        raise ValueError(f"{label}: non-empty ground_truth required")
    unknown = set(truth) - KNOWN_FIELDS
    if unknown:
        raise ValueError(f"{label}: unknown ground-truth fields {sorted(unknown)}")
    try:
        ExtractionFields.model_validate(truth)
    except ValueError as exc:
        raise ValueError(f"{label}: ground_truth violates extraction schema: {exc}") from None
    tolerances = row.get("numeric_tolerances", {})
    if not isinstance(tolerances, dict) or set(tolerances) - NUMERIC_FIELDS:
        raise ValueError(f"{label}: invalid numeric_tolerances")
    for field, tolerance in tolerances.items():
        if not isinstance(tolerance, dict) or set(tolerance) - {"absolute", "relative"}:
            raise ValueError(f"{label}: invalid tolerance for {field}")
        for key in ("absolute", "relative"):
            value = tolerance.get(key, 0)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{label}: invalid {key} tolerance for {field}")


def finalize_annotations(
    candidates_path: Path,
    reviewer_a_path: Path,
    reviewer_b_path: Path,
    adjudication_path: Path,
    output_path: Path,
) -> list[dict[str, Any]]:
    """Validate completed independent reviews and emit evaluator-ready labels."""
    candidates = _index(_read_jsonl(candidates_path), "candidates")
    review_a = _index(_read_jsonl(reviewer_a_path), "reviewer-a")
    review_b = _index(_read_jsonl(reviewer_b_path), "reviewer-b")
    adjudications = _index(_read_jsonl(adjudication_path), "adjudication")
    expected = set(candidates)
    for label, rows in (("reviewer-a", review_a), ("reviewer-b", review_b), ("adjudication", adjudications)):
        if set(rows) != expected:
            raise ValueError(f"{label}: record IDs must exactly match candidates")

    finalized: list[dict[str, Any]] = []
    panel_reviewer_ids: tuple[str, str] | None = None
    panel_adjudicator_id: str | None = None
    for record_id in sorted(expected):
        candidate = candidates[record_id]
        first, second, resolved = review_a[record_id], review_b[record_id], adjudications[record_id]
        source_hash = candidate.get("source_sha256")
        source = candidate.get("source_text")
        if not isinstance(source, str) or _sha256_text(source) != source_hash:
            raise ValueError(f"{record_id}: candidate source hash mismatch")
        for label, row in (("reviewer-a", first), ("reviewer-b", second), ("adjudication", resolved)):
            if row.get("source_sha256") != source_hash:
                raise ValueError(f"{record_id}: {label} source hash mismatch")
        if first.get("review_status") != "completed" or second.get("review_status") != "completed":
            raise ValueError(f"{record_id}: both independent reviews must be completed")
        reviewer_ids = (first.get("reviewer_id"), second.get("reviewer_id"))
        if any(not isinstance(value, str) or not value for value in reviewer_ids) or len(set(reviewer_ids)) != 2:
            raise ValueError(f"{record_id}: reviews require two distinct reviewer IDs")
        if panel_reviewer_ids is None:
            panel_reviewer_ids = reviewer_ids
        elif reviewer_ids != panel_reviewer_ids:
            raise ValueError(f"{record_id}: reviewer IDs must stay consistent across the panel")
        _validate_truth(first, f"{record_id}/reviewer-a")
        _validate_truth(second, f"{record_id}/reviewer-b")
        if resolved.get("adjudication_status") != "completed":
            raise ValueError(f"{record_id}: adjudication must be completed")
        if resolved.get("reviewer_ids") != list(reviewer_ids):
            raise ValueError(f"{record_id}: adjudication reviewer IDs do not match review files")
        adjudicator_id = resolved.get("adjudicator_id")
        if not isinstance(adjudicator_id, str) or not adjudicator_id or adjudicator_id in reviewer_ids:
            raise ValueError(f"{record_id}: independent adjudicator_id required")
        if panel_adjudicator_id is None:
            panel_adjudicator_id = adjudicator_id
        elif adjudicator_id != panel_adjudicator_id:
            raise ValueError(f"{record_id}: adjudicator ID must stay consistent across the panel")
        _validate_truth(resolved, f"{record_id}/adjudication")
        disagreement_fields = sorted(
            field for field in set(first["ground_truth"]) | set(second["ground_truth"])
            if first["ground_truth"].get(field) != second["ground_truth"].get(field)
        )
        if resolved.get("disagreement_fields") != disagreement_fields:
            raise ValueError(f"{record_id}: disagreement_fields must equal {disagreement_fields}")
        finalized.append({
            "record_id": record_id,
            "annotation_status": "adjudicated",
            "reviewer_ids": list(reviewer_ids),
            "adjudicator_id": adjudicator_id,
            "source_text": source,
            "source_sha256": source_hash,
            "ground_truth": resolved["ground_truth"],
            "numeric_tolerances": resolved.get("numeric_tolerances", {}),
        })
    _write_jsonl(output_path, finalized)
    return finalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare/finalize two-reviewer extraction labels")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--input", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--dataset-id", required=True)
    prepare.add_argument("--reviewer-a", required=True)
    prepare.add_argument("--reviewer-b", required=True)
    prepare.add_argument("--seed", type=int, default=20261118)
    prepare.add_argument("--sample-size", type=int)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--candidates", type=Path, required=True)
    finalize.add_argument("--reviewer-a", type=Path, required=True)
    finalize.add_argument("--reviewer-b", type=Path, required=True)
    finalize.add_argument("--adjudication", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare_dataset(
            args.input,
            args.output_dir,
            dataset_id=args.dataset_id,
            reviewer_ids=(args.reviewer_a, args.reviewer_b),
            seed=args.seed,
            sample_size=args.sample_size,
        )
    else:
        finalize_annotations(args.candidates, args.reviewer_a, args.reviewer_b, args.adjudication, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
