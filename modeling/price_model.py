from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable
import datetime
import shutil

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.compose import TransformedTargetRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingRegressor, VotingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler


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


def flatten_text_column(values):
    if hasattr(values, "squeeze"):
        values = values.squeeze(axis=1)
    return pd.Series(values).fillna("")


def build_feature_frame(records: Iterable[Dict[str, Any]]) -> pd.DataFrame:
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
            ("tfidf", TfidfVectorizer(max_features=3000, ngram_range=(1, 2))),
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
            ("hgb", HistGradientBoostingRegressor(max_depth=8, learning_rate=0.06, max_iter=250, random_state=42)),
        ]
    )

    regressor = TransformedTargetRegressor(
        regressor=base_regressor,
        func=np.log1p,
        inverse_func=np.expm1,
    )

    return Pipeline([("preprocessor", preprocessor), ("regressor", regressor)])


@dataclass
class TrainResult:
    model_path: str
    sample_count: int
    metrics: Dict[str, float]


class RealEstatePriceModel:
    def __init__(self, model=None) -> None:
        self.model = model or build_regression_pipeline()

    def train(self, records: Iterable[Dict[str, Any]], model_path: str, metrics_path: str | None = None) -> TrainResult:
        frame = build_feature_frame(records)
        frame = frame[frame.get("is_model_candidate", True).fillna(True)]
        frame = frame[frame[TARGET].notna() & (frame[TARGET] > 0)]

        if len(frame) < 200:
            raise ValueError("Need at least 200 candidate listings with valid target price to train the model.")

        X = frame[NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]]
        y = frame[TARGET].astype(float)

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        self.model.fit(X_train, y_train)
        predictions = self.model.predict(X_test)

        metrics = {
            "sample_count": float(len(frame)),
            "mae_vnd": float(mean_absolute_error(y_test, predictions)),
            "rmse_vnd": float(np.sqrt(mean_squared_error(y_test, predictions))),
            "r2": float(r2_score(y_test, predictions)) if len(y_test) > 1 else 0.0,
            "train_size": float(len(X_train)),
            "test_size": float(len(X_test)),
        }

        requested_path = Path(model_path) if model_path else Path("artifacts/models/price_model.joblib")

        # Versioned saving
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        models_dir = requested_path.parent
        models_dir.mkdir(parents=True, exist_ok=True)
        versioned_name = f"price_model_v{timestamp}.joblib"
        versioned_path = models_dir / versioned_name
        joblib.dump(self.model, versioned_path)
        shutil.copyfile(versioned_path, requested_path)

        # Save metadata
        metadata = {
            "version": timestamp,
            "model_path": str(versioned_path),
            "sample_count": int(len(frame)),
            "metrics": metrics,
        }
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
        return cls(model=joblib.load(model_path))

    def predict(self, record: Dict[str, Any]) -> Dict[str, Any]:
        frame = build_feature_frame([record])
        X = frame[NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]]
        price_vnd = float(self.model.predict(X)[0])
        return {
            "predicted_price_vnd": price_vnd,
            "predicted_price_billion_vnd": round(price_vnd / 1_000_000_000, 4),
        }
