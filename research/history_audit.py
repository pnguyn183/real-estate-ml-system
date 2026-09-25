"""Audit real telemetry coverage, outages and target variation without repairing logs."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.data_audit import parse_utc_timestamp, write_json
from research.forecast import target_support


def audit_history(records: list[dict], *, target="incoming_rate", interval_seconds=5.0) -> dict:
    if not np.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("interval_seconds must be finite and positive")
    frame = pd.DataFrame(records)
    if frame.empty:
        return {"status": "insufficient_data", "rows": 0}
    stamps = pd.to_datetime(frame.get("timestamp", pd.Series(index=frame.index, dtype=float)).map(parse_utc_timestamp), utc=True, errors="coerce")
    values = pd.to_numeric(frame.get(target, pd.Series(index=frame.index, dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan)
    errors = [row.get("errors", []) for row in records]
    healthy = pd.Series([not error and row.get("instrumentation_ready") is True for error, row in zip(errors, records)], index=frame.index)
    healthy &= stamps.notna() & values.notna() & (values >= 0)
    deltas = stamps.diff().dt.total_seconds()
    identities = [tuple(row.get(key) for key in ("run_id", "topic", "phase")) for row in records]
    provenance_change = pd.Series([False] + [left != right for left, right in zip(identities, identities[1:])], index=frame.index)
    boundaries = (~healthy | ~healthy.shift(fill_value=False) | provenance_change | (deltas <= 0) | (deltas > interval_seconds * 1.8)).cumsum()
    segments = []
    for _, indices in frame[healthy].groupby(boundaries[healthy]).groups.items():
        times = stamps.loc[indices]
        identity = records[indices[0]]
        segments.append({"rows": len(indices), "start": times.min().isoformat(), "end": times.max().isoformat(), "duration_seconds": (times.max() - times.min()).total_seconds(),
                         **{key: identity.get(key) for key in ("run_id", "topic", "phase")}})
    support = target_support(values)
    healthy_support = target_support(values[healthy])
    gaps = [{"previous_timestamp": stamps.iloc[index - 1].isoformat(), "timestamp": stamps.iloc[index].isoformat(), "seconds": float(deltas.iloc[index])}
            for index in range(1, len(stamps)) if pd.notna(deltas.iloc[index]) and deltas.iloc[index] > interval_seconds * 1.8]
    reasons = []
    if healthy_support["unique_count"] < 2:
        reasons.append("Target is constant; undefined R2 and zero error on idle data cannot establish forecast skill.")
    if not healthy.all():
        reasons.append("Unavailable/error observations cannot be treated as zero traffic or bridged during training.")
    if not all(row.get("run_id") for row in records):
        reasons.append("Missing run_id provenance; benchmark cannot infer boundaries safely.")
    valid_stamps = stamps.dropna()
    status = ("insufficient_data" if not healthy.any() else "insufficient_variation"
              if healthy_support["unique_count"] < 2 else "requires_chronological_evaluation")
    return {"rows": len(records), "target": target, "target_support": support, "healthy_target_support": healthy_support,
            "start_timestamp": valid_stamps.min().isoformat() if len(valid_stamps) else None,
            "end_timestamp": valid_stamps.max().isoformat() if len(valid_stamps) else None,
            "wall_span_seconds": (valid_stamps.max() - valid_stamps.min()).total_seconds() if len(valid_stamps) else None,
            "expected_interval_seconds": interval_seconds, "median_interval_seconds": float(deltas.median()),
            "gaps": gaps, "gap_count": len(gaps), "invalid_timestamps": int(stamps.isna().sum()),
            "duplicate_timestamps": int(valid_stamps.duplicated().sum()), "nonmonotonic_timestamp_count": int((deltas <= 0).sum()),
            "provenance_boundary_count": int(provenance_change.sum()),
            "missing_target_count": int(values.isna().sum()), "missing_target_fraction": float(values.isna().mean()),
            "error_row_count": sum(bool(error) for error in errors), "healthy_target_rows": int(healthy.sum()),
            "error_categories": dict(Counter(str(error).split(":", 1)[0] for row in errors for error in row)),
            "healthy_segments": segments, "healthy_contiguous_span_seconds": sum(segment["duration_seconds"] for segment in segments),
            "longest_healthy_segment_seconds": max((segment["duration_seconds"] for segment in segments), default=0),
            "run_ids": sorted({str(row.get("run_id")) for row in records}),
            "topics": sorted({str(row.get("topic")) for row in records}),
            "status": status,
            "limitations": reasons}


def history_plot(records, path):
    if not records:
        return None
    frame = pd.DataFrame(records)
    if "timestamp" not in frame:
        return None
    stamps = pd.to_datetime(frame.timestamp.map(parse_utc_timestamp), utc=True, errors="coerce")
    if not stamps.notna().any():
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    for axis, name, label in zip(axes[:2], ("incoming_rate", "cpu_percent"), ("Ingress (messages/s)", "Pipeline CPU (% Docker capacity)")):
        values = pd.to_numeric(frame.get(name, pd.Series(index=frame.index, dtype=float)), errors="coerce")
        axis.plot(stamps, values, linewidth=.8)
        if not values.notna().any():
            axis.text(.5, .5, "No measured values", ha="center", transform=axis.transAxes)
        axis.set_ylabel(label)
        axis.grid(alpha=.2)
    axes[2].step(stamps, [bool(row.get("errors")) for row in records], where="post", color="tab:red")
    axes[2].set(ylabel="Collection error", xlabel="Actual UTC timestamp", yticks=[0, 1])
    fig.suptitle("Recorded passive traffic history: gaps and missing measurements are retained")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", default="incoming_rate", choices=["incoming_rate", "requested_rate"])
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, source in enumerate(args.input):
        payload = source.read_bytes()
        records = [json.loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
        report = audit_history(records, target=args.target, interval_seconds=args.interval)
        report.update(input_path=str(source), input_sha256=hashlib.sha256(payload).hexdigest())
        chart = args.output / f"history_{index + 1}.png"
        report["chart"] = history_plot(records, chart)
        if report["chart"] is None:
            report["chart_unavailable_reason"] = "No observations with a valid timestamp; no chart generated."
        reports.append(report)
    write_json(args.output / "history_audit.json", {"histories": reports})
    print(json.dumps({"output": str(args.output / "history_audit.json"), "statuses": [report["status"] for report in reports]}))


if __name__ == "__main__":
    main()
