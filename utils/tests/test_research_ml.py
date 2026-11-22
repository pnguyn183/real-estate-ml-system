"""Guard the scientifically consequential split/label boundaries, not scores."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import TransformedTargetRegressor
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline

from research.benchmark import (
    build_traffic_supervised,
    chronological_split,
    comparison_plot,
    evaluate_price_candidate_cv,
    price_group_cv_folds,
    scores,
    select_price_candidate,
    traffic_benchmark,
)
from research.data_audit import profile_records


def observations(count=100, run="one", start=None, step=5):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{"timestamp": (start + timedelta(seconds=i * step)).isoformat(), "run_id": run,
             "requested_rate": float(i), "incoming_rate": float(i / 2), "throughput": float(i / 3)} for i in range(count)]


class RecordingTransformer(BaseEstimator, TransformerMixin):
    fit_sizes = []

    def fit(self, X, y=None):
        type(self).fit_sizes.append(len(X))
        return self

    def transform(self, X):
        return np.asarray(X)


def test_price_group_cv_is_deterministic_and_excludes_outer_test():
    frame = pd.DataFrame({"property_group": [f"group-{index // 2}" for index in range(24)]})
    development = np.arange(20)
    first = price_group_cv_folds(
        frame, development, group_column="property_group", n_splits=3, seed=42
    )
    second = price_group_cv_folds(
        frame, development, group_column="property_group", n_splits=3, seed=42
    )

    assert len(first) == len(second) == 3
    assert sorted(np.concatenate([fold["validation"] for fold in first]).tolist()) == development.tolist()
    for left, right in zip(first, second):
        assert np.array_equal(left["train"], right["train"])
        assert np.array_equal(left["validation"], right["validation"])
        assert not (set(left["train"]) | set(left["validation"])) & set(range(20, 24))
        train_groups = set(frame.iloc[left["train"]].property_group)
        validation_groups = set(frame.iloc[left["validation"]].property_group)
        assert train_groups.isdisjoint(validation_groups)


def test_price_group_cv_rejects_too_few_groups():
    frame = pd.DataFrame({"property_group": ["one", "one", "two", "two"]})
    with pytest.raises(ValueError, match="at least 3 property groups"):
        price_group_cv_folds(
            frame, np.arange(4), group_column="property_group", n_splits=3, seed=42
        )


def test_price_candidate_selection_never_uses_outer_test_metrics():
    candidates = {
        "worse_cv_better_test": {
            "status": "measured",
            "cv_metrics": {"rmse": {"mean": 5.0}},
            "winner_outer_test_metrics": {"rmse": 1.0},
        },
        "better_cv_worse_test": {
            "status": "measured",
            "cv_metrics": {"rmse": {"mean": 4.0}},
            "winner_outer_test_metrics": {"rmse": 999.0},
        },
    }
    assert select_price_candidate(candidates) == "better_cv_worse_test"


def test_price_cv_refits_complete_pipeline_inside_every_fold():
    RecordingTransformer.fit_sizes = []
    template = Pipeline([
        ("preprocessor", RecordingTransformer()),
        ("regressor", TransformedTargetRegressor(
            regressor=LinearRegression(), func=np.log1p, inverse_func=np.expm1
        )),
    ])
    X = pd.DataFrame({"feature": np.arange(12, dtype=float)})
    y = pd.Series(np.arange(12, dtype=float) + 10)
    folds = [
        {"fold": 1, "train": np.arange(0, 6), "validation": np.arange(6, 9)},
        {"fold": 2, "train": np.arange(3, 12), "validation": np.arange(0, 3)},
    ]

    metrics, predictions = evaluate_price_candidate_cv(
        template, LinearRegression(), X, y, folds
    )

    assert RecordingTransformer.fit_sizes == [6, 9]
    assert [result["fold"] for result in metrics] == [1, 2]
    assert sorted(row["row_index"] for row in predictions) == [0, 1, 2, 6, 7, 8]


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


@pytest.mark.parametrize("actual,predicted,expected_mae,expected_rmse", [
    ([20, 20, 20], [20, 20, 20], 0, 0),
    ([20, 20, 20], [10, 30, 20], 20 / 3, (200 / 3) ** .5),
    ([20], [15], 5, 5),
])
def test_constant_or_single_target_has_undefined_r2(actual, predicted, expected_mae, expected_rmse):
    result = scores(actual, predicted)
    assert result["r2"] is None
    assert result["mae"] == pytest.approx(expected_mae)
    assert result["rmse"] == pytest.approx(expected_rmse)


def test_varying_target_retains_measured_r2():
    result = scores(pd.Series([10, 20, 30]), [10, 20, 30])
    assert result == {"r2": 1.0, "mae": 0.0, "rmse": 0.0}


def test_mdape_is_percent_and_ignores_zero_actual_values():
    result = scores([0, 100, 200], [999, 90, 240], include_mdape=True)
    assert result["mdape_percent"] == pytest.approx(15.0)


def test_mdape_is_undefined_when_every_actual_value_is_zero():
    result = scores([0, 0], [1, 2], include_mdape=True)
    assert result["mdape_percent"] is None


def test_model_plot_accepts_undefined_r2_without_inventing_score(tmp_path):
    results = {"persistence": {"status": "measured", **scores([20, 20], [20, 20])}}
    output = tmp_path / "constant_target.png"
    assert comparison_plot(results, output, "Constant-target diagnostic") == str(output)
    assert output.is_file() and output.stat().st_size > 0
    assert results["persistence"]["r2"] is None


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
