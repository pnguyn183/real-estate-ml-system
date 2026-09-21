# Monitoring

The repository contains a Prometheus/Grafana setup only. It does not contain
ELK, Elasticsearch, Jaeger, Alertmanager or notification integrations.

## Files

- `prometheus.yml`: 15-second scrape interval, targets and rule file.
- `alert_rules.yml`: six implemented alert rules.
- `grafana/provisioning/datasources/prometheus.yml`: Prometheus datasource.
- `grafana/provisioning/dashboards/pipeline.yml`: dashboard provisioning.
- `grafana/dashboards/real_estate_pipeline.json`: processor/trainer dashboard.
- `grafana/dashboards/agent_operations.json`: dedicated AI, stress and safety
	dashboard for the agent demo.

## Scrape targets

| Target | Exposes |
| --- | --- |
| `processor:8003` | Worker 1 Kafka consumption/processing, lag, Mongo write and duration metrics |
| `processor-2:8004` | Worker 2 metrics; same consumer group |
| `processor-3:8005` | Worker 3 metrics; same consumer group |
| `trainer:8001` | Training duration, model metrics, run/failure and freshness metrics |
| `ai-agent:8006` (DNS discovery) | `/metrics`: extraction, cache, retries, circuit state, committed-offset lag; `/health`: process/enabled state |
| `stress-agent:8007` | Generation, broker-acknowledged delivery, failures, duration and run state |
| `localhost:9090` | Prometheus self-scrape |

The API, scraper, MongoDB and legacy predictor do not expose Prometheus metrics
in the current implementation.
AI and stress ports are internal-only. Both agents expose health/metrics while
disabled; disabled AI deliberately has no Kafka group membership or API calls.
AI DNS discovery refreshes every 15 seconds to cover scalable service replicas.
See [the metric inventory](../docs/MONITORING_AND_ALERTING.md) for exact names.

## Alerts

| Alert | Expression summary | Hold |
| --- | --- | --- |
| `HighProcessingErrorRate` | failed/consumed rate > 5% | 5m |
| `HighKafkaConsumerLag` | lag > 1000 | 5m |
| `ProcessingDurationAnomaly` | average processing > 5s | 5m |
| `ModelTrainingFailed` | failed trainer run in previous hour | 10m |
| `ModelTrainingStale` | no successful run for 12h | 15m |
| `DatabaseWriteFailures` | write failure rate > 0 | 5m |

Prometheus evaluates the rule group every 30 seconds. Alerts are visible at
`/alerts` and in Grafana; no receiver sends them elsewhere.

## Access

```bash
docker compose up -d prometheus grafana processor processor-2 processor-3 trainer ai-agent stress-agent
```

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001` (credentials are Compose-configured for local use)
- Processor metrics: `http://localhost:8003/metrics`, `http://localhost:8004/metrics`, `http://localhost:8005/metrics`
- Trainer metrics: `http://localhost:8001/metrics`

The dedicated agent dashboard is provisioned automatically alongside the
pipeline dashboard. It does not claim extraction accuracy or confidence
calibration: confidence is the provider's validated self-report, while actual
accuracy still requires labelled evaluation data.

```bash
docker compose exec -T ai-agent curl -fsS http://localhost:8006/metrics
docker compose exec -T stress-agent curl -fsS http://localhost:8007/metrics
```
