from __future__ import annotations

# Price model training pipeline and helpers (numeric, categorical, text, ensemble)
import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable
import datetime
import os
import shutil
import tempfile
import logging

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
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler
from processing.price_anomaly import get_anomaly_training_policy
from agents.safety import real_records
from processing.feature_engineering import GEO_CATEGORICAL_FEATURES, GEO_NUMERIC_FEATURES, enrich_geographic_features
from processing.text_enrichment import TEXT_EMBEDDING_DIMENSIONS, enrich_text_record


BASE_NUMERIC_FEATURES = [
    "area_m2",
    "bedroom_count",
    "bathroom_count",
    "floor_count",
    "front_width_m",
    "road_width_m",
]

BASE_CATEGORICAL_FEATURES = [
    "property_type",
    "direction",
    "legal",
    "listing_type",
    "province_slug",
    "district_slug",
    "ward_slug",
    "project_hint",
]

NUMERIC_FEATURES = BASE_NUMERIC_FEATURES + [
    *GEO_NUMERIC_FEATURES,
    "extracted_bedrooms",
    "extracted_bathrooms",
    "extracted_amenity_count",
    *[f"text_embedding_{index:03d}" for index in range(TEXT_EMBEDDING_DIMENSIONS)],
]

CATEGORICAL_FEATURES = BASE_CATEGORICAL_FEATURES + [
    *GEO_CATEGORICAL_FEATURES,
    "extracted_direction",
    "extracted_furnishing",
    "extracted_legal_status",
]

TEXT_FEATURE = "text_features"
TARGET = "price_vnd"
DEFAULT_MIN_TRAINING_RECORDS = 200
PRICE_GROUP_COLUMN = "__property_group_key"
PRICE_SPLIT_SEED = 42

