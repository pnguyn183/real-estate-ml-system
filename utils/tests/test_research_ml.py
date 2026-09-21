"""Guard the scientifically consequential split/label boundaries, not scores."""
from datetime import datetime, timedelta, timezone

import pandas as pd

from research.benchmark import build_traffic_supervised, chronological_split, traffic_benchmark
from research.data_audit import profile_records


def observations(count=100, run="one", start=None, step=5):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{"timestamp": (start + timedelta(seconds=i * step)).isoformat(), "run_id": run,
             "requested_rate": float(i), "incoming_rate": float(i / 2), "throughput": float(i / 3)} for i in range(count)]


def test_traffic_labels_are_future_demand_not_admitted_rate():
    frame, _ = build_traffic_supervised(observations(), 30)
    assert not frame.empty
    assert (frame.target == frame.requested_rate + 6).all()
    assert (frame.requested_rate_rolling4 == frame.requested_rate - 1.5).all()
    assert (frame.label_timestamp - frame.timestamp == pd.Timedelta(seconds=30)).all()


def test_traffic_features_and_labels_never_cross_runs_or_gaps():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    short = observations(7)
    short += observations(7, start=start + timedelta(seconds=35), run="two")
    short += observations(7, start=start + timedelta(seconds=100), run="one")
    frame, audit = build_traffic_supervised(short, 30)
    assert frame.empty  # Only 35s per segment: 15s lag warmup + 30s horizon cannot fit.
    assert audit["runs"]["one"]["gap_count"] == 1


def test_collector_epoch_seconds_and_load_drain_boundary():
    rows = observations(100)
    for index, row in enumerate(rows):
        row["timestamp"] = datetime.fromisoformat(row["timestamp"]).timestamp()
        row["phase"] = "load" if index < 50 else "drain"
    frame, _ = build_traffic_supervised(rows, 30)
    assert not frame.empty
    assert (frame.timestamp.dt.year == 2026).all()
    # Labels and lag windows do not span load->drain discontinuities.
    assert not ((frame.requested_rate >= 44) & (frame.requested_rate < 53)).any()


def test_chronological_split_purges_future_label_overlap():
    frame, _ = build_traffic_supervised(observations(300), 300)
    train, test = chronological_split(frame)
    assert len(train) and len(test)
    assert train.label_timestamp.max() < test.timestamp.min()
    assert len(train) + len(test) < len(frame)


def test_short_real_run_emits_no_fabricated_forecast_scores(tmp_path):
    result = traffic_benchmark(observations(20), tmp_path, render_plots=False)
    assert all(v["status"] == "insufficient_data" for v in result["horizons"].values())
    assert all("models" not in v for v in result["horizons"].values())


def test_quality_audit_counts_missing_invalid_and_duplicates():
    report = profile_records([{"url": "one", "area_m2": 0}, {"url": "one", "area_m2": "bad"}, {"url": "two", "area_m2": None}])
    assert report["duplicates"]["url"] == 1
    assert report["columns"]["area_m2"]["missing_count"] == 1
    assert report["numeric"]["area_m2"]["unparseable_present_count"] == 1
    assert report["numeric"]["area_m2"]["nonpositive_count"] == 1


def test_data_profile_parses_collector_seconds_instead_of_nanoseconds():
    rows = observations(2)
    for row in rows:
        row["timestamp"] = datetime.fromisoformat(row["timestamp"]).timestamp()
    report = profile_records(rows, numeric_fields=["requested_rate"])
    assert report["timestamps"]["timestamp"]["min"].startswith("2026-01-01")
    assert report["numeric"]["requested_rate"]["finite_count"] == 2
