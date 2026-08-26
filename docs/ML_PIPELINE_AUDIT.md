# ML Pipeline Audit

**Audit date:** 2026-08-26  
**Scope:** repository state before the geographic/text/LLM enhancement work in this change set.

## Current architecture

The implemented system is a Python/Kafka/MongoDB/scikit-learn application with a React frontend. It is **not** a Spark application and it does not implement physical Bronze/Silver/Gold storage layers.

| Logical stage | Implemented component | Persistent/output boundary |
| --- | --- | --- |
| Ingestion | `scraper/listing_feature_scraper.py`, `scraper/kafka_producer.py` | Kafka `real_estate_raw` |
| Raw archive (Bronze equivalent) | `processing/kafka_to_mongo.py` | MongoDB `listings_raw` |
| Normalization/features (Silver equivalent) | `normalize_listing()` in `processing/kafka_to_mongo.py` | MongoDB `training_features`, Kafka `real_estate_features` |
| Quality/anomaly | `validate_normalized_record()` and `processing/price_anomaly.py` | MongoDB `invalid_records`, `price_anomaly_thresholds`, anomaly metadata on feature records |
| Training (Gold/training view equivalent) | `scripts/auto_train.py`, `modeling/train_model.py`, `modeling/price_model.py` | versioned joblib artifacts and JSON metrics |
| Serving | `modeling/api.py` (FastAPI); `modeling/predict_service.py` (legacy internal predictor) | HTTP API |
| UI/monitoring | `frontend/`, Prometheus/Grafana configuration in `monitoring/` | browser, Prometheus |

`docker-compose.yml` runs Kafka, Zookeeper, MongoDB, processor, scheduled scraper/trainer, FastAPI, the legacy predictor, frontend, Prometheus and Grafana. The database is MongoDB; no relational database, data lake, Spark job or Streamlit application exists.

## Current data flow

```text
Batdongsan HTML
  -> scraper raw dictionary (price/area/room text, location slugs, title/description)
  -> Kafka real_estate_raw
  -> Mongo listings_raw (raw archive)
  -> normalize + validation
  -> historical IQR price-per-m² annotation
  -> Mongo training_features + Kafka real_estate_features
  -> Mongo query of model candidates
  -> train/test split, sklearn pipeline, joblib artifact
  -> FastAPI /predict or legacy /predict
```

The Kafka `real_estate_features` topic is currently an audit/extension output: repository code contains no consumer for it. `training_features` is therefore the effective normalized and training source.

## Data and current features

The raw scraper records `title`, `description`, `price_text`, `area_text`, bedroom/bathroom/floor/frontage/road text, property metadata and `province_slug`/`district_slug`/`ward_slug`. It does **not** extract a normalized street address, latitude or longitude. Location slugs are parsed heuristically from the listing URL.

`normalize_listing()` parses numeric values, derives `price_vnd`, `price_per_m2_vnd`, `feature_coverage_score`, `has_target_price`, `is_model_candidate`, and `text_features`. It sends hard-invalid records to `invalid_records` but retains raw data for audit.

The current model (`modeling/price_model.py`) uses:

- Numeric: area, bedroom/bathroom/floor counts, frontage and road width.
- Categorical: property type, direction, legal status, listing type and province/district/ward/project.
- Text: TF-IDF of the constructed `text_features` field.
- Target: `price_vnd`, transformed with `log1p` during model fitting.

Training currently uses a fixed random 80/20 split (`random_state=42`). The preprocessing is fit only on the training partition through the sklearn pipeline, which is correct for its numeric/categorical/TF-IDF transformations.

## Current model pipeline

`RealEstatePriceModel` combines Ridge, HistGradientBoosting and SGD in a voting regressor. It records MAE, RMSE, R² and median absolute percentage error, versioned artifact metadata, and residual quantiles used for a heuristic prediction range. The FastAPI service validates input with Pydantic and loads the model artifact lazily.

