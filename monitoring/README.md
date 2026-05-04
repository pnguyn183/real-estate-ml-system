# Monitoring & Alerting

This folder contains configuration for Prometheus and alerting rules.

## Files

- **prometheus.yml**: Prometheus configuration, scrape targets, alert rules
- **alert_rules.yml**: Alert rules for pipeline monitoring

## Key Metrics

### Processor (Kafka Consumer → MongoDB)
- `kafka_messages_consumed_total`: Total messages received from Kafka
- `kafka_messages_processed_total`: Total messages successfully processed
- `kafka_messages_failed_total`: Total failed messages
- `processing_duration_seconds`: Histogram of processing time
- `db_writes_success_total`: Successful database writes
- `db_writes_failed_total`: Failed database writes

### Trainer (Model Training)
- `model_train_duration_seconds`: Time taken for model training
- `model_mae_vnd`: Model Mean Absolute Error (in VND)
- `model_rmse_vnd`: Model Root Mean Squared Error (in VND)
- `model_r2`: Model R² score (0-1)
- `model_sample_count`: Number of samples used in training

## Alerts

The following alerts are configured:

- **HighProcessingErrorRate**: Error rate > 5% for 5 minutes
- **HighKafkaConsumerLag**: Consumer lag > 1000 messages
- **ProcessingDurationAnomaly**: Average processing time > 5 seconds
- **ModelTrainingFailed**: No training in 6-hour window (scheduled interval)
- **DatabaseWriteFailures**: Any database write failures detected

## Access

- **Prometheus**: http://localhost:9090
  - Query metrics with PromQL
  - View alert status
  - View scrape targets

- **Grafana**: http://localhost:3001
  - Create dashboards
  - Visualize metrics
  - Setup alerting notifications

## Adding Custom Alerts

1. Add rule to `alert_rules.yml`
2. Reload Prometheus (restart container or send SIGHUP)
3. View alert status in http://localhost:9090/alerts

## Setup Alerting Notifications

To receive alerts (email, Slack, etc.), configure Alertmanager in `prometheus.yml` and add notification channel in Grafana.