# Listing descriptions commonly repeat the asking price. Feeding that text to
# TF-IDF/embeddings would expose the target to the model, so currency amounts and
# explicitly price-labelled numbers are redacted on both training and inference.
_CURRENCY_AMOUNT = re.compile(
    r"(?<!\w)\d[\d\s.,]*(?:tỷ|ty|triệu|trieu|million|billion|vnd|vnđ|đồng|dong|₫|t)(?!\w)(?:\s*\d{1,3})?",
    re.IGNORECASE,
)
_PRICE_LABEL_AMOUNT = re.compile(
    r"\b(?:giá|gia|price)\s*[:=~-]?\s*\d[\d\s.,]*",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


def sanitize_listing_text(value: Any, target: Any = None) -> str:
    """Remove target-price representations while retaining non-price text."""
    text = str(value or "")
    if target is not None:
        try:
            integer_target = int(float(target))
        except (TypeError, ValueError, OverflowError):
            integer_target = None
        if integer_target is not None:
            variants = {
                str(integer_target),
                f"{integer_target:,}",
                f"{integer_target:,}".replace(",", "."),
                f"{integer_target:,}".replace(",", " "),
            }
            for variant in sorted(variants, key=len, reverse=True):
                text = re.sub(re.escape(variant), " [PRICE_REDACTED] ", text, flags=re.IGNORECASE)
    text = _CURRENCY_AMOUNT.sub(" [PRICE_REDACTED] ", text)
    text = _PRICE_LABEL_AMOUNT.sub(" [PRICE_REDACTED] ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _normal_identity(value: Any) -> str:
    return _WHITESPACE.sub(" ", str(value or "").strip().lower())


def _identity_hash(kind: str, values: Iterable[Any]) -> str:
    payload = json.dumps([_normal_identity(value) for value in values], ensure_ascii=False, separators=(",", ":"))
    return f"{kind}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def property_group_key(record: Dict[str, Any]) -> str:
    """Return a target-independent key used to keep one property in one split.

    Explicit property identities take precedence. When they are unavailable, a
    conservative physical signature is used before falling back to listing IDs.
    The method is auditable but remains a heuristic for legacy rows without a
    durable property identifier.
    """
    for field in ("property_group_id", "property_id", "original_record_id"):
        if record.get(field) not in (None, ""):
            return _identity_hash(field, [record[field]])

    address = record.get("address") or record.get("street_address")
    area = record.get("area_m2")
    if address not in (None, "") and area not in (None, ""):
        return _identity_hash(
            "physical_signature",
            [
                address,
                area,
                record.get("property_type"),
                record.get("province_slug"),
                record.get("district_slug"),
                record.get("ward_slug"),
                record.get("bedroom_count"),
                record.get("bathroom_count"),
                record.get("floor_count"),
            ],
        )

    location = (record.get("location_slug") or record.get("ward_slug")
                or record.get("district_slug") or record.get("province_slug"))
    if location not in (None, "") and record.get("area_m2") not in (None, ""):
        return _identity_hash(
            "conservative_physical_block",
            [
                location,
                record.get("area_m2"),
                record.get("property_type"),
                record.get("bedroom_count"),
                record.get("bathroom_count"),
                record.get("floor_count"),
                record.get("front_width_m"),
                record.get("road_width_m"),
            ],
        )
    if record.get("source_listing_id") not in (None, ""):
        return _identity_hash("source_listing_id", [record.get("source"), record["source_listing_id"]])
    if record.get("listing_id") not in (None, ""):
        return _identity_hash("listing_id", [record.get("source"), record["listing_id"]])
    return _identity_hash("target_free_fallback", [
        location, record.get("area_m2"), record.get("property_type"),
        record.get("bedroom_count"), record.get("bathroom_count"),
        record.get("floor_count"), record.get("front_width_m"), record.get("road_width_m"),
    ])


def _duplicate_key(record: Dict[str, Any]) -> str:
    title = sanitize_listing_text(record.get("title"), record.get(TARGET))
    description = sanitize_listing_text(record.get("description"), record.get(TARGET))
    if title or description:
        return _identity_hash(
            "exact_content",
            [
                title,
                description,
                record.get(TARGET),
                record.get("area_m2"),
                record.get("property_type"),
                record.get("province_slug"),
                record.get("district_slug"),
                record.get("ward_slug"),
                record.get("bedroom_count"),
                record.get("bathroom_count"),
                record.get("floor_count"),
                record.get("front_width_m"),
                record.get("road_width_m"),
            ],
        )
    for field in ("canonical_url", "url", "listing_fingerprint"):
        if record.get(field) not in (None, ""):
            return _identity_hash(field, [record[field]])
    if record.get("source_listing_id") not in (None, ""):
        return _identity_hash("source_listing_id", [record.get("source"), record["source_listing_id"]])
    return _identity_hash("empty_content", [record.get("source"), record.get("listing_id")])


def publish_model(source: Path, destination: Path) -> None:
    """Readers see the complete old or new artifact, never a partial copy."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            with source.open("rb") as original:
                shutil.copyfileobj(original, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def flatten_text_column(values):
    # Flatten transformer column output into a 1-D pandas Series
    if hasattr(values, "squeeze"):
        values = values.squeeze(axis=1)
    return pd.Series(values).fillna("")


def to_dense_matrix(values):
    # Convert sparse matrix outputs to dense ndarray when needed
    return values.toarray() if hasattr(values, "toarray") else values


def prepare_price_dataset(records: Iterable[Dict[str, Any]]) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Filter, de-duplicate and annotate price rows before any split or fit."""
    real = real_records(records)
    candidates: list[Dict[str, Any]] = []
    raw_price_text_rows = 0
    for source in real:
        if source.get("is_model_candidate") is False:
            continue
        if get_anomaly_training_policy() == "EXCLUDE" and source.get("is_price_anomaly"):
            continue
        try:
            target = float(source.get(TARGET))
        except (TypeError, ValueError, OverflowError):
            continue
        if not np.isfinite(target) or target <= 0:
            continue
        row = dict(source)
        row[TARGET] = target
        row[PRICE_GROUP_COLUMN] = property_group_key(row)
        redacted = False
        for field in ("title", "description", TEXT_FEATURE):
            if field not in row:
                continue
            cleaned = sanitize_listing_text(row.get(field), target)
            redacted = redacted or cleaned != str(row.get(field) or "")
            row[field] = cleaned
        raw_price_text_rows += int(redacted)
        candidates.append(row)

    # Keep the most recent occurrence of an exact listing identity. A conservative
    # target-free physical block keeps many likely relistings together, but legacy
    # rows without durable property IDs can still evade this heuristic.
    by_duplicate_key: dict[str, Dict[str, Any]] = {}
    for row in candidates:
        by_duplicate_key[_duplicate_key(row)] = row
    deduplicated = list(by_duplicate_key.values())
    frame = build_feature_frame(deduplicated)
    fingerprint_rows = sorted(
        json.dumps(
            {
                "property_group": row[PRICE_GROUP_COLUMN],
                "duplicate_key": _duplicate_key(row),
                "target": row[TARGET],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in deduplicated
    )
    audit = {
        "input_rows": len(real),
        "valid_candidate_rows": len(candidates),
        "duplicates_removed": len(candidates) - len(deduplicated),
        "output_rows": len(frame),
        "property_group_count": int(frame[PRICE_GROUP_COLUMN].nunique()) if not frame.empty else 0,
        "rows_with_price_text_redacted": raw_price_text_rows,
        "dataset_sha256": hashlib.sha256("\n".join(fingerprint_rows).encode("utf-8")).hexdigest(),
        "property_group_method": "explicit property id; address signature; conservative location/physical-attribute block; source/listing identity; URL-, price- and free-text-independent fallback",
        "target_leakage_control": "currency and explicitly price-labelled amounts redacted before text features and embeddings",
    }
    return frame, audit


def group_train_validation_test_split(
    frame: pd.DataFrame,
    *,
    seed: int = PRICE_SPLIT_SEED,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> Dict[str, np.ndarray]:
    """Deterministically split rows while keeping every property group isolated."""
    if PRICE_GROUP_COLUMN not in frame:
        raise ValueError(f"Price dataset must contain {PRICE_GROUP_COLUMN}.")
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1 - train_fraction:
        raise ValueError("train_fraction and validation_fraction must leave a non-empty test fraction.")
    group_count = int(frame[PRICE_GROUP_COLUMN].nunique())
    if group_count < 4:
        raise ValueError("Need at least four distinct property groups for train/validation/test splitting.")

    positions = np.arange(len(frame))
    holdout_fraction = 1 - train_fraction
    first = GroupShuffleSplit(n_splits=1, test_size=holdout_fraction, random_state=seed)
    train_positions, holdout_positions = next(first.split(positions, groups=frame[PRICE_GROUP_COLUMN]))
    holdout = frame.iloc[holdout_positions]
    relative_test_fraction = (1 - train_fraction - validation_fraction) / holdout_fraction
    second = GroupShuffleSplit(n_splits=1, test_size=relative_test_fraction, random_state=seed + 1)
    validation_relative, test_relative = next(
        second.split(np.arange(len(holdout)), groups=holdout[PRICE_GROUP_COLUMN])
    )
    result = {
        "train": np.sort(train_positions),
        "validation": np.sort(holdout_positions[validation_relative]),
        "test": np.sort(holdout_positions[test_relative]),
    }
    group_sets = {
        name: set(frame.iloc[indexes][PRICE_GROUP_COLUMN])
        for name, indexes in result.items()
    }
    if any(group_sets[left] & group_sets[right] for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))):
        raise RuntimeError("Property group leakage detected across price-model splits.")
    return result


def build_feature_frame(records: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    # Build one schema-stable frame for training and inference. Geographic and text
    # enrichment is deterministic and has no target-derived input.
    rows = []
    for record in records:
        row = dict(record)
        target = row.get(TARGET)
        for field in ("title", "description", TEXT_FEATURE):
            if field in row:
                row[field] = sanitize_listing_text(row.get(field), target)
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
        row.update(enrich_geographic_features(row))
        text_enrichment = enrich_text_record(row)
        row.update({key: value for key, value in text_enrichment.items() if key != "text_embedding"})
        for index, value in enumerate(text_enrichment["text_embedding"]):
            row[f"text_embedding_{index:03d}"] = value
        rows.append(row)
    frame = pd.DataFrame(rows)
    # Missing fields are explicit columns rather than an inference-time schema
    # accident. sklearn imputers then apply the same handling on both paths.
    for feature in NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]:
        if feature not in frame.columns:
            frame[feature] = None
    return frame


def build_regression_pipeline(
    *, include_geographic_features: bool = True, include_text_enrichment: bool = True
) -> Pipeline:
    # Construct sklearn Pipeline: numeric, categorical, text preprocessing + ensemble regressor
    numeric_features = list(BASE_NUMERIC_FEATURES)
    categorical_features = list(BASE_CATEGORICAL_FEATURES)
    if include_geographic_features:
        numeric_features.extend(GEO_NUMERIC_FEATURES)
        categorical_features.extend(GEO_CATEGORICAL_FEATURES)
    if include_text_enrichment:
        numeric_features.extend(
            ["extracted_bedrooms", "extracted_bathrooms", "extracted_amenity_count"]
            + [f"text_embedding_{index:03d}" for index in range(TEXT_EMBEDDING_DIMENSIONS)]
        )
        categorical_features.extend(["extracted_direction", "extracted_furnishing", "extracted_legal_status"])
    numeric_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
        ]
    )

    categorical_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent", keep_empty_features=True)),
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
            ("num", numeric_pipeline, numeric_features),
            ("cat", categorical_pipeline, categorical_features),
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
        frame, data_audit = prepare_price_dataset(records)
        if frame.empty or TARGET not in frame.columns:
            raise ValueError("Training data must include records with price_vnd.")

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

        split = group_train_validation_test_split(frame)
        X_train, y_train = X.iloc[split["train"]], y.iloc[split["train"]]
        X_validation, y_validation = X.iloc[split["validation"]], y.iloc[split["validation"]]
        X_test, y_test = X.iloc[split["test"]], y.iloc[split["test"]]
        self.model.fit(X_train, y_train)
        predictions = self.model.predict(X_test)
        validation_predictions = self.model.predict(X_validation)
        residuals = np.abs(y_test.to_numpy() - predictions)
        percentage_errors = residuals / np.maximum(y_test.to_numpy(), 1.0)
        validation_residuals = np.abs(y_validation.to_numpy() - validation_predictions)

        metrics = {
            "sample_count": float(len(frame)),
            "mae_vnd": float(mean_absolute_error(y_test, predictions)),
            "rmse_vnd": float(np.sqrt(mean_squared_error(y_test, predictions))),
            "r2": float(r2_score(y_test, predictions)) if len(y_test) > 1 else 0.0,
            "train_size": float(len(X_train)),
            "validation_size": float(len(X_validation)),
            "test_size": float(len(X_test)),
            "median_absolute_percentage_error": float(np.median(percentage_errors)),
            "validation_mae_vnd": float(mean_absolute_error(y_validation, validation_predictions)),
            "validation_rmse_vnd": float(np.sqrt(mean_squared_error(y_validation, validation_predictions))),
            "validation_median_absolute_percentage_error": float(
                np.median(validation_residuals / np.maximum(y_validation.to_numpy(), 1.0))
            ),
        }

        # The held-out test stays untouched. Validation is not used for model
        # selection yet, so it can safely join training for the published model.
        fit_positions = np.concatenate([split["train"], split["validation"]])
        self.model.fit(X.iloc[fit_positions], y.iloc[fit_positions])

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
            "evaluation": {
                "split_strategy": "property_group_holdout_70_15_15",
                "random_seed": PRICE_SPLIT_SEED,
                "train_fraction": 0.70,
                "validation_fraction": 0.15,
                "test_fraction": 0.15,
                "mdape_unit": "fraction",
                "property_group_column": PRICE_GROUP_COLUMN,
                "property_group_counts": {
                    name: int(frame.iloc[indexes][PRICE_GROUP_COLUMN].nunique())
                    for name, indexes in split.items()
                },
                "property_group_ids": {
                    name: sorted(set(frame.iloc[indexes][PRICE_GROUP_COLUMN]))
                    for name, indexes in split.items()
                },
                "group_overlap_count": 0,
                "test_used_for_model_selection": False,
            },
            "data_quality": data_audit,
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
            "feature_schema": {
                "numeric": NUMERIC_FEATURES,
                "categorical": CATEGORICAL_FEATURES,
                "text": TEXT_FEATURE,
                "text_embedding_dimensions": TEXT_EMBEDDING_DIMENSIONS,
                "geographic_features": list(GEO_NUMERIC_FEATURES + GEO_CATEGORICAL_FEATURES),
            },
        }
        self.metadata = metadata
        model_bundle = {"model": self.model, "metadata": metadata}
        joblib.dump(model_bundle, versioned_path)
        publish_model(versioned_path, requested_path)
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
            publish_model(versioned_path, current_path)
        except OSError:
            logging.getLogger(__name__).warning("Could not update optional current-model alias %s", current_path, exc_info=True)

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


