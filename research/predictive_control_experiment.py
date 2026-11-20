"""Readiness gate for a separate forecast-driven control experiment.

This module does not alter the production controller.  It verifies that a
forecast artifact covers an independently measured overload point before a
paired predictive-control trial is allowed to start.  Passing this gate is
only permission to run the experiment, never evidence that predictive control
improves the pipeline.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _target_support(path: Path, target: str, train_indices: set[int]) -> dict:
    values: list[float] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if index not in train_indices:
                continue
            try:
                value = float(row[target])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                values.append(value)
    if not values:
        raise ValueError("Forecast training support has no finite nonnegative target values")
    return {
        "count": len(values),
        "unique_count": len(set(values)),
        "min": min(values),
        "max": max(values),
        "path": str(path),
        "sha256": _sha256(path),
    }


def _training_indices(path: Path) -> tuple[set[int], dict]:
    split = _read_json(path)
    values = split.get("train_indices")
    if (not isinstance(values, list) or not values
            or any(type(value) is not int or value < 0 for value in values)
            or len(set(values)) != len(values)):
        raise ValueError("Forecast split has no valid unique train_indices")
    return set(values), {"path": str(path), "sha256": _sha256(path), "train_count": len(values)}


def _capacity(evidence: dict) -> dict:
    """Read either a raw calibration run or its conservative committed summary."""
    raw = evidence.get("calibration_evidence")
    result = evidence.get("result") if isinstance(evidence.get("result"), dict) else {}
    profile = evidence.get("selected_profile", result.get("selected_overload_profile"))
    rate = raw.get("rate") if isinstance(raw, dict) else None
    if rate is None and isinstance(profile, list):
        overload = next((row for row in profile if isinstance(row, dict) and row.get("name") == "overload"), None)
        rate = overload.get("rate") if overload else None
    return {
        "status": evidence.get("status"),
        "overload_rate": rate,
        "resource_fingerprint": evidence.get("resource_fingerprint", result.get("resource_fingerprint")),
        "reason": raw.get("reason") if isinstance(raw, dict) else None,
    }


def evaluate_readiness(artifact: str | Path, capacity_evidence: str | Path,
                       support_csv: str | Path | None = None,
                       split_json: str | Path | None = None) -> dict:
    """Return a fail-closed readiness result for an isolated paired trial."""
    artifact = Path(artifact)
    capacity_path = Path(capacity_evidence)
    manifest_path = artifact.with_suffix(".json")
    support_path = Path(support_csv) if support_csv else artifact.with_name("supervised.csv")
    split_path = Path(split_json) if split_json else artifact.with_name("split.json")
    blockers: list[str] = []

    manifest = _read_json(manifest_path)
    actual_hash = _sha256(artifact)
    if manifest.get("schema_version") != 1 or manifest.get("eligible") is not True:
        blockers.append("forecast_artifact_not_eligible")
    if manifest.get("artifact_sha256") != actual_hash:
        blockers.append("forecast_artifact_hash_mismatch")
    if manifest.get("target") != "requested_rate" or manifest.get("target_semantics") != "offered demand before admission":
        blockers.append("forecast_target_is_not_pre_admission_demand")
    if not _finite(manifest.get("horizon_seconds")) or manifest["horizon_seconds"] <= 0:
        blockers.append("forecast_horizon_invalid")

    try:
        train_indices, split_identity = _training_indices(split_path)
        support = _target_support(support_path, manifest.get("target", "requested_rate"), train_indices)
        support["split"] = split_identity
    except (OSError, ValueError) as exc:
        support = {"path": str(support_path), "error": type(exc).__name__}
        blockers.append("forecast_training_support_unavailable")

    expected_support_hash = manifest.get("support_sha256")
    expected_split_hash = manifest.get("split_sha256")
    if not expected_support_hash:
        blockers.append("forecast_support_hash_missing")
    elif support.get("sha256") != expected_support_hash:
        blockers.append("forecast_support_hash_mismatch")
    if not expected_split_hash:
        blockers.append("forecast_split_hash_missing")
    elif support.get("split", {}).get("sha256") != expected_split_hash:
        blockers.append("forecast_split_hash_mismatch")

    capacity_raw = _read_json(capacity_path)
    capacity = _capacity(capacity_raw)
    if capacity["status"] not in {"calibration_complete", "completed"}:
        blockers.append("capacity_calibration_not_completed")
    rate = capacity["overload_rate"]
    if not _finite(rate) or rate <= 0:
        blockers.append("measured_overload_rate_missing")
    if not capacity["resource_fingerprint"]:
        blockers.append("capacity_resource_fingerprint_missing")
    if _finite(rate) and _finite(support.get("max")) and support["max"] < rate:
        blockers.append("forecast_training_range_does_not_cover_overload_rate")

    return {
        "schema_version": 1,
        "status": "ready_for_isolated_predictive_control_trial" if not blockers else "blocked_before_trial",
        "production_controller_integrated": False,
        "blockers": blockers,
        "forecast": {
            "artifact": str(artifact),
            "artifact_sha256": actual_hash,
            "manifest": str(manifest_path),
            "model": manifest.get("model"),
            "target": manifest.get("target"),
            "target_semantics": manifest.get("target_semantics"),
            "horizon_seconds": manifest.get("horizon_seconds"),
            "training_support": support,
        },
        "capacity": {
            **capacity,
            "evidence": str(capacity_path),
            "evidence_sha256": _sha256(capacity_path),
        },
        "required_trial": {
            "minimum_pairs": 3,
            "order": "AB/BA alternating",
            "arms": ["reactive_controller", "experimental_forecast_controller"],
            "same_conditions": ["workload", "seed", "resource_fingerprint", "initial_state", "duration"],
            "required_accounting": ["offered", "admitted", "committed", "deferred", "rejected"],
            "outcomes": ["p95_latency", "p99_latency", "error_rate", "peak_lag", "recovery_time", "served_fraction"],
            "attribution": "Every predictive action must reference this artifact hash and precede the measured response.",
        },
        "conclusion": (
            "Prerequisites passed; this still does not authorize production integration or claim improvement."
            if not blockers else
            "Do not connect the forecast to the controller until blockers are resolved and the paired trial passes."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--capacity-evidence", type=Path, required=True)
    parser.add_argument("--support-csv", type=Path)
    parser.add_argument("--split-json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = evaluate_readiness(args.artifact, args.capacity_evidence, args.support_csv, args.split_json)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, ensure_ascii=False))
    return 0 if result["status"] == "ready_for_isolated_predictive_control_trial" else 2


if __name__ == "__main__":
    raise SystemExit(main())
