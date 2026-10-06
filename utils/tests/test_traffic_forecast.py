"""Forecast tests use explicit fixtures; only runtime logs are research evidence."""
from datetime import datetime, timedelta, timezone
import json

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression

from research.benchmark import build_traffic_supervised, traffic_benchmark
from research.forecast import TrafficForecaster, save_forecast_artifact, traffic_features
from research.history_audit import audit_history


def observations(count=220):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{"timestamp": (start + timedelta(seconds=index * 5)).isoformat(), "run_id": "fixture", "topic": "test_topic",
             "requested_rate": float(index + 1), "incoming_rate": float(index + 1),
             "instrumentation_ready": True, "errors": []} for index in range(count)]


def test_missing_target_breaks_feature_and_label_windows():
    rows = observations(40)
    rows[20]["requested_rate"] = None
    frame, _ = build_traffic_supervised(rows, 30)
    assert not ((frame.requested_rate >= 15) & (frame.requested_rate <= 24)).any()


@pytest.mark.parametrize("failure", [{"errors": ["docker: unavailable"]}, {"instrumentation_ready": False}, {"telemetry_usable": False}])
def test_failed_telemetry_breaks_feature_and_label_windows_with_numeric_target(failure):
    rows = observations(40)
    rows[20].update(failure)
    frame, audit = build_traffic_supervised(rows, 30)
    assert not frame.empty
    assert not ((frame.requested_rate >= 15) & (frame.requested_rate <= 24)).any()
    assert audit["runs"]["fixture"]["unavailable_telemetry_rows"] == 1


def test_topic_boundary_splits_features_and_horizon_labels():
    rows = observations(40)
    for row in rows[20:]:
        row["topic"] = "different_topic"
    frame, audit = build_traffic_supervised(rows, 30)
    assert not ((frame.requested_rate >= 15) & (frame.requested_rate <= 23)).any()
    assert audit["runs"]["fixture"]["topic_boundary_count"] == 1


def test_legacy_rows_without_optional_quality_flags_remain_usable():
    rows = observations(40)
    for row in rows:
        row.pop("errors")
        row.pop("instrumentation_ready")
    frame, audit = build_traffic_supervised(rows, 30)
    assert len(frame) == 31
    assert audit["runs"]["fixture"]["unavailable_telemetry_rows"] == 0


def test_long_context_uses_timestamps_and_only_past_values():
    rows = observations(100)
    frame, _ = build_traffic_supervised(rows, 30, lag_seconds=[60, 120])
    assert (frame.requested_rate_lag_60s == frame.requested_rate - 12).all()
    assert (frame.requested_rate_lag_120s == frame.requested_rate - 24).all()
    assert frame.requested_rate.min() == 25
    assert (frame.target == frame.requested_rate + 6).all()


def test_idle_collection_has_no_learned_scores_or_export(tmp_path):
    rows = observations()
    for row in rows:
        row["requested_rate"] = 0
    result = traffic_benchmark(rows, tmp_path, horizons=[30], min_train=20, min_test=10, render_plots=False)
    horizon = result["horizons"]["30"]
    assert horizon["status"] == "insufficient_variation"
    assert horizon["persistence_diagnostic"]["r2"] is None
    assert "models" not in horizon
    assert not list(tmp_path.rglob("*.joblib"))


def test_forecast_export_uses_purged_validation_and_inference_matches_features(tmp_path, monkeypatch):
    monkeypatch.setattr("research.benchmark.estimators", lambda **kwargs: {"fixture_linear": LinearRegression()})
    rows = observations()
    result = traffic_benchmark(rows, tmp_path, horizons=[30], min_train=20, min_test=10, lag_seconds=[60, 120], render_plots=False)
    metrics = result["horizons"]["30"]
    assert metrics["artifact_eligibility"]["eligible"]
    split = json.loads((tmp_path / "horizon_30s/split.json").read_text())
    assert pd.Timestamp(split["last_selection_train_label"]) < pd.Timestamp(split["first_validation_observation"])
    assert pd.Timestamp(split["last_train_label"]) < pd.Timestamp(split["first_test_observation"])
    forecaster = TrafficForecaster(metrics["artifact_eligibility"]["artifact"])
    for row in rows[-25:]:
        prediction = forecaster.observe(row)
    assert prediction["reason"] == "validated_forecast"
    assert prediction["predicted_traffic"] == pytest.approx(rows[-1]["requested_rate"] + 6)
    history = pd.DataFrame(rows[-25:])
    history["timestamp"] = pd.to_datetime(history.timestamp, utc=True)
    expected = traffic_features(history, forecaster.metadata["observation_columns"], [60, 120])
    actual = forecaster.pipeline.predict(pd.DataFrame([expected])[forecaster.metadata["features"]])[0]
    assert prediction["predicted_traffic"] == pytest.approx(actual)
    changed = dict(rows[-1], topic="different")
    assert forecaster.observe(changed)["reason"] == "topic_mismatch"
    assert len(forecaster.history) == 0
    assert forecaster.observe(dict(rows[-1], requested_rate=np.nan))["reason"] == "missing_current_target_or_timestamp"
    path = tmp_path / "horizon_30s/forecast.joblib"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        TrafficForecaster(path)


def test_export_requires_measured_eligibility(tmp_path):
    with pytest.raises(ValueError, match="evidence gate"):
        save_forecast_artifact(None, tmp_path, {"eligible": False})


@pytest.mark.parametrize("failure", [{"errors": ["worker: unavailable"]}, {"instrumentation_ready": False}, {"telemetry_usable": False}])
def test_inference_rejects_failed_measurement_and_requires_fresh_warmup(tmp_path, monkeypatch, failure):
    monkeypatch.setattr("research.benchmark.estimators", lambda **kwargs: {"fixture_linear": LinearRegression()})
    rows = observations()
    result = traffic_benchmark(rows, tmp_path, horizons=[30], min_train=20, min_test=10, render_plots=False)
    forecaster = TrafficForecaster(result["horizons"]["30"]["artifact_eligibility"]["artifact"])
    for row in rows[:4]:
        predicted = forecaster.observe(row)
    assert predicted["predicted_traffic"] is not None
    rejected = forecaster.observe(dict(rows[4], **failure))
    assert rejected["reason"] == "telemetry_unavailable"
    assert rejected["predicted_traffic"] is None
    assert not forecaster.history
    for row in rows[5:8]:
        assert forecaster.observe(row)["predicted_traffic"] is None
    assert forecaster.observe(rows[8])["predicted_traffic"] is not None


def test_history_audit_distinguishes_outage_from_zero_and_breaks_segments():
    rows = observations(10)
    for row in rows:
        row["incoming_rate"] = 0
    rows[4]["incoming_rate"] = None
    rows[4]["errors"] = ["kafka: unavailable"]
    rows[4]["instrumentation_ready"] = False
    rows[8]["timestamp"] = "2026-01-01T13:00:00+00:00"
    rows[9]["timestamp"] = "2026-01-01T13:00:05+00:00"
    result = audit_history(rows)
    assert result["status"] == "insufficient_variation"
    assert result["missing_target_count"] == 1
    assert result["error_row_count"] == 1
    assert result["healthy_target_rows"] == 9
    assert result["gap_count"] == 1
    assert len(result["healthy_segments"]) == 3
    assert result["healthy_contiguous_span_seconds"] == 30
