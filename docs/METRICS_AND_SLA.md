# Metrics and service targets

This page separates metrics exported by the current runtime from product goals.
Prometheus details are in [`MONITORING_AND_ALERTING.md`](MONITORING_AND_ALERTING.md).

## Runtime metrics

| Component | Metrics |
| --- | --- |
| Processor workers (`:8003`, `:8004`, `:8005`) | Kafka consumed/processed/failed counters, consumer lag, processing duration histogram, Mongo write success/failure counters |
| Trainer (`:8001`) | Training duration, MAE/RMSE/R2/sample gauges, run/failure counters, last-run and last-success timestamps |
| AI agent (internal `:8006`) | Extraction outcomes/duration, provider calls, retry/rate/circuit state, cache hits, request committed-offset lag |
| Stress agent (internal `:8007`) | Generated/delivered/failed counters, duplicate/unstructured counts, bounded run/rate/duration gauges |
| API, scraper, predictor | No Prometheus metrics in this repository; use HTTP health and logs |

Prometheus scrapes every 15 seconds. Alert expressions and hold durations are
defined only in `monitoring/alert_rules.yml`: failed/consumed rate >5%, lag
>1000, average processing >5 seconds, trainer failure/staleness and Mongo write
failures.
Exact implemented names and semantics are listed in
[`MONITORING_AND_ALERTING.md`](MONITORING_AND_ALERTING.md). The six existing
alerts and provisioned dashboard panels are not agent-specific. Processor
throughput combines real, synthetic and AI-result traffic; it must not be
reported as primary-training ingestion throughput without separate measurement.

## Latest observed local model

An earlier local audit recorded an artifact trained from 4,943 candidates:

| Metric | Observed |
| --- | ---: |
| R2 | 0.596 |
| MAE | 4.56B VND |
| RMSE | 12.87B VND |
| Median absolute percentage error | 27.5% |

These are historical measurements, not a fresh verification of the mounted
artifact or evidence of current runtime health. See `PROJECT_STATUS.md` for
the latest checks; the original PRD quality targets are not claimed as met.

## Product targets (not runtime guarantees)

The PRD targets R2 > 0.75, MAE < 500M VND, RMSE < 800M VND, API response under
500 ms and 99.5% availability. No automated quality gate, rollback controller
or API latency metric enforces those numbers.

## Freshness and retention

- Trainer checks every 1,800 seconds by default and retries every 60 seconds.
- Scraper periodic runs default to every 1,800 seconds; initial settings are
  separate.
- MongoDB has no configured TTL or rolling deletion job. Kafka topic retention
  uses broker defaults; no application-specific retention override or archival
  job is configured.

## Dashboard queries

```promql
rate(kafka_messages_consumed_total[5m])
rate(kafka_messages_processed_total[5m])
rate(kafka_messages_failed_total[5m])
kafka_consumer_lag
rate(processing_duration_seconds_sum[5m]) / rate(processing_duration_seconds_count[5m])
model_mae_vnd
model_rmse_vnd
model_r2
time() - trainer_last_success_timestamp_seconds
```
