from __future__ import annotations

# Price model training pipeline and helpers (numeric, categorical, text, ensemble)
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable
import datetime
import os
import shutil

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.ensemble import VotingRegressor
from sklearn.linear_model import Ridge, SGDRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler
from processing.price_anomaly import get_anomaly_training_policy


NUMERIC_FEATURES = [
    "area_m2",
    "bedroom_count",
    "bathroom_count",
    "floor_count",
    "front_width_m",
    "road_width_m",
]

CATEGORICAL_FEATURES = [
    "property_type",
    "direction",
    "legal",
    "listing_type",
    "province_slug",
    "district_slug",
    "ward_slug",
    "project_hint",
]

TEXT_FEATURE = "text_features"
TARGET = "price_vnd"
DEFAULT_MIN_TRAINING_RECORDS = 200


def flatten_text_column(values):
    # Flatten transformer column output into a 1-D pandas Series
    if hasattr(values, "squeeze"):
        values = values.squeeze(axis=1)
    return pd.Series(values).fillna("")


def to_dense_matrix(values):
    # Convert sparse matrix outputs to dense ndarray when needed
    return values.toarray() if hasattr(values, "toarray") else values


def build_feature_frame(records: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    # Build DataFrame and ensure `text_features` is present by concatenating key fields
    rows = []
    for record in records:
        row = dict(record)
        row[TEXT_FEATURE] = row.get(TEXT_FEATURE) or " | ".join(
            str(part)
            for part in [
                row.get("title") or "",
                row.get("property_type") or "",
                row.get("province_slug") or "",
                row.get("district_slug") or "",
                row.get("legal") or "",
                row.get("description") or "",
            ]
            if part
        )
        rows.append(row)
    return pd.DataFrame(rows)


def build_regression_pipeline() -> Pipeline:
    # Construct sklearn Pipeline: numeric, categorical, text preprocessing + ensemble regressor
    numeric_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )

    categorical_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )

    text_pipeline = Pipeline(
        [
            ("flatten", FunctionTransformer(flatten_text_column, validate=False)),
            ("tfidf", TfidfVectorizer(max_features=1800, ngram_range=(1, 2), min_df=1)),
        ]
    )

    preprocessor = ColumnTransformer(
        [
            ("num", numeric_pipeline, NUMERIC_FEATURES),
            ("cat", categorical_pipeline, CATEGORICAL_FEATURES),
            ("txt", text_pipeline, [TEXT_FEATURE]),
        ]
    )

    base_regressor = VotingRegressor(
        estimators=[
            ("ridge", Ridge(alpha=3.0)),
            (
                "hgb",
                HistGradientBoostingRegressor(
                    learning_rate=0.06,
                    max_leaf_nodes=31,
                    l2_regularization=0.05,
                    random_state=42,
                ),
            ),
            (
                "sgd",
                SGDRegressor(
                    loss="squared_error",
                    penalty="elasticnet",
                    alpha=0.0001,
                    max_iter=2000,
                    tol=1e-3,
                    random_state=42,
                ),
            ),
        ]
    )

    regressor = TransformedTargetRegressor(
        regressor=base_regressor,
        func=np.log1p,
        inverse_func=np.expm1,
    )

    return Pipeline(
        [
            ("preprocessor", preprocessor),
            ("to_dense", FunctionTransformer(to_dense_matrix, accept_sparse=True)),
            ("regressor", regressor),
        ]
    )


@dataclass
class TrainResult:
    model_path: str
    sample_count: int
    metrics: Dict[str, float]


