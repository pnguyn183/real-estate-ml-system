# Product requirements and implementation status

This file preserves the product intent while explicitly separating it from the
current implementation. Runtime behavior is defined by the source code and
[`ARCHITECTURE.md`](ARCHITECTURE.md), not by an unchecked target in this PRD.

## Business goals and target metrics

The intended product predicts Vietnamese real-estate prices from scraped market
data, keeps a reproducible training history, and serves predictions to a web
client. Product targets are R² > 0.75, bounded MAE/RMSE, fresh training data,
99.5% availability and sub-second API responses. These values are goals for
measurement and release policy; they are not current guarantees. Observed
metrics are recorded in [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Functional requirements mapped to the repository

| ID | Intended behavior | Current implementation |
| --- | --- | --- |
| F1 | Scrape Batdongsan listings | Implemented by `scraper/listing_feature_scraper.py`; volume is configuration/runtime dependent. |
| F2 | Resume crawling | Implemented with JSON state under `runtime/scrape_state`; Compose does not mount `runtime`, so state is not guaranteed across container replacement. |
| F3 | Extract listing fields | Implemented for the fields in `DATA_SCHEMA.md`; missing optional values remain null/empty. |
| F4 | Paginate crawls | Implemented; producer default is 1,000 pages and scraper config is bounded by its supplied settings. |
| F5 | Prefer verified listings | Implemented; verified filtering is enabled by default and can be disabled with `--include-unverified`. |
| F6 | Parse price/area text | Implemented in `processing/kafka_to_mongo.py` for the formats covered by its parser; unsupported text is invalid or missing. |
| F7 | Normalize numeric values | Implemented with finite/positive/range validation. |
| F8 | Derived price-per-square-metre | Implemented when valid price and area are available. |
| F9 | Feature coverage score | Implemented as `feature_coverage_score` (0-10 style score used by the processor). |
| F10 | Mark training candidates | Implemented as `is_model_candidate`; no fixed 60%/85% acceptance gate is enforced. |
| F11 | Train a regression model | Implemented by `RealEstatePriceModel`: Ridge, HistGradientBoosting and SGD components in a voting ensemble. |
| F12 | Use text features | Implemented with TF-IDF plus deterministic local hashing embeddings/structured text fields; no remote embedding service is configured. |
| F13 | Handle missing values | Implemented with scikit-learn numeric and categorical imputers. |
| F14 | Train/test split | Implemented as a reproducible random 80/20 split (`random_state=42`); time/group splitting is not implemented. |
| F15 | Record training metrics | Implemented through versioned model metadata and JSON metrics files. |
| F16 | Single prediction | Implemented by `modeling/predict_price.py`, FastAPI `/predict`, and the separate legacy predictor. |
| F17 | Batch prediction | Implemented by FastAPI `/predict/batch`; throughput at 1K records is not a release-gated claim. |
| F18 | Model versioning | Implemented with timestamped joblib/metadata files and a current model pointer. |
| F19 | Confidence information | Implemented as a heuristic interval/score based on validation residuals and input completeness; it is not a calibrated ±10% guarantee. |
| F20 | Optional difficult-data extraction | Implemented as Processor → `real_estate_ai_input` → separate external-API AI worker → `real_estate_ai_results` → existing Processor result handler. Normal structured ingestion remains deterministic. |
| F21 | Control API cost/failure | Disabled by default; timeout, transient retries, total budget, per-process rate/concurrency/circuit limits and durable successful-result reuse are implemented. No global quota guarantee or unlimited free tier. |
| F22 | Validate extracted data | Strict nullable JSON, evidence/confidence checks, preserved structured facts/provenance and repeated shared business validation before Mongo writes. These do not prove factual truth. |
| F23 | Synthetic stress generation | Bounded deterministic templates/variations, duplicates, missing/malformed/semi-structured/unstructured scenarios; dedicated stress topic and database, no generator LLM calls. |
| F24 | Protect primary training | Synthetic rejection on live ingress, isolated stress storage, non-candidate generation, Mongo query filters, shared model/variant filters covering manual JSON. |
| F25 | AI recovery and observability | Manual-commit Kafka request/result hand-offs, acknowledged terminal DLQ, Mongo cache/state/receipts and implemented Prometheus agent metrics. Automated replay is not present. |

## Current data contracts

- Source: Batdongsan HTML parsed into JSON and published to Kafka
  `real_estate_raw`.
- Processor output: MongoDB collections `listings_raw`,
  `training_features`, `invalid_records`, `dlq_raw`, anomaly thresholds and
  offset checkpoints. It also publishes `real_estate_features`, which has no
  in-repository consumer.
- AI: request and result envelopes on `real_estate_ai_input` and
  `real_estate_ai_results`; terminal failures retained in Kafka
  `real_estate_ai_dlq` and origin-specific Mongo review/state collections.
- Synthetic: tagged records on `real_estate_stress_raw`, isolated
  `real_estate_stress_db` and best-effort `real_estate_stress_features` audit
  output. Synthetic data must not enter primary training.
- Model artifacts: timestamped files under `artifacts/models/`, metadata and
  metrics JSON. Training does not require or emit a Parquet data lake.
- Serving: authenticated FastAPI on port 8000; the legacy predictor on port
  8002 is unauthenticated and host-published; network access must be restricted.

See [`DATA_SCHEMA.md`](DATA_SCHEMA.md) for exact field and validation details.

## Non-functional expectations and status

URL upserts, fingerprint comparisons, manual Kafka offset commits, bounded API
retry logic, required durable failure hand-offs, best-effort diagnostic
checkpoints and signal-aware shutdown are implemented. Required DB/publish
failure leaves the current input uncommitted. Kafka/Mongo/API operations are
not one atomic transaction; redelivery or an API repeat in the crash-before-
cache window remains possible. Cross-URL fuzzy duplicate merging is not
implemented.
Throughput, latency, uptime, coverage, freshness, retention and test-coverage
percentages are not automatically enforced as SLAs. MongoDB has no configured
TTL policy, Kafka runs as three replicated ZooKeeper-mode brokers with three
partitions in Compose (all on one host), and model evaluation uses a random
rather than temporal split.

## Constraints and external dependencies

The supported runtime is a local/single-host Docker Compose stack using Python,
Kafka 7.5.3, Zookeeper, MongoDB 7, scikit-learn, React/Vite, Prometheus and
Grafana. The scraper depends on Batdongsan availability and terms of service.
Secrets are supplied through ignored `.env`/environment variables; examples
contain placeholders only. External AI is off by default and requires
`AI_ENABLED`, the appropriate Processor routing gate, `LLM_PROVIDER`,
`LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`. The OpenAI-compatible adapter is
replaceable through configuration; no local model/GPU is required.

## Explicitly not present in this checkout

There is no Spark process or cluster, physical Bronze/Silver/Gold lake, Redis,
PostgreSQL, Elasticsearch/ELK, Streamlit app, Kubernetes deployment, CI/CD
pipeline, Alertmanager receiver, remote embedding service, self-hosted LLM/GPU,
A/B framework, global AI quota service, automatic DLQ replay or host-level
high-availability deployment. The implemented external LLM path is optional;
it must not be described as absent merely because its default is disabled.
Three brokers run on one host. The browser UI is present and calls
the authenticated API for single predictions and role-authorized batch/model
operations.

Implementation rows above are source-level status, not blanket live-test PASS
claims. See [PROJECT_STATUS.md](PROJECT_STATUS.md) for implemented-and-tested,
untested and environment-blocked checks.

**Implementation reviewed:** 2026-09-08
