# Grafana dashboard and alerts

The dashboard is provisioned from
`monitoring/grafana/dashboards/real_estate_pipeline.json` and reads the local
Prometheus datasource. No external notification channel is configured.

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
- **Model Error**: current MAE and RMSE gauges.
- **Training Samples**: sample count of the latest training run.
- **Model R2**: latest holdout R².
- **Trainer Freshness**: seconds since the last successful training run.

These provisioned panels cover Processor and trainer metrics. Both agents also
expose application metrics, but dedicated AI/stress panels have not been added.
Use Prometheus or Grafana Explore with the queries below. API, scraper, MongoDB
internals and legacy predictor metrics are not present in the dashboard.

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

These rules do not send email/Slack/PagerDuty because Alertmanager is not part
of the repository. Investigate through Prometheus, Grafana and Compose logs.

## Useful PromQL

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

## Agent exploration (not provisioned panels)

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
