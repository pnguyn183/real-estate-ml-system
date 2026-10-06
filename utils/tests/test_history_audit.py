"""History CLI must preserve gaps and report unusable evidence without invented plots."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from research.history_audit import audit_history


@pytest.mark.parametrize("key", ["run_id", "topic", "phase"])
def test_healthy_history_never_bridges_provenance_boundaries(key):
    records = [{"timestamp": 1_700_000_000 + index * 5, "run_id": "one", "topic": "raw", "phase": "load",
                "incoming_rate": index, "errors": [], "instrumentation_ready": True} for index in range(8)]
    for record in records[4:]:
        record[key] = "different"
    report = audit_history(records)
    assert report["provenance_boundary_count"] == 1
    assert [segment["rows"] for segment in report["healthy_segments"]] == [4, 4]
    assert report["healthy_contiguous_span_seconds"] == 30
    assert report["longest_healthy_segment_seconds"] == 15
    assert report["healthy_segments"][1][key] == "different"


def test_unavailable_values_cannot_supply_variation():
    records = [{"timestamp": 1_700_000_000 + index * 5, "run_id": "one", "incoming_rate": index,
                "errors": [] if index == 0 else ["docker: unavailable"], "instrumentation_ready": True}
               for index in range(5)]
    report = audit_history(records)
    assert report["target_support"]["unique_count"] == 5
    assert report["healthy_target_support"]["unique_count"] == 1
    assert report["status"] == "insufficient_variation"


def test_recovery_warmup_is_not_usable_history_even_after_http_returns():
    records = [{"timestamp": 1_700_000_000 + index * 5, "run_id": "one", "topic": "raw",
                "incoming_rate": index, "errors": [], "instrumentation_ready": True} for index in range(8)]
    records[3].update(telemetry_usable=False, telemetry_recovery_state="paused")
    report = audit_history(records)
    assert report["healthy_target_rows"] == 7
    assert [segment["rows"] for segment in report["healthy_segments"]] == [3, 4]


@pytest.mark.parametrize("records", [[], [{"incoming_rate": 3}], [{"timestamp": "invalid", "incoming_rate": 3}],
                                     [{"timestamp": 1_700_000_000}]])
def test_cli_reports_empty_or_incomplete_history_without_crashing(tmp_path, records):
    source = tmp_path / "observations.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    output = tmp_path / "audit"
    process = subprocess.run([sys.executable, "-m", "research.history_audit", "--input", str(source),
                              "--output", str(output)], cwd=Path(__file__).resolve().parents[2],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    report = json.loads((output / "history_audit.json").read_text())["histories"][0]
    assert report["status"] == "insufficient_data"
    if not records or records[0].get("timestamp") in (None, "invalid"):
        assert report["chart"] is None
        assert not list(output.glob("*.png"))
    else:
        assert Path(report["chart"]).is_file()
