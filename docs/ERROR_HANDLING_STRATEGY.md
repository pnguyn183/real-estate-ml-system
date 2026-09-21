# Error handling and recovery (implemented)

This document describes the behavior present in the source. It does not define
error codes or recovery components that are not implemented. See
[RUNBOOK.md](RUNBOOK.md) for operator commands and [ARCHITECTURE.md](ARCHITECTURE.md)
for component boundaries.

## Scraper

`listing_feature_scraper.py` uses HTTP retries and parsing fallbacks supplied by
the scraper implementation. `kafka_producer.py` logs delivery failures through
its delivery callback. `auto_scrape.py`:

- runs each subprocess with a configured timeout;
- logs non-zero exit codes and timeout warnings;
- waits 30 seconds after an unexpected loop exception;
- preserves state files under `runtime/scrape_state/` for resumable runs.

There is no external retry queue, Alertmanager receiver or scraper metrics
endpoint. A website 4xx/5xx or rate-limit response is therefore diagnosed from
scraper logs and the persisted state file.

## Kafka processor

All three `KafkaToMongoPipeline` workers subscribe to `real_estate_raw`,
`real_estate_stress_raw` and `real_estate_ai_results` in the existing
`real_estate_training_pipeline` group. Both auto-commit and auto-offset-store
are disabled; `auto.offset.reset=earliest` applies only without a usable
committed offset.

| Failure/event | Current behavior |
| --- | --- |
| Consumer error | Log `kafka_consumer_error`; continue polling |
| JSON/UTF-8 decode failure, non-object JSON or malformed AI result envelope | Log `kafka_deserialize_failed`; upsert base64 bytes/tombstone metadata to Mongo `dlq_raw` by topic/partition/offset; commit only after that write succeeds |
| Raw Mongo write failure | Log `raw_db_write_failed`, best-effort diagnostic `dlq_raw`, then re-raise; no source commit |
| Invalid normalized record | Add `validation_errors`, upsert `invalid_records`, best-effort publish to clean topic |
| Feature/invalid Mongo write failure | Log failure, attempt diagnostic `dlq_raw`, then re-raise; offset remains uncommitted |
| Clean-topic publish failure | Log warning; Mongo feature write remains the source of truth |
| Kafka commit failure | Log `commit_failed`, stop polling so a later offset cannot skip the failed commit; Compose restarts the worker |
| Mongo checkpoint failure after Kafka commit | Log `checkpoint_write_failed`, continue; Kafka group offset remains authoritative |
| Synthetic record sent to live topic or unmarked/non-synthetic URL sent to stress topic | Persist an origin-rejection DLQ record before commit; no primary features |
| AI request publish not acknowledged | Raise, keep source offset uncommitted; raw source is already saved for retry |
| Detector failure | Log `price_anomaly_detection_failed`, mark anomaly status `UNAVAILABLE`, keep ingestion alive |
| SIGINT/SIGTERM | Set a shutdown flag, finish the loop and close the consumer |

Successful processing commits the Kafka offset synchronously and attempts an
`offset_checkpoint` document. Mongo URL upserts and completed AI result receipts
support normal redelivery. The fingerprint compares content; it includes URL
and is not a cross-URL duplicate key. `listings_raw` retains only the latest
value per URL. Required persistence or hand-off errors terminate that worker
loop and Compose restarts it; repeated infrastructure failure can therefore
cause restart loops and must not be reported as a healthy pipeline.

## AI request, external API and result recovery

`agents/worker.py` consumes `real_estate_ai_input` in
`real_estate_ai_extraction`; it publishes `real_estate_ai_results`. The existing
Processor group then calls `agents/results.py` to apply results to the correct
real/stress database. The AI worker does not directly upsert training features.

| Event | Current behavior / commit boundary |
| --- | --- |
| `AI_ENABLED=false` | Health/metrics stay available; no Kafka consumption, key validation or external requests |
| Routing enabled but AI worker disabled | Requests can accumulate; no silent fallback processing of the queue |
| Malformed AI request | Base64 source in acknowledged `real_estate_ai_dlq`; only then commit request offset |
| Provider timeout, connection failure, HTTP 429 or 5xx | Up to `LLM_MAX_RETRIES` additional attempts, exponential backoff and total time budget |
| Authentication failure, rejected response or schema/evidence/confidence failure | Bounded failure result; no infinite retry |
| Local rate limit, concurrency ceiling or open circuit | Immediate bounded failure result; no unbounded request backlog inside the process |
| API output accepted | Strict nullable schema/evidence plus shared business validation; cache success and durable event result before Kafka publish |
| Result publish failure | Do not commit AI request offset; replay reuses saved result instead of making another API call |
| Completed repeated request | Reuse saved published state; do not unnecessarily publish/call again |
| Same URL/content with timestamp-only source change | Successful response cache can be reused within origin/provider/model/settings boundary |
| Newer raw source exists | Source-version checks skip known stale result; record stale outcome |
| Accepted result message | Preserve original raw source and provenance; deterministic validation/review/storage; save receipt before result offset commit |
| Failed extraction or post-result validation | Persist invalid record and `ai_failures`, acknowledge Kafka AI DLQ, save result receipt, then commit result offset |
| DLQ publish or receipt/database save fails | Keep current offset uncommitted; restart/replay can repeat earlier idempotent operations |

