import logging
from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)

# Metrics
kafka_messages_consumed = Counter("kafka_messages_consumed_total", "Total Kafka messages consumed")
kafka_messages_processed = Counter("kafka_messages_processed_total", "Total Kafka messages processed")
kafka_messages_failed = Counter("kafka_messages_failed_total", "Total Kafka messages failed")
kafka_consumer_lag = Gauge("kafka_consumer_lag", "Kafka consumer lag")
db_writes_success = Counter("db_writes_success_total", "Total successful DB writes")
db_writes_failed = Counter("db_writes_failed_total", "Total failed DB writes")
processing_duration = Histogram("processing_duration_seconds", "Processing duration in seconds")
model_train_duration = Histogram("model_train_duration_seconds", "Model training duration in seconds")
model_mae = Gauge("model_mae_vnd", "Model MAE in VND")
model_rmse = Gauge("model_rmse_vnd", "Model RMSE in VND")
model_r2 = Gauge("model_r2", "Model R2 score")
model_sample_count = Gauge("model_sample_count", "Model training sample count")
trainer_runs = Counter("trainer_runs_total", "Total model training runs")
trainer_runs_failed = Counter("trainer_runs_failed_total", "Total failed model training runs")
trainer_last_run_timestamp = Gauge("trainer_last_run_timestamp_seconds", "Unix timestamp of the last training run")
trainer_last_success_timestamp = Gauge(
    "trainer_last_success_timestamp_seconds",
    "Unix timestamp of the last successful training run",
)

def start_prometheus_server(port: int = 8000):
    """Start Prometheus metrics server on specified port"""
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on port {port}")
    except Exception as exc:
        logger.error(f"Failed to start Prometheus server: {exc}")

def update_metrics_from_result(result):
    """Update model metrics from training result"""
    try:
        metrics = result.metrics
        model_mae.set(metrics.get("mae_vnd", 0))
        model_rmse.set(metrics.get("rmse_vnd", 0))
        model_r2.set(metrics.get("r2", 0))
        model_sample_count.set(metrics.get("sample_count", 0))
    except Exception as exc:
        logger.error(f"Error updating metrics: {exc}")
