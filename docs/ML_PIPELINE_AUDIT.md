# ML pipeline audit

**Implementation reviewed:** 2026-09-08. Runtime/test evidence is recorded
separately in [PROJECT_STATUS.md](PROJECT_STATUS.md).

This audit describes the code currently executed by Compose. It does not
describe a planned Spark/data-lake architecture.

## Implemented pipeline

| Stage | Implementation | Boundary |
| --- | --- | --- |
| Ingestion | `scraper/listing_feature_scraper.py`, `scraper/kafka_producer.py` | Three-partition Kafka `real_estate_raw` (RF3) |
| Parallel processing | `processor`, `processor-2`, `processor-3` running `processing/kafka_to_mongo.py` | One `real_estate_training_pipeline` consumer group |
| Optional difficult-data fallback | `agents/worker.py`, `agents/extraction.py`, `agents/providers.py`, `agents/results.py` | Processor → AI input topic → external API/cache → AI results topic → same Processor group |
| Controlled synthetic load | `agents/stress.py`, `agents/generator.py` | Stress raw topic → same workers → `real_estate_stress_db`; never primary training |
| Raw persistence | Processor workers | MongoDB `listings_raw` (latest document per URL) |
| Normalization/review | `normalize_listing()`, validation, enrichment, IQR review | MongoDB `training_features`, `invalid_records`, `price_anomaly_thresholds`; best-effort clean topic |
| Training | `scripts/auto_train.py`, `modeling/train_model.py`, `modeling/price_model.py` | Timestamped joblib and JSON metadata/metrics under `artifacts/` |
| Serving | `modeling/api.py` and legacy `modeling/predict_service.py` | FastAPI port 8000 and host-published, unauthenticated legacy HTTP port 8002 |

The trainer reads candidate records from MongoDB; it does not consume the clean
Kafka topic. `real_estate_features` currently has no in-repository consumer.
Neither does `real_estate_stress_features`. AI result handling, not the AI
worker itself, writes validated features through the shared Processor routine.

## Features and target

`build_feature_frame()` applies deterministic geographic and text enrichment on
both training and inference paths. The model uses numeric dimensions/counts,
optional coordinates, extracted text counts and 32-dimensional local hashing
embeddings; categorical property/location fields; and TF-IDF over
`text_features`. The target is `price_vnd` through a `log1p` transformed target
regressor.

The ensemble contains Ridge, HistGradientBoosting and SGD regressors in a
VotingRegressor. Missing values use scikit-learn imputers. A fixed random 80/20
split (`random_state=42`) is used; it is not time- or listing-group-aware.

## Artifacts and inference

Each training run writes a timestamped model and metadata, copies the requested
stable model path and best-effort updates `artifacts/price_model_current.joblib`.
Metrics include MAE, RMSE, R2, median absolute percentage error, sample count,
residual quantiles and feature-schema metadata. FastAPI reloads the model when
the configured file changes. Prediction intervals/confidence are heuristics
based on residuals and input completeness, not calibrated probabilities.

## Historical artifact result

The earlier audit recorded an artifact using 4,943 samples and approximately R2
0.596, MAE 4.56B VND, RMSE 12.87B VND and median absolute percentage error
27.5%. These are historical observations, not a fresh benchmark of the Agent
extension or a claim about the currently loaded container model. Inspect the
actual artifact metadata for current measurements. No automatic quality gate
blocks deployment.

## Leakage and data-quality review

- `price_vnd` is the target only; price-derived anomaly metadata is excluded
  from model features.
- IQR thresholds are built from historical feature records while excluding the
  incoming URL.
- Text and geographic features use listing inputs only.
- Preprocessing is fitted inside the sklearn pipeline after the split.
- Raw records are retained, invalid records are separately recorded, and
  normalized records are URL upserts with a content-comparison fingerprint.
  The fingerprint includes URL; cross-URL near duplicates are not merged.

## Synthetic training safety

The primary model must never learn from stress-test records. The implemented
protection is layered, not just a single trainer query:

1. Stress generation uses a dedicated topic and reserved URL/metadata.
2. The live Processor branch rejects synthetic markers before raw/feature
   writes. The stress branch accepts marked synthetic URLs only and writes
   `real_estate_stress_db`, distinct from the trainer's `real_estate_db`.
3. `normalize_listing()` always sets synthetic `is_model_candidate=false`.
   AI request/result validation preserves origin and original synthetic
   provenance; a model response cannot opt into the real database.
4. `auto_train.training_query()` applies `real_data_query()` to both candidate
   counting and data loading from the configured feature collection. The
   manual Mongo trainer and manual export utility also filter synthetic data.
5. `RealEstatePriceModel.train()` and `evaluate_feature_variants()` call
   `real_records()` before feature construction, so direct calls and the manual
   JSON training path cannot bypass the marker filter merely by setting
   `is_model_candidate=true`.

Markers recognized include boolean/string/numeric synthetic flags, synthetic
source types, `generated_by=stress_agent` and the reserved URL prefix. These
checks are independent of `PRICE_ANOMALY_TRAINING_POLICY`; setting `KEEP` or
`FLAG` never permits synthetic training. They cannot identify deliberately
untagged third-party synthetic data whose provenance has been removed.

Regression evidence is maintained in `utils/tests/test_synthetic_safety.py`,
`test_agent_pipeline.py` and `test_ai_results.py`; current execution results
belong in the verification report, not inferred from test-file existence.

## AI-derived real features

Normal structured records do not require an LLM. Enabled fallback applies only
to required-field parsing gaps with usable source text. The external provider
returns strict nullable JSON fields, quoted evidence and confidence. The
worker checks schema/evidence and shared business rules; the result handler
checks source version, provenance and business rules again before storing.
`processing_method=ai_extraction` and bounded `ai_*` metadata retain lineage.
Accepted real AI-derived records can become candidates under the same feature
coverage policy; no human-approval gate is implemented.

API failures/invalid extraction are routed through the results topic to
invalid/review storage and acknowledged Kafka DLQ, not training features.
Successful same-URL/same-content extraction may be reused from Mongo cache.
This is not semantic duplicate detection or a guarantee that extracted facts
are true; evidence presence and confidence alone do not establish accuracy.

## Known limitations

The scraper currently supplies URL-derived location slugs but no trusted street
address or coordinates, so coordinate coverage is generally missing. The raw
collection is a latest-state upsert rather than immutable event history. The
legacy predictor has no authentication. There is no remote embedding or LLM
provider enabled by default, no automatic model rollback, and no temporal/group
evaluation. External extraction is implemented but gated off by default;
the older anomaly-review provider hook remains unconfigured. Current runtime
and external-provider testing limitations must be read in the verification
report. A stronger model or changed serving policy still requires a benchmark
using real, reviewed data.