The README's claim of "94%+ accuracy" is unsupported by an artifact or an explicitly defined regression metric in this checkout. Regression should be reported using MAE/RMSE/R² (and optionally median absolute error/MAPE), not a generic accuracy percentage.

## Existing anomaly detection

`processing/price_anomaly.py` already implements contextual, historical IQR detection on price per m². The primary segment is `province_slug + district_slug + property_type`; it falls back to `province_slug + property_type` if the primary group is too small. A new listing is intentionally excluded from its own baseline and thresholds are refreshed/cached and persisted for audit. Flagged listings are retained, not deleted. The default training policy is `FLAG`; `EXCLUDE` is opt-in.

This is a sound statistical price-anomaly baseline, but it does not yet represent all anomaly categories under one explicit status schema (data quality, duplicate, business rule, statistical price anomaly) and it has no LLM-assisted reviewer.

## Weaknesses and compatibility concerns

1. There are no usable coordinates or normalized address fields in the current source schema. Distance, geohash, spatial cluster, density and coordinate-based local target statistics would be fabricated or mostly missing today.
2. The current text model is TF-IDF only. It has no offline embedding cache, provider abstraction, structured-text extraction or LLM fallback design.
3. The raw and feature upserts use URL uniqueness, but no explicit duplicate-review metadata exists for changed/repeated listings. The raw archive is also an upsert rather than immutable event history.
4. Price anomaly is contextual but only available when a historical segment has sufficient data. `UNAVAILABLE` must remain a normal, auditable outcome.
5. The current train/test split is random and can overstate performance for repeated/newer listings. It is not time-aware or group-aware.
6. Historical local price aggregates must never become model features unless fitted from the training fold only. The existing anomaly metadata is correctly not included in the model feature list.
7. Documentation is partly stale: it describes unimplemented TTL/indexes and reports historical/example performance values as if current. The README's generic accuracy claim is especially misleading.
8. `real_estate_features` has no consumer; keeping it is compatible but its purpose needs documentation. The legacy predictor has no RBAC and must remain private if deployed.
9. Model artifacts currently have no recorded feature-schema version or experiment-comparison results. Existing callers rely on the API's current request fields, so additions must stay optional.

## Files planned for change

- `processing/kafka_to_mongo.py`: normalize optional coordinates and deterministic text enrichment; add explicit ingestion-review fields while preserving records.
- `processing/feature_engineering.py` (new): coordinate validation and deterministic geographic features shared by normalization and inference.
- `processing/text_enrichment.py` (new): cached, batchable deterministic text extraction plus an optional embedding-provider boundary; no secrets or network calls by default.
- `processing/llm_review.py` (new): typed provider/reviewer abstraction with strict JSON schema validation and fail-open fallback.
- `processing/price_anomaly.py`: integrate the existing detector into a unified review result without replacing its historical-IQR safeguards.
- `modeling/price_model.py`, `modeling/train_model.py`, `scripts/auto_train.py`: feature-schema metadata and reproducible ablation evaluation that does not fabricate results.
- `modeling/api.py`: optional coordinate/structured input fields and same deterministic preprocessing, without remote embedding/LLM calls in requests.
- `processing/export_training_dataset.py`, `.env.example`, `docs/DATA_SCHEMA.md`, `docs/PRICE_ANOMALY_DETECTION.md`, `README.md`: schema/configuration/documentation alignment.
- `utils/tests/`: focused tests for geo, text cache/extraction, LLM JSON parsing, review statuses and train/inference feature consistency.

## Risk and migration strategy

- All new fields will be optional. Existing raw messages, historical Mongo documents, API requests and model artifacts remain readable.
- No external embedding/LLM provider will be enabled by default. If configured later, keys are read only from environment variables and failures return deterministic results.
- Coordinate features will be null/unknown for the present scraper dataset. They will not manufacture points from location slugs.
- No price-derived geographic feature will be added to the prediction model. This avoids validation/test leakage.
- Benchmarks will run only against supplied real records. If no real dataset is available in the checkout, the implementation will save the evaluation method and report that no comparison result is available.
