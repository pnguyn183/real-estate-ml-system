# Cross Review

**Reviewed:** 2026-08-26, after the geographic/text/anomaly-review changes.

## Critical Issues

1. **No production benchmark dataset/artifact is present in this checkout.** `artifacts/` only contains the local authentication user store, so no honest baseline-versus-enhanced model score can be reported. The training CLI now provides `--evaluate-variants` and writes `feature_variant_metrics.json` only when supplied real candidate records; no synthetic score was created.
2. **The legacy predictor (`modeling/predict_service.py`, port 8002) has no authentication or authorization.** It must stay on a private network or be removed before exposing the stack publicly. The primary FastAPI endpoint is the supported authenticated serving path.

## Major Issues

1. **Current scraper data has no coordinates or normalized street address.** The new geographic processor accepts trusted optional coordinates and derives only latitude/longitude plus a 0.01-degree grid. It intentionally does not invent distance, geocode location slugs, spatial clusters, density, or target-price neighborhood means.
2. **The URL location parser is heuristic.** `parse_location_parts()` derives province/district/ward from URL path segments and can become stale when the upstream site changes. It needs an upstream-source contract or a monitored parser fixture before location claims are treated as authoritative.
3. **The raw archive is an upsert, not immutable scrape history.** It preserves the latest raw listing per URL, which supports current processing but cannot reconstruct every price change. A future event archive should use a scrape-event key if market-history modelling is needed.
4. **The `real_estate_features` Kafka topic has no repository consumer.** It remains an audit/extension feed, while MongoDB `training_features` is the actual Silver/training source. Either add a consumer or explicitly retain it only for external subscribers.
5. **The existing random holdout can leak repeated-listing/time effects into evaluation.** Preprocessing itself is fit on the train partition, and no target-based feature was added. Nevertheless, a time-based or listing-group split should be added once enough dated history exists.

## Minor Issues

1. `docs/` contains historical examples and aspirational TTL/index/versioning statements that are not all enforced in MongoDB. The central schema/audit docs now mark the effective pipeline, but older planning documents should be consolidated in a future documentation pass.
2. The default deployment values are suitable for local development only: MongoDB has no authentication, Grafana uses its documented default credentials, and `AUTH_SECRET_KEY` must be changed in production.
3. The text cache is local SQLite. It deduplicates repeated content during a processor lifetime, but a multi-instance deployment should replace it with a shared cache or a MongoDB collection keyed by `text_content_hash`.
4. Statistical price flags are not fraud labels and cannot be measured with precision/recall without reviewed anomaly labels.

## Improvements Made

- Added a shared deterministic geo feature module. Invalid/missing coordinates become explicit status values and never crash processing or prediction.
- Added local, 32-dimensional hashing text embeddings, structured extraction, batch/cache support and an embedding-provider interface. It makes no remote call or uses a secret.
- Added feature-schema metadata and a reproducible held-out ablation command: current baseline, baseline plus geo, baseline plus text enrichment, and both.
- Kept contextual IQR as the price detector and added a unified ingestion review schema: `NORMAL`, `SUSPICIOUS`, `INVALID`, including data-quality, duplicate and price reasons. Records are retained.
- Added a strict optional LLM review boundary. It is disabled by default, runs only for suspicious records, validates exact JSON, never adjusts `price_vnd`, and fails open to deterministic detection.
- Fixed the unsupported README "94%+ accuracy" claim to name the actual regression metrics instead.
- Added tests for geo validity, missing coordinates, embedding cache/batch behavior, structured extraction, malformed/failed LLM responses, unified review states, extreme price validation and train/inference feature-schema consistency.

## Leakage Audit

- `price_vnd` remains the target only. No price-derived anomaly metadata, local-price aggregation or future statistic was added to `NUMERIC_FEATURES`/`CATEGORICAL_FEATURES`.
- IQR thresholds are built from persisted historical feature records before the incoming record is upserted, and explicitly exclude the incoming URL.
- Text embeddings/extraction consume listing text only; they do not read the target.
- sklearn imputation, one-hot encoding, TF-IDF and regressors are fit after the train/test split inside the pipeline.
- The current split is still random; temporal/group split remains a documented limitation, not an ignored risk.

## Performance and Cost Review

- Built-in embeddings are 32 floats/listing, roughly 128 bytes raw payload before BSON/document overhead. They are generated in batches and cached by SHA-256 content hash; unchanged text produces no new vectorization on cache hit.
- The default path makes **zero** remote embedding or LLM API calls. `LLM_REVIEW_ENABLED=false` is explicit in `.env.example`.
- If an external provider is introduced, it must use the `EmbeddingProvider`/`LLMProvider` boundary, batch offline work, persist content-hash cache state and own timeout/retry/rate-limit handling. It must not be placed in FastAPI request handling.

## Remaining Limitations

- No coordinate coverage, address standardization, reviewed anomaly labels or real training data is included in this checkout; therefore geospatial lift, text-enrichment lift and anomaly precision/recall are intentionally unreported.
- The local hashing embedding is a lightweight portfolio-project baseline, not a Vietnamese sentence-transformer semantic embedding. A stronger provider should be benchmarked only after the available data supports it.
- No automatic price adjustment is implemented. That is intentional: only a human-approved policy may use an LLM suggestion, and ground-truth scraped prices remain immutable.