Defaults: 30-second API timeout, three additional transient retries, 120-second
total extraction budget, 20 provider attempts per minute per process, one
in-flight slot, circuit threshold five provider failures, 60-second cooldown.
The Kafka worker is sequential; raising the slot ceiling does not itself
parallelize its loop. Adding AI replicas requires dividing provider quota;
there is no cluster-wide rate limiter.

`ai_extractions` stores per-event state, `ai_response_cache` stores successful
content reuse, and `ai_result_receipts` stores completed result handling. These
are not a Kafka/Mongo/API transaction. An API response received immediately
before a crash may be called again before its cache is saved; publish/commit
races may produce redelivery. Stale checks are not an atomic cross-collection
compare-and-write. There is no exact-once guarantee or fuzzy near-duplicate
merger.

Failures are recoverable for manual inspection from the retained original
source, `invalid_records`, `ai_failures` and Kafka DLQ. No automated DLQ
consumer/retry topic, cache expiry or replay command is implemented. Replaying
an unchanged failed event is not an automatic new API attempt: the saved
failure/receipt is deliberately reusable. Recovery must review event state and
make a controlled replay decision, not blindly reset the whole consumer group.

## Stress agent

`STRESS_ENABLED=false` serves idle health/metrics without generating. Enabled
runs are bounded by duration, count/rate limits and a seeded template source.
Kafka delivery callbacks count acknowledged records; generation count is not
delivery count. A claimed run ID is not automatically rerun after interruption
or failure. Inspect `runtime/stress/<run_id>/` report/ground truth and use a new
explicit run ID for another intentional test. A failed finite run can leave
the agent alive and serving metrics; inspect run status and failure counters,
not only HTTP health. No stress process calls an LLM, and synthetic AI requires
the separate opt-in gate.

## MongoDB

Processor/trainer clients use the configured URI and rely on MongoDB driver
exceptions plus the worker retry loops. The local Compose deployment has no
Mongo authentication. A persistent named volume (`mongo_data`) protects data
across ordinary container recreation; `docker compose down -v` intentionally
deletes that volume and is destructive.

## Trainer

`auto_train.py` catches candidate-query and training exceptions, increments
`trainer_runs_failed_total`, logs the error and retries after
`TRAIN_RETRY_INTERVAL`. Insufficient candidates simply skip training. A failed
run does not replace the stable model artifact; no automatic quality-based
rollback service exists.

## API and predictor

FastAPI returns structured HTTP errors for missing/invalid bearer tokens (401),
insufficient roles (403), validation failures (422), missing model artifacts
(503) and unexpected prediction failures (500). Model loading errors are logged
and surfaced through `/health`/prediction responses. The legacy predictor logs
file-load and prediction exceptions and returns HTTP 500; it has no
authentication or RBAC.

## Observability and escalation

Processor/trainer structured events are exported to stdout and selected counters
are exposed on ports 8003, 8004, 8005 and 8001 for Prometheus. AI and stress
agents additionally expose internal health/metrics on 8006 and 8007. Bounded AI
error-code labels, provider-call/retry/validation counters, circuit state,
cache hits and committed-offset AI lag are implemented. These endpoints are
not public extraction APIs. Rules in
`monitoring/alert_rules.yml` cover processing error rate, Kafka lag, processing
duration, trainer failures/staleness and Mongo write failures. Prometheus and
Grafana provide visibility only; no notification or paging integration is
configured.

## Operator response

1. Run `docker compose ps` and inspect `docker compose logs --tail=200 <service>`.
2. Check API/metrics health endpoints and Prometheus `/targets`.
3. For pipeline failures, inspect `dlq_raw`, `invalid_records`, `ai_failures`,
   `ai_extractions`, `ai_response_cache`, `ai_result_receipts`, Kafka AI DLQ and
   `offset_checkpoint` in the appropriate real/stress database before replay.
4. Restart only the affected service with `docker compose restart <service>`.
5. Never run `docker compose down -v` unless deleting Mongo data is intended.
