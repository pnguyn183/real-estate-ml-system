"""Trusted local traffic-forecast artifacts and past-only online inference.

Artifacts are experimental until an untouched chronological test beats persistence.
No model is loaded or traffic policy changed merely by importing this module.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from research.data_audit import parse_utc_timestamp, write_json


def observation_unavailable(observation: dict) -> bool:
    """Reject explicit collection failures without inventing status for old logs."""
    return bool(observation.get("errors")) or (
        "instrumentation_ready" in observation and observation["instrumentation_ready"] is not True
    )


def traffic_features(window: pd.DataFrame, columns: list[str], lag_seconds=()) -> dict:
    """Use already-observed samples; longer context is explicitly timestamp based."""
    if len(window) < 4:
        raise ValueError("At least four observed samples are required")
    current = window.iloc[-1]
    row = {}
    for column in columns:
        values = pd.to_numeric(window[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
        row[column] = values.iloc[-1]
        row[f"{column}_lag1"] = values.iloc[-2]
        row[f"{column}_lag3"] = values.iloc[-4]
        row[f"{column}_rolling4"] = values.iloc[-4:].mean()
        for seconds in lag_seconds:
            cutoff = current.timestamp - pd.Timedelta(seconds=seconds)
            prior = values[window.timestamp <= cutoff]
            row[f"{column}_lag_{seconds}s"] = prior.iloc[-1] if len(prior) else np.nan
            row[f"{column}_rolling_{seconds}s"] = values[window.timestamp > cutoff].mean()
    stamp = pd.Timestamp(current.timestamp)
    row["hour_sin"] = np.sin(2 * np.pi * stamp.hour / 24)
    row["hour_cos"] = np.cos(2 * np.pi * stamp.hour / 24)
    row["day_of_week"] = stamp.dayofweek
    return row


def target_support(values) -> dict:
    values = np.asarray(values, dtype=float)
    finite = values[np.isfinite(values)]
    return {"count": len(values), "finite_count": len(finite),
            "unique_count": len(np.unique(finite)),
            "nonzero_count": int(np.count_nonzero(finite)),
            "min": float(finite.min()) if len(finite) else None,
            "max": float(finite.max()) if len(finite) else None}


def save_forecast_artifact(pipeline, directory: Path, metadata: dict) -> str:
    """Export only a candidate that passed explicitly recorded evidence gates."""
    if not metadata.get("eligible"):
        raise ValueError("Refusing export: forecast evidence gate has not passed")
    directory.mkdir(parents=True, exist_ok=True)
    artifact = directory / "forecast.joblib"
    joblib.dump(pipeline, artifact)
    manifest = dict(metadata, schema_version=1, artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    write_json(directory / "forecast.json", manifest)
    return str(artifact)


class TrafficForecaster:
    """Load a locally trained gated artifact; return unavailable on invalid history.

    This forecasts demand only. The existing bounded controller remains responsible
    for translating predictions into safe actions and evaluating their effects.
    """

    def __init__(self, artifact: str | Path):
        path = Path(artifact)
        self.metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if self.metadata.get("schema_version") != 1 or not self.metadata.get("eligible"):
            raise ValueError("Forecast is not eligible for experimental inference")
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.metadata.get("artifact_sha256"):
            raise ValueError("Forecast artifact hash mismatch")
        self.pipeline = joblib.load(path)
        seconds = max(self.metadata.get("lag_seconds", []), default=0)
        self.history = deque(maxlen=max(4, int(seconds / self.metadata["sampling_interval_seconds"] * 2) + 5))

    def observe(self, observation: dict) -> dict:
        result = {"predicted_traffic": None, "target": self.metadata["target"],
                  "horizon_seconds": self.metadata["horizon_seconds"], "reason": None}
        if observation_unavailable(observation):
            self.history.clear()
            return dict(result, reason="telemetry_unavailable")
        try:
            stamp = pd.Timestamp(parse_utc_timestamp(observation.get("timestamp")))
            value = float(observation.get(self.metadata["target"]))
            if pd.isna(stamp) or not np.isfinite(value) or value < 0:
                raise ValueError("invalid target/timestamp")
        except (ValueError, TypeError, OverflowError):
            self.history.clear()
            return dict(result, reason="missing_current_target_or_timestamp")
        expected_topic = self.metadata.get("topic")
        if expected_topic and observation.get("topic") != expected_topic:
            self.history.clear()
            return dict(result, reason="topic_mismatch")
        if self.history:
            previous = self.history[-1]
            elapsed = (stamp - previous["timestamp"]).total_seconds()
            expected = self.metadata["sampling_interval_seconds"]
            boundary = any(observation.get(key) != previous.get(key) for key in ("run_id", "topic", "phase"))
            if boundary or not expected / 2 <= elapsed <= expected * 1.8:
                self.history.clear()
        record = dict(observation, timestamp=stamp)
        for column in self.metadata["observation_columns"]:
            record.setdefault(column, np.nan)
        self.history.append(record)
        if len(self.history) < 4:
            return dict(result, reason="history_warmup_or_discontinuity")
        longest_lag = max(self.metadata.get("lag_seconds", []), default=0)
        if (stamp - self.history[0]["timestamp"]).total_seconds() < longest_lag:
            return dict(result, reason="history_warmup_or_discontinuity")
        frame = pd.DataFrame(self.history)
        features = traffic_features(frame, self.metadata["observation_columns"], self.metadata.get("lag_seconds", []))
        predicted = float(self.pipeline.predict(pd.DataFrame([features])[self.metadata["features"]])[0])
        if not np.isfinite(predicted):
            return dict(result, reason="nonfinite_prediction")
        # Match the exact nonnegative postprocessing evaluated in the benchmark.
        return dict(result, predicted_traffic=max(0.0, predicted), reason="validated_forecast",
                    observation_timestamp=stamp.isoformat(), model=self.metadata["model"])
