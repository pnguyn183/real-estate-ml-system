# Acceptance criteria and quality gates

This document distinguishes checks that are implemented in the repository from
product targets that still require measurement or a policy decision. It is not
a report of a successful production release.

## Implemented checks

### Scraper to Kafka

- `scraper/listing_feature_scraper.py` produces JSON records for the
  `real_estate_raw` topic.
- The producer retries Kafka sends and the scheduled scraper logs failures.
- Scraper resume state is written as JSON under `runtime/scrape_state/`.
  Compose does not mount this scraper directory, so container replacement
  can lose it; fresh-start settings can also deliberately reset a run.
- The repository does not enforce a daily-volume, duplicate-rate or timeout
  acceptance gate.

### Kafka to MongoDB

- `processing/kafka_to_mongo.py` commits Kafka offsets only after processing.
- Raw records are upserted by URL into `listings_raw`.
- Normalized records are upserted into `training_features`; duplicate URL and
  content fingerprints are tracked in the review metadata.
- Invalid records are written to `invalid_records` and raw payloads may be
  written to `dlq_raw`.
- Validation rejects missing URLs, invalid/non-finite or non-positive price and
  area values, extreme total prices/areas, and inconsistent derived
  price-per-square-metre values.
- `feature_coverage_score`, `has_target_price` and `is_model_candidate` are
  calculated by the processor. No fixed candidate-rate threshold is enforced.
- The historical IQR detector annotates price anomalies; it does not delete
  records or change the target price.

### AI fallback and synthetic boundary

- Structured records stay on the deterministic path without external calls.
- Missing required extraction fields with usable source text can be routed,
  behind explicit opt-in, to `real_estate_ai_input`.
- `ai-agent` consumes requests, calls an external HTTPS JSON-mode provider,
  validates output, caches it durably, and acknowledges publication to
  `real_estate_ai_results` before committing the input offset.
- Processor result handling repeats business validation and uses origin-selected
  storage, URL upserts and durable receipts. Known source values/provenance are
  preserved. Failed output remains recoverable through review/DLQ paths.
- Provider timeouts, transient retries, per-process rate limits and a circuit
  breaker are bounded. Disabled startup needs no API key.
- Stress generation is deterministic and bounded; tagged inputs use a dedicated
  stress topic/database but share the existing worker capacity.
- Primary Mongo queries, manual Mongo/JSON training, the model `train()` method
  and dataset export all exclude synthetic data using `agents/safety.py`.
- Tests cover malformed JSON/output, missing fields, retries, publication
  failures, result replay/cache and synthetic training exclusion. Their live
  infrastructure/provider coverage must be reported separately.

### Training

- `scripts/auto_train.py` and `modeling/train_model.py` query model candidates
  from MongoDB and write versioned joblib artifacts plus JSON metrics.
- Training fails when fewer than `MIN_RECORDS_FOR_TRAINING` records are
  available. The Compose default is 3,000 (the CLI/model fallback is 200).
- Metrics recorded are MAE, RMSE, R², MAPE/median absolute percentage error
  and sample count, together with model metadata.
- There is no automatic R²/MAE quality gate, rollback, approval workflow or
  email notification. A newly trained artifact becomes the current pointer.

### Prediction API

- FastAPI validates request fields with Pydantic and protects prediction and
  model-information routes with bearer-token roles.
- `/predict` handles one record; `/predict/batch` handles a list and returns
  per-record errors rather than silently dropping invalid entries.
- Responses include a heuristic prediction interval derived from validation
  residuals; this is not a calibrated probability or guaranteed ±10% interval.
- The legacy predictor on port 8002 exposes `/health` and `/predict` without
  authentication and must remain private.

## Product targets (not current guarantees)

The PRD targets R² > 0.75, MAE/RMSE limits, freshness, throughput and uptime.
They are retained in [`REQUIREMENTS.md`](REQUIREMENTS.md) and
[`METRICS_AND_SLA.md`](METRICS_AND_SLA.md), but no code currently evaluates all
of them as release gates. The latest observed model metrics are documented in
[`PROJECT_STATUS.md`](PROJECT_STATUS.md).

## Operational acceptance checklist

Run the following against the intended environment:

```text
[ ] python -m compileall -q .
[ ] pytest -q
[ ] npm.cmd run build                 (from frontend/)
[ ] docker compose config --quiet
[ ] docker compose build
[ ] docker compose up -d
[ ] docker compose ps                 (required services stable)
[ ] GET /health returns HTTP 200 and model_exists=true when an artifact exists
[ ] processor and trainer /metrics return HTTP 200
[ ] disabled agents /health and /metrics respond without credentials
[ ] all seven application topics have 3 partitions / RF3 / min ISR2
[ ] AI requests -> result topic -> Processor -> correct Mongo database is verified
[ ] publish/storage failure leaves the corresponding input offset uncommitted
[ ] duplicate/cache and malformed-provider cases are exercised
[ ] synthetic records are excluded from Mongo, JSON and model training entrypoints
[ ] Prometheus targets are up; Grafana /api/health returns HTTP 200
[ ] authenticated /predict and /predict/batch calls return valid JSON
[ ] Kafka producer/consumer and MongoDB writes are verified from logs/data
```

Do not mark a box as passed from configuration inspection alone. Record the
command, timestamp and environment for each run. No stakeholder sign-off or
fabricated acceptance report is stored in this repository.

See [`PROJECT_STATUS.md`](PROJECT_STATUS.md) for dated executed results rather
than interpreting this checklist as a passed release.