def evaluate_feature_variants(records: Iterable[Dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Evaluate the same held-out split across baseline and optional new features.

    The function is intentionally explicit rather than automatically publishing a
    winner. It exists to produce real ablation results only when a caller supplies
    sufficient real training records.
    """
    frame, _ = prepare_price_dataset(records)
    if TARGET not in frame.columns:
        raise ValueError("Feature evaluation data must include price_vnd.")
    min_records = int(os.environ.get("MIN_RECORDS_FOR_TRAINING", DEFAULT_MIN_TRAINING_RECORDS))
    if len(frame) < min_records:
        raise ValueError(f"Need at least {min_records} valid candidate listings for feature evaluation.")
    for feature in NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE]:
        if feature not in frame.columns:
            frame[feature] = None
    split = group_train_validation_test_split(frame)
    train_indices = split["train"]
    validation_indices = split["validation"]
    test_indices = split["test"]
    y = frame[TARGET].astype(float)
    variants = {
        "current_model_baseline": (False, False),
        "baseline_plus_geographic": (True, False),
        "baseline_plus_text_enrichment": (False, True),
        "geographic_plus_text_enrichment": (True, True),
    }
    results: dict[str, dict[str, float]] = {}
    for name, (with_geo, with_text) in variants.items():
        model = build_regression_pipeline(
            include_geographic_features=with_geo,
            include_text_enrichment=with_text,
        )
        model.fit(frame.iloc[train_indices], y.iloc[train_indices])
        predictions = model.predict(frame.iloc[test_indices])
        validation_predictions = model.predict(frame.iloc[validation_indices])
        actual = y.iloc[test_indices]
        validation_actual = y.iloc[validation_indices]
        results[name] = {
            "mae_vnd": float(mean_absolute_error(actual, predictions)),
            "rmse_vnd": float(np.sqrt(mean_squared_error(actual, predictions))),
            "r2": float(r2_score(actual, predictions)),
            "median_absolute_error_vnd": float(np.median(np.abs(actual.to_numpy() - predictions))),
            "train_size": float(len(train_indices)),
            "validation_size": float(len(validation_indices)),
            "test_size": float(len(test_indices)),
            "validation_mae_vnd": float(mean_absolute_error(validation_actual, validation_predictions)),
            "validation_rmse_vnd": float(np.sqrt(mean_squared_error(validation_actual, validation_predictions))),
        }
    return results
