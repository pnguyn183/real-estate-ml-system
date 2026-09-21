"""Reproducible legacy diagnosis and separate, leakage-aware traffic forecasting.

Legacy results do not measure traffic-control effectiveness or publish a model.
Traffic forecasting requires genuine historical observations; no demo data is generated.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from research.data_audit import write_json, parse_utc_timestamp, profile_records, plots

OBSERVATIONS = ["requested_rate", "incoming_rate", "throughput", "cpu_percent", "ram_percent", "kafka_lag", "latency_p95_seconds", "error_rate", "current_limit"]


def versions():
    result = {"python": platform.python_version()}
    for name in ("numpy", "pandas", "scikit-learn", "xgboost", "matplotlib"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def scores(actual, predicted):
    return {"r2": float(r2_score(actual, predicted)) if len(actual) > 1 else None,
            "mae": float(mean_absolute_error(actual, predicted)),
            "rmse": float(np.sqrt(mean_squared_error(actual, predicted)))}


def estimators(seed=42, trees=100, jobs=2):
    models = {
        "random_forest": RandomForestRegressor(n_estimators=trees, max_depth=14, min_samples_leaf=2, random_state=seed, n_jobs=jobs),
        "gradient_boosting": GradientBoostingRegressor(n_estimators=trees, max_depth=3, learning_rate=.06, random_state=seed),
    }
    try:
        from xgboost import XGBRegressor
        models["xgboost"] = XGBRegressor(n_estimators=trees, max_depth=6, learning_rate=.06, objective="reg:squarederror", random_state=seed, n_jobs=jobs, tree_method="hist")
    except ImportError:
        models["xgboost"] = None
    return models


def comparison_plot(results: dict, path: Path, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    usable = {k: v for k, v in results.items() if v.get("status") == "measured"}
    if not usable:
        return None
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax, metric in zip(axes, ("r2", "mae", "rmse")):
        ax.bar(list(usable), [v[metric] for v in usable.values()])
        ax.set(ylabel=metric.upper(), xlabel="Model")
        ax.tick_params(axis="x", rotation=30)
        ax.axhline(0, color="black", linewidth=.6)
    fig.suptitle(title)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def legacy_benchmark(records: list[dict], output: Path, *, trees=100, jobs=2):
    from agents.safety import real_records
    from modeling.price_model import build_feature_frame, build_regression_pipeline, NUMERIC_FEATURES, CATEGORICAL_FEATURES, TEXT_FEATURE, TARGET
    from processing.price_anomaly import get_anomaly_training_policy
    records = real_records(records)
    records = [r for r in records if r.get("is_model_candidate", True) and r.get(TARGET) is not None and r[TARGET] > 0
               and not (get_anomaly_training_policy() == "EXCLUDE" and r.get("is_price_anomaly"))]
    if len(records) < 200:
        return {"status": "insufficient_data", "rows": len(records), "required": 200}
    frame = build_feature_frame(records)
    X, y = frame[NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]], frame[TARGET].astype(float)
    train, test = train_test_split(np.arange(len(frame)), test_size=.2, random_state=42)
    write_json(output / "split.json", {"train_indices": train.tolist(), "test_indices": test.tolist(), "seed": 42, "strategy": "legacy random 80/20 diagnostic, unsuitable for forecasting"})
    template = build_regression_pipeline()
    prep = Pipeline(template.steps[:-1])
    start = time.perf_counter()
    x_train = prep.fit_transform(X.iloc[train], y.iloc[train])
    x_test = prep.transform(X.iloc[test])
    preprocessing_seconds = time.perf_counter() - start
    # Identical fitted preprocessing and identical log1p target for every estimator.
    current = clone(template.named_steps["regressor"].regressor)
    models = {"existing_voting": current, **estimators(trees=trees, jobs=jobs)}
    results, predictions = {}, pd.DataFrame({"row_index": test, "actual_price_vnd": y.iloc[test].to_numpy()})
    for name, model in models.items():
        if model is None:
            results[name] = {"status": "dependency_missing", "install": "python -m pip install -r research/requirements.txt"}
            continue
        print(f"Fitting legacy diagnostic {name}, train={len(train)}, test={len(test)}, transformed_features={x_train.shape[1]}", flush=True)
        started = time.perf_counter()
        try:
            model.fit(x_train, np.log1p(y.iloc[train].to_numpy()))
            predicted = np.expm1(model.predict(x_test))
            results[name] = {"status": "measured", **scores(y.iloc[test], predicted), "fit_predict_seconds": time.perf_counter() - started, "parameters": model.get_params(deep=False)}
            # Estimator objects in voting parameters are represented explicitly as strings.
            results[name]["parameters"] = {k: v if isinstance(v, (str, int, float, bool, type(None))) else repr(v) for k, v in results[name]["parameters"].items()}
            predictions[name] = predicted
        except Exception as exc:
            results[name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        write_json(output / "model_metrics.partial.json", results)
    predictions.to_csv(output / "predictions.csv", index=False)
    overlaps = {}
    for key in ("url", "title", "description", "text_features"):
        overlaps[key] = len({records[i].get(key) for i in train if records[i].get(key)} & {records[i].get(key) for i in test if records[i].get(key)})
    chart = comparison_plot(results, output / "algorithm_comparison.png", "Legacy PROPERTY PRICE diagnostic; MAE/RMSE in VND")
    return {"status": "measured" if all(r["status"] == "measured" for r in results.values()) else "partial", "purpose": "legacy_price_diagnostic_only", "target": TARGET,
            "target_transform": "log1p/expm1 for every model", "sample_count": len(records), "train_size": len(train), "test_size": len(test),
            "features": list(X.columns), "transformed_feature_count": x_train.shape[1], "preprocessing_seconds": preprocessing_seconds,
            "split": "same random 80/20 seed 42 as existing code; NOT a generalization claim for future traffic",
            "cross_split_content_overlap": overlaps,
            "limitations": ["The historical 0.236 artifact has no saved training snapshot; this reruns the current snapshot, not the historical dataset.", "Unredacted listing text may contain target prices.", "Legacy source/location inconsistencies and duplicate text are retained to reproduce the existing pipeline; no improved model is deployed.", "Single fixed holdout, no hyperparameter search or confidence interval."],
            "models": results, "charts": [chart] if chart else []}


def build_traffic_supervised(records: list[dict], horizon_seconds: int, *, target="requested_rate", max_gap_factor=1.8):
    """Past-only features; labels from the same uninterrupted run/segment.

    Nearest observed horizon label must be within half a median sample interval.
    Missing intervals split segments: neither rolling windows nor labels bridge them.
    """
    if horizon_seconds <= 0 or max_gap_factor <= 1:
        raise ValueError("Horizon must be positive and gap factor must exceed one")
    if not records:
        return pd.DataFrame(), {"status": "insufficient_data", "reason": "No observations"}
    frame = pd.DataFrame(records)
    if "timestamp" not in frame or "run_id" not in frame or target not in frame:
        return pd.DataFrame(), {"status": "insufficient_data", "reason": f"timestamp, run_id and explicit target {target} required"}
    frame["timestamp"] = pd.to_datetime(frame["timestamp"].map(parse_utc_timestamp), utc=True, errors="coerce")
    invalid_timestamps = int(frame.timestamp.isna().sum())
    frame = frame.dropna(subset=["timestamp", "run_id"]).sort_values(["run_id", "timestamp"])
    duplicate_timestamps = int(frame.duplicated(["run_id", "timestamp"]).sum())
    # Duplicated observations cannot establish which state was actually measured.
    frame = frame.drop_duplicates(["run_id", "timestamp"], keep=False)
    columns = [column for column in OBSERVATIONS if column in frame]
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    rows, run_audit = [], {}
    for run_id, run in frame.groupby("run_id", sort=False):
        run = run.sort_values("timestamp").reset_index(drop=True)
        intervals = run.timestamp.diff().dt.total_seconds()
        interval = float(intervals[intervals > 0].median())
        if not np.isfinite(interval) or interval <= 0:
            continue
        gap = intervals > interval * max_gap_factor
        phase_change = run["phase"].ne(run["phase"].shift()) if "phase" in run else pd.Series(False, index=run.index)
        phase_change.iloc[0] = False
        run_audit[str(run_id)] = {"rows": len(run), "median_interval_seconds": interval, "gap_count": int(gap.sum()), "phase_boundary_count": int(phase_change.sum()), "duration_seconds": (run.timestamp.iloc[-1] - run.timestamp.iloc[0]).total_seconds(),
                                  "target_missing": int(run[target].isna().sum()), "target_skewness": float(run[target].skew()), "target_lag1_autocorrelation": float(run[target].autocorr())}
        run_audit[str(run_id)]["seasonality_assessment"] = "Not established; independent daily/weekly history is required, especially for scripted ramps."
        valid_target = run.dropna(subset=[target])
        if len(valid_target) >= 2:
            elapsed = (valid_target.timestamp - valid_target.timestamp.iloc[0]).dt.total_seconds()
            run_audit[str(run_id)]["target_linear_trend_per_second"] = float(np.polyfit(elapsed, valid_target[target], 1)[0])
        for _, segment in run.groupby((gap | phase_change).cumsum()):
            segment = segment.reset_index(drop=True)
            if len(segment) < 4:
                continue
            # pandas >=3 may infer microsecond storage; never assume int64 is ns.
            stamps = segment.timestamp.map(lambda value: value.timestamp()).to_numpy()
            for index in range(3, len(segment)):
                desired = stamps[index] + horizon_seconds
                right = int(np.searchsorted(stamps, desired))
                choices = [position for position in (right - 1, right) if index < position < len(stamps)]
                if not choices:
                    continue
                label_index = min(choices, key=lambda position: abs(stamps[position] - desired))
                if abs(stamps[label_index] - desired) > interval / 2 or pd.isna(segment.iloc[label_index][target]):
                    continue
                observation = segment.iloc[index]
                row = {"timestamp": observation.timestamp, "label_timestamp": segment.iloc[label_index].timestamp,
                       "run_id": run_id, "target": float(segment.iloc[label_index][target]), "persistence": observation[target]}
                for column in columns:
                    row[column] = observation[column]
                    row[f"{column}_lag1"] = segment.iloc[index - 1][column]
                    row[f"{column}_lag3"] = segment.iloc[index - 3][column]
                    # Includes current known observation, never a future observation.
                    row[f"{column}_rolling4"] = segment.iloc[index - 3:index + 1][column].mean()
                row["hour_sin"] = np.sin(2 * np.pi * observation.timestamp.hour / 24)
                row["hour_cos"] = np.cos(2 * np.pi * observation.timestamp.hour / 24)
                row["day_of_week"] = observation.timestamp.dayofweek
                rows.append(row)
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.dropna(subset=["persistence", "target"]).sort_values("timestamp").reset_index(drop=True)
    return result, {"raw_rows": len(records), "invalid_timestamps": invalid_timestamps, "duplicate_timestamps_removed": duplicate_timestamps, "runs": run_audit, "supervised_rows": len(result), "target": target, "horizon_seconds": horizon_seconds}


def chronological_split(frame: pd.DataFrame, test_fraction=.2):
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must lie between zero and one")
    frame = frame.sort_values("timestamp")
    cut = min(max(1, int(len(frame) * (1 - test_fraction))), len(frame) - 1)
    start_test = frame.iloc[cut].timestamp
    train = frame[(frame.timestamp < start_test) & (frame.label_timestamp < start_test)]
    test = frame[frame.timestamp >= start_test]
    return train, test


def traffic_benchmark(records, output, *, horizons=(300, 600), target="requested_rate", min_train=100, min_test=30, trees=100, jobs=2, render_plots=True):
    results = {}
    for horizon in horizons:
        frame, audit = build_traffic_supervised(records, horizon, target=target)
        result = {"data_audit": audit, "target": f"{target}_plus_{horizon}s", "status": "insufficient_data"}
        results[str(horizon)] = result
        if len(frame) < 2:
            result["reason"] = "No complete uninterrupted feature/horizon pairs; collect longer genuine traffic history."
            continue
        train, test = chronological_split(frame)
        result.update(train_size=len(train), test_size=len(test), purged_rows=len(frame) - len(train) - len(test))
        if len(train) < min_train or len(test) < min_test:
            result["reason"] = f"Requires >= {min_train} train and {min_test} test samples AFTER temporal label embargo."
            continue
        features = [column for column in frame if column not in ("timestamp", "label_timestamp", "run_id", "target", "persistence")]
        directory = output / f"horizon_{horizon}s"
        directory.mkdir(parents=True, exist_ok=True)
        frame.to_csv(directory / "supervised.csv", index=False)
        write_json(directory / "split.json", {"train_indices": train.index.tolist(), "test_indices": test.index.tolist(), "last_train_label": str(train.label_timestamp.max()), "first_test_observation": str(test.timestamp.min())})
        models = {"persistence": {"status": "measured", **scores(test.target, test.persistence)}}
        predictions = pd.DataFrame({"timestamp": test.timestamp.astype(str), "actual": test.target, "persistence": test.persistence})
        for name, model in estimators(trees=trees, jobs=jobs).items():
            if model is None:
                models[name] = {"status": "dependency_missing"}
                continue
            pipeline = Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)), ("regressor", model)])
            started = time.perf_counter()
            try:
                pipeline.fit(train[features], train.target)
                predicted = pipeline.predict(test[features])
                models[name] = {"status": "measured", **scores(test.target, predicted), "fit_predict_seconds": time.perf_counter() - started}
                predictions[name] = predicted
            except Exception as exc:
                models[name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        predictions.to_csv(directory / "predictions.csv", index=False)
        chart = comparison_plot(models, directory / "algorithm_comparison.png", f"Observed traffic forecast at +{horizon}s; MAE/RMSE messages/s") if render_plots else None
        result.update(status="measured" if all(m["status"] == "measured" for m in models.values()) else "partial", features=features, models=models, charts=[chart] if chart else [])
    telemetry_profile = profile_records(records, numeric_fields=OBSERVATIONS)
    charts = plots(records, telemetry_profile, output / "data_charts") if records and render_plots else []
    return {"purpose": "traffic_forecasting", "telemetry_profile": telemetry_profile, "data_charts": charts,
            "target_semantics": "offered demand before admission" if target == "requested_rate" else "admitted throughput (not offered demand)",
            "strategy": "chronological holdout; train labels strictly precede first test observation; no cross-run/gap rolling features or horizon labels",
            "limitations": ["Short scripted ramps cannot establish seasonal forecasting skill or production generalization.", "No hyperparameter tuning, LSTM or RL; establish genuine history and persistence baseline first.", "These metrics evaluate forecasts, not the causal benefit of enabling predictive control."], "horizons": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["legacy", "traffic"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trees", type=int, default=100)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--horizons", type=int, nargs="+", default=[300, 600])
    parser.add_argument("--target", choices=["requested_rate", "incoming_rate"], default="requested_rate")
    parser.add_argument("--min-train", type=int, default=100)
    parser.add_argument("--min-test", type=int, default=30)
    args = parser.parse_args()
    if args.trees < 1 or args.jobs < 1 or args.min_train < 2 or args.min_test < 2:
        parser.error("trees/jobs must be positive; min-train/min-test must be >= 2")
    args.output.mkdir(parents=True, exist_ok=True)
    payload = args.input.read_bytes()
    records = [json.loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
    if args.mode == "legacy":
        result = legacy_benchmark(records, args.output, trees=args.trees, jobs=args.jobs)
    else:
        result = traffic_benchmark(records, args.output, horizons=args.horizons, target=args.target, min_train=args.min_train, min_test=args.min_test, trees=args.trees, jobs=args.jobs)
    source_files = [Path(__file__), Path(__file__).with_name("data_audit.py")]
    if args.mode == "legacy":
        source_files.extend(Path(__file__).parents[1] / name for name in ("modeling/price_model.py", "processing/text_enrichment.py", "processing/feature_engineering.py", "agents/safety.py"))
    result.update(created_at=datetime.now(timezone.utc).isoformat(), input_path=str(args.input), input_sha256=hashlib.sha256(payload).hexdigest(), versions=versions(),
                  source_sha256={str(path.relative_to(Path(__file__).parents[1])): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files},
                  command_parameters=vars(args) | {"input": str(args.input), "output": str(args.output)})
    write_json(args.output / "metrics.json", result)
    print(json.dumps({"output": str(args.output / "metrics.json"), "status": result.get("status", {k: v["status"] for k, v in result.get("horizons", {}).items()})}))


if __name__ == "__main__":
    main()
