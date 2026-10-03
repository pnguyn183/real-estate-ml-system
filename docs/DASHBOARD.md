# Grafana dashboard and alerts

Grafana provisions three dashboards from `monitoring/grafana/dashboards/`:
`real_estate_pipeline.json`, `agent_operations.json`, and `source_ingestion.json`.
They read the local Prometheus datasource. No external notification channel is
configured.

## Access

```bash
docker compose up -d prometheus grafana processor processor-2 processor-3 trainer ai-agent stress-agent
```

- Grafana: `http://localhost:3001` (use the credentials configured in local
  Compose; replace them before deployment).
- Prometheus: `http://localhost:9090`.

## Provisioned panels

- **Processor Throughput**: consumed, processed and failed Kafka message rates.
- **Kafka Consumer Lag**: exported processor consumer lag.
- **Processing and Training Duration**: average histogram-derived durations.
- **Price Model Error**: latest holdout MAE and RMSE in VND; lower is better.
- **Price Model Training Samples**: sample count of the latest training run.
- **Price Model R2**: latest holdout R²; higher is better, and negative values are
  possible. This is not percentage accuracy.
- **Trainer Freshness**: seconds since the last successful training run.

Price model panels select only `{job="trainer"}` and require a positive
`trainer_last_success_timestamp_seconds` from the same instance. The shared
metrics library also exports uninitialized model gauges from Processors; their
zero values do not represent trained models. A restarted trainer supplies no new
price metrics until its first successful training run. The R², sample count and
freshness stat panels use instant queries so past samples are not displayed as
the current result while this trainer is uninitialized; error charts retain the
measured history.

These are **property-price** metrics. Extraction outcomes and stress delivery
are shown separately in Agent Operations; price R² does not measure either
agent's effectiveness.

The agent operations dashboard contains AI/stress panels, and the source
ingestion dashboard contains crawl quality, access and freshness metrics. API
and MongoDB internal metrics are not provisioned.

## Web application model card

For managers and administrators, the price-model card shows R², MAE, RMSE and
**MdAPE**, the median absolute percentage error. The artifact field
`median_absolute_percentage_error` is a fraction and is displayed as a percentage;
it is not mean absolute percentage error (MAPE). Lower MAE, RMSE and MdAPE are
better. The card also shows the artifact's training timestamp when available.

Health and model information refresh after each completed request, followed by a
30-second delay. A new model artifact is therefore picked up while the page stays
open; the API already reloads when the model file modification time changes.
Requests are not overlapped, and obsolete responses after logout or a role change
are ignored. A failed model-information refresh clears the old metrics instead of
presenting them as current.

## Alert rules

Prometheus loads `monitoring/alert_rules.yml` (evaluation interval 30 seconds):

| Alert | Trigger |
| --- | --- |
| `HighProcessingErrorRate` | failed/consumed rate > 5% for 5m |
| `HighKafkaConsumerLag` | lag > 1000 for 5m |
| `ProcessingDurationAnomaly` | average processing duration > 5s for 5m |
| `ModelTrainingFailed` | any failed trainer run in the last hour, held 10m |
| `ModelTrainingStale` | no successful training for 12h, held 15m |
| `DatabaseWriteFailures` | write failure rate > 0 for 5m |
| `CrawlSourceMetricsUnavailable` | enabled source metric state unreadable for 10m |
| `CrawlSourceRepeatedFailures` | at least two failures in 2h, held 5m |
| `CrawlSourceNotChecked` | no parseable category check for 2h, held 10m |
| `CrawlSourceDataStale` | no valid acknowledged listing for 2d, held 15m |

These rules do not send email/Slack/PagerDuty because Alertmanager is not part
of the repository. Investigate through Prometheus, Grafana and Compose logs.

## Useful PromQL

```promql
rate(kafka_messages_consumed_total[5m])
rate(kafka_messages_processed_total[5m])
rate(kafka_messages_failed_total[5m])
kafka_consumer_lag
rate(processing_duration_seconds_sum[5m]) / rate(processing_duration_seconds_count[5m])
model_mae_vnd{job="trainer"} and on(job, instance) (trainer_last_success_timestamp_seconds{job="trainer"} > 0)
model_rmse_vnd{job="trainer"} and on(job, instance) (trainer_last_success_timestamp_seconds{job="trainer"} > 0)
model_r2{job="trainer"} and on(job, instance) (trainer_last_success_timestamp_seconds{job="trainer"} > 0)
time() - (trainer_last_success_timestamp_seconds{job="trainer"} > 0)
```

## Agent exploration

```promql
ai_agent_enabled
sum(ai_consumer_lag)
sum(rate(ai_extraction_provider_calls_total[5m]))
sum(rate(ai_extraction_failure_total[5m])) by (reason)
sum(rate(ai_cached_results_total[5m]))
sum(rate(stress_delivered_messages_total[5m]))
stress_run_active
```

See [the metric reference](MONITORING_AND_ALERTING.md) for the full inventory.
Agents remain scrapeable while disabled. Enabling AI/stress changes work, not
the monitoring topology. Shared Processor counters combine all input paths;
AI extraction success is not proof that downstream Mongo persistence finished.