class RealEstatePriceModel:
    def __init__(self, model=None, metadata: Dict[str, Any] | None = None) -> None:
        self.model = model or build_regression_pipeline()
        self.metadata = metadata or {}

    def train(self, records: Iterable[Dict[str, Any]], model_path: str, metrics_path: str | None = None) -> TrainResult:
        # Train model pipeline, compute metrics, and save versioned artifacts
        frame = build_feature_frame(records)
        if frame.empty or TARGET not in frame.columns:
            raise ValueError("Training data must include records with price_vnd.")
        if "is_model_candidate" in frame.columns:
            frame = frame[frame["is_model_candidate"].fillna(True)]
        if get_anomaly_training_policy() == "EXCLUDE" and "is_price_anomaly" in frame.columns:
            frame = frame[~frame["is_price_anomaly"].fillna(False)]
        frame = frame[frame[TARGET].notna() & (frame[TARGET] > 0)]

        min_records = int(os.environ.get("MIN_RECORDS_FOR_TRAINING", DEFAULT_MIN_TRAINING_RECORDS))
        if len(frame) < min_records:
            raise ValueError(
                f"Need at least {min_records} candidate listings with valid target price to train the model."
            )

        for feature in NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]:
            if feature not in frame.columns:
                frame[feature] = None

        X = frame[NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]]
        y = frame[TARGET].astype(float)

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        self.model.fit(X_train, y_train)
        predictions = self.model.predict(X_test)
        residuals = np.abs(y_test.to_numpy() - predictions)
        percentage_errors = residuals / np.maximum(y_test.to_numpy(), 1.0)

        metrics = {
            "sample_count": float(len(frame)),
            "mae_vnd": float(mean_absolute_error(y_test, predictions)),
            "rmse_vnd": float(np.sqrt(mean_squared_error(y_test, predictions))),
            "r2": float(r2_score(y_test, predictions)) if len(y_test) > 1 else 0.0,
            "train_size": float(len(X_train)),
            "test_size": float(len(X_test)),
            "median_absolute_percentage_error": float(np.median(percentage_errors)),
        }

        requested_path = Path(model_path) if model_path else Path("artifacts/models/price_model.joblib")

        # Versioned saving
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        models_dir = requested_path.parent
        models_dir.mkdir(parents=True, exist_ok=True)
        versioned_name = f"price_model_v{timestamp}.joblib"
        versioned_path = models_dir / versioned_name

        # Save metadata
        metadata = {
            "version": timestamp,
            "model_path": str(versioned_path),
            "sample_count": int(len(frame)),
            "metrics": metrics,
            "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "residual_quantiles_vnd": {
                "p50": float(np.quantile(residuals, 0.50)) if len(residuals) else 0.0,
                "p80": float(np.quantile(residuals, 0.80)) if len(residuals) else 0.0,
                "p90": float(np.quantile(residuals, 0.90)) if len(residuals) else 0.0,
            },
            "target_quantiles_vnd": {
                "p10": float(np.quantile(y, 0.10)),
                "p50": float(np.quantile(y, 0.50)),
                "p90": float(np.quantile(y, 0.90)),
            },
        }
        self.metadata = metadata
        model_bundle = {"model": self.model, "metadata": metadata}
        joblib.dump(model_bundle, versioned_path)
        shutil.copyfile(versioned_path, requested_path)
        if metrics_path:
            metrics_path_obj = Path(metrics_path)
            metrics_path_obj.parent.mkdir(parents=True, exist_ok=True)
            metrics_path_obj.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        # Write metadata next to models
        metadata_path = models_dir / f"metadata_v{timestamp}.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        # Update current model pointer (copy to stable path)
        current_path = models_dir.parent / "price_model_current.joblib"
        try:
            shutil.copyfile(versioned_path, current_path)
        except Exception:
            # best-effort; not fatal
            pass

        return TrainResult(model_path=str(requested_path), sample_count=len(frame), metrics=metrics)

    @classmethod
    def load(cls, model_path: str) -> "RealEstatePriceModel":
        payload = joblib.load(model_path)
        if isinstance(payload, dict) and "model" in payload:
            return cls(model=payload["model"], metadata=payload.get("metadata") or {})
        return cls(model=payload, metadata={})

    def predict(self, record: Dict[str, Any]) -> Dict[str, Any]:
        frame = build_feature_frame([record])
        for feature in NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]:
            if feature not in frame.columns:
                frame[feature] = None
        X = frame[NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]]
        price_vnd = float(self.model.predict(X)[0])
        interval_vnd = self._prediction_interval(price_vnd)
        confidence_score = self._confidence_score(record, price_vnd, interval_vnd)
        area_m2 = record.get("area_m2")
        price_per_m2_vnd = price_vnd / area_m2 if isinstance(area_m2, (int, float)) and area_m2 > 0 else None
        return {
            "predicted_price_vnd": price_vnd,
            "predicted_price_billion_vnd": round(price_vnd / 1_000_000_000, 4),
            "price_per_m2_vnd": price_per_m2_vnd,
            "confidence_low_vnd": max(price_vnd - interval_vnd, 0.0),
            "confidence_high_vnd": price_vnd + interval_vnd,
            "confidence_score": confidence_score,
            "feature_quality_score": self._feature_quality_score(record),
            "explanations": self._explain_prediction(record, interval_vnd),
        }

    def _prediction_interval(self, price_vnd: float) -> float:
        residuals = self.metadata.get("residual_quantiles_vnd") or {}
        learned_interval = float(residuals.get("p80") or 0.0)
        fallback_interval = price_vnd * 0.12
        return max(learned_interval, fallback_interval, 50_000_000.0)

    def _feature_quality_score(self, record: Dict[str, Any]) -> float:
        important_features = [
            "area_m2",
            "bedroom_count",
            "bathroom_count",
            "property_type",
            "province_slug",
            "district_slug",
            "description",
        ]
        present = sum(record.get(feature) not in (None, "") for feature in important_features)
        return round(present / len(important_features), 3)

    def _confidence_score(self, record: Dict[str, Any], price_vnd: float, interval_vnd: float) -> float:
        quality = self._feature_quality_score(record)
        interval_penalty = min(interval_vnd / max(price_vnd, 1.0), 0.35)
        score = 0.45 + quality * 0.45 - interval_penalty * 0.55
        return round(float(np.clip(score, 0.30, 0.93)), 3)

    def _explain_prediction(self, record: Dict[str, Any], interval_vnd: float) -> list[str]:
        explanations = []
        if record.get("area_m2"):
            explanations.append("Area is the strongest numeric signal for the valuation.")
        if record.get("district_slug"):
            explanations.append("District and province help the model capture local market level.")
        if record.get("description") or record.get("text_features"):
            explanations.append("Listing text contributes semantic signals such as legal status, furniture, and frontage.")
        explanations.append(f"Confidence range uses the model validation residual, currently about {interval_vnd / 1_000_000_000:.2f}B VND.")
        return explanations
