# Monitoring, Alerting & Observability

## 1. MONITORING ARCHITECTURE

```
┌─────────────────────────────────────────────────┐
│           Monitoring Infrastructure              │
├─────────────────────────────────────────────────┤
│                                                   │
│  Application Logs → ELK Stack (Elasticsearch)   │
│  Metrics → Prometheus → Grafana Dashboard       │
│  Traces → Jaeger (optional)                     │
│  Alerts → PagerDuty + Slack                     │
│  Events → Kafka audit topic                     │
│                                                   │
└─────────────────────────────────────────────────┘
```

---

## 2. KEY METRICS TO MONITOR

### 2.1 Scraper Metrics

**Volume Metrics:**
```
scraper_records_scraped_total (counter)
  - Labels: hourly, daily
  - Alert: < 100/hour (too slow)
  
scraper_duplicate_rate (gauge, %)
  - Target: < 0.5%
  - Alert: > 1%

scraper_error_rate (gauge, %)
  - By error type (network, parsing, etc.)
  - Alert: > 5%
```

**Latency Metrics:**
```
scraper_page_fetch_seconds (histogram)
  - p50, p95, p99
  - Alert if p95 > 10 seconds

scraper_detail_parse_seconds (histogram)
  - p50, p95, p99
  - Alert if p95 > 5 seconds
```

**Resource Metrics:**
```
scraper_memory_mb (gauge)
  - Alert if > 500MB
  
scraper_cpu_percent (gauge)
  - Alert if > 80%
  
scraper_network_bandwidth_mbps (gauge)
```

### 2.2 Kafka Metrics

**Producer Side:**
```
kafka_produce_latency_ms (histogram)
  - p50, p95, p99
  - Alert if p95 > 500ms

kafka_produce_errors_total (counter)
  - By error type
  - Alert if rate > 0.1/sec

kafka_queue_size (gauge)
  - Alert if > 1000 messages
```

**Consumer Side:**
```
kafka_consumer_lag (gauge)
  - Per partition, per consumer group
  - Alert if > 1000 messages

kafka_consume_latency_ms (histogram)
  - Alert if p95 > 1000ms

kafka_consume_errors_total (counter)
  - Alert if rate > 0.1/sec
```

### 2.3 MongoDB Metrics

**Connection Metrics:**
```
mongodb_connections_active (gauge)
  - Alert if > 80% of pool

mongodb_connection_pool_size (gauge)
  - Current size and limit
```

**Operation Metrics:**
```
mongodb_operations_total (counter)
  - By operation: insert, update, find, etc.

mongodb_operation_duration_ms (histogram)
  - p50, p95, p99 per operation
  - Alert if p95 > 500ms

mongodb_operation_errors_total (counter)
  - By error type
```

**Storage Metrics:**
```
mongodb_collection_size_bytes (gauge)
  - Per collection
  - Alert if > 50GB

mongodb_index_size_bytes (gauge)

mongodb_disk_free_bytes (gauge)
  - Alert if < 10% free
```

### 2.4 Model Metrics

**Training Metrics:**
```
model_training_duration_seconds (histogram)
  - Alert if > 600 seconds (10 min)

model_training_success_total (counter)
  - Track success rate

model_training_errors_total (counter)

model_training_sample_count (gauge)
  - Training set size

model_r2_score (gauge)
  - Alert if < 0.75

model_mae_vnd (gauge)
  - Alert if > 500M

model_rmse_vnd (gauge)
  - Alert if > 800M
```

**Prediction Metrics:**
```
model_prediction_latency_ms (histogram)
  - p50, p95, p99
  - Alert if p95 > 500ms

model_prediction_errors_total (counter)
  - By error type

model_prediction_count (counter)
  - Hourly, daily totals
```

### 2.5 Pipeline Metrics

**End-to-End Latency:**
```
pipeline_scrape_to_kafka_ms (histogram)
  - Time from scrape start to Kafka publish
  - p95 target: < 10 seconds

pipeline_kafka_to_mongodb_ms (histogram)
  - Time from Kafka consume to MongoDB write
  - p95 target: < 5 seconds

pipeline_total_latency_minutes (gauge)
  - Time from scrape to prediction ready
  - p95 target: < 30 minutes
```

**Data Quality Metrics:**
```
pipeline_data_freshness_minutes (gauge)
  - Age of newest record
  - Alert if > 60 minutes

pipeline_quality_score (gauge 0-100)
  - Overall data quality
  - Alert if < 80%

pipeline_duplicate_rate (gauge, %)
  - Alert if > 0.5%

pipeline_candidate_rate (gauge, %)
  - % of records suitable for training
  - Alert if < 50%
```

---

## 3. ALERTING RULES

### 3.1 Critical Alerts (Page on-call, Immediate Action Required)

```yaml
Alert: ScraperNotProducing
  Condition: kafka_produce_rate < 10 msg/min for 5 minutes
  Severity: CRITICAL
  Action: Page on-call → Check Kafka & scraper logs
  Runbook: docs/runbooks/scraper_stuck.md

Alert: MongoDBDown
  Condition: mongodb_connections_active = 0 for 2 minutes
  Severity: CRITICAL
  Action: Page on-call → Restart MongoDB or failover
  Runbook: docs/runbooks/mongodb_down.md

Alert: KafkaBrokerDown
  Condition: kafka_broker_up = 0 for 1 minute
  Severity: CRITICAL
  Action: Page on-call → Check broker logs
  Runbook: docs/runbooks/kafka_broker_down.md

Alert: ModelTrainingFailed
  Condition: model_training_errors_total increased by 1 in last 24h
           AND model_training_success_total not increased
  Severity: CRITICAL
  Action: Page ML engineer → Check training logs
  Runbook: docs/runbooks/model_training_failed.md

Alert: PipelineDataLoss
  Condition: pipeline_scrape_to_kafka_error_rate > 10% for 5 min
  Severity: CRITICAL
  Action: Page data team → Stop consumers, investigate
  Runbook: docs/runbooks/pipeline_data_loss.md
```

### 3.2 High Priority Alerts (Email + Slack, within 1 hour)

```yaml
Alert: HighErrorRate
  Condition: pipeline_error_rate > 5% for 10 minutes
  Severity: HIGH
  Action: Investigate error type, may be partial outage
  Notification: team-data-eng@company.com, #data-alerts

Alert: LowDataQuality
  Condition: pipeline_quality_score < 80% for 1 hour
  Severity: HIGH
  Action: Check scraper for issues, may need investigation
  Notification: team-data-eng@company.com

Alert: HighLatency
  Condition: pipeline_total_latency_minutes > 60 for 15 min
  Severity: HIGH
  Action: Check resource utilization, may need scaling
  Notification: team-data-eng@company.com, #ops-alerts

Alert: LowCandidateRate
  Condition: pipeline_candidate_rate < 50% for 1 hour
  Severity: HIGH
  Action: Website format may have changed, manual review
  Notification: team-data-eng@company.com

Alert: ModelPerformanceDegraded
  Condition: model_r2_score < 0.75 OR model_mae_vnd > 500M
  Severity: HIGH
  Action: Automatic rollback triggered, investigate cause
  Notification: team-ml-eng@company.com
```

### 3.3 Medium Priority Alerts (Slack, within 4 hours)

```yaml
Alert: SlowPrediction
  Condition: model_prediction_latency_p95 > 500ms for 30 min
  Severity: MEDIUM
  Action: Check model complexity, may need optimization
  Notification: #data-alerts

Alert: HighMemoryUsage
  Condition: scraper_memory_mb > 500 OR model_memory_mb > 1000
  Severity: MEDIUM
  Action: Investigate memory leak, may need restart
  Notification: #ops-alerts

Alert: DatabaseSizeGrowing
  Condition: mongodb_collection_size_bytes increase > 20% in 1 day
  Severity: MEDIUM
  Action: Check data retention, may need cleanup
  Notification: #ops-alerts

Alert: KafkaLagIncreasing
  Condition: kafka_consumer_lag > 5000 and increasing
  Severity: MEDIUM
  Action: Consumer may be slow, increase throughput or scale
  Notification: #data-alerts
```

### 3.4 Low Priority Alerts (Metric tracking, weekly review)

```yaml
Alert: RateLimitApproaching
  Condition: scraper_request_rate > 80% of website limit
  Severity: LOW
  Action: Monitor and adjust delays if needed
  Notification: Weekly report only

Alert: DiskSpaceWarning
  Condition: mongodb_disk_free_bytes < 20% of total
  Severity: LOW
  Action: Plan for disk expansion
  Notification: Weekly report only
```

---

## 4. DASHBOARD STRUCTURE

### Dashboard 1: Pipeline Health (Real-time, updated every 1 min)
```
┌─────────────────────────────────────────┐
│ REAL ESTATE PIPELINE HEALTH DASHBOARD   │
├─────────────────────────────────────────┤
│                                          │
│ Status: [🟢 HEALTHY] [Last update: 1s] │
│                                          │
│ ┌─ VOLUME ─────────────────────────┐   │
│ │ Scraped today: 8,234 ✓           │   │
│ │ Rate: 342/hour (target: >200)    │   │
│ │ Kafka produced: 8,234            │   │
│ │ MongoDB normalized: 8,156        │   │
│ │ Error rate: 0.5% (target: <5%)  │   │
│ └──────────────────────────────────┘   │
│                                          │
│ ┌─ QUALITY ─────────────────────────┐  │
│ │ Quality score: 87/100 ✓           │  │
│ │ Candidate rate: 68% (target: >60%)│  │
│ │ Duplicate rate: 0.1% (target: <0.5%)│ │
│ │ Freshness: 5 min (target: <60min)│  │
│ └──────────────────────────────────┘   │
│                                          │
│ ┌─ INFRASTRUCTURE ──────────────────┐  │
│ │ Kafka: 🟢 Healthy (3 brokers)    │  │
│ │ MongoDB: 🟢 Healthy (47.2 GB)    │  │
│ │ Scraper: 🟢 Running (mem: 180MB) │  │
│ │ Model: 🟢 Serving (age: 1 day)   │  │
│ └──────────────────────────────────┘   │
│                                          │
│ Recent Alerts: None                     │
│                                          │
└─────────────────────────────────────────┘
```

### Dashboard 2: Model Performance (Hourly)
```
┌──────────────────────────────────────┐
│ MODEL PERFORMANCE DASHBOARD          │
├──────────────────────────────────────┤
│ Current Model: v3 (trained 2024-04-28│
│                                      │
│ ┌─ METRICS ────────────────────────┐│
│ │ R² Score: 0.78 ✓                 ││
│ │ MAE: 420M VND ✓ (target: <500M)  ││
│ │ RMSE: 680M VND ✓ (target: <800M) ││
│ │ Prediction latency p95: 120ms ✓  ││
│ └──────────────────────────────────┘│
│                                      │
│ ┌─ PREDICTIONS ────────────────────┐│
│ │ Today: 1,234 predictions         ││
│ │ Error rate: 0% ✓                 ││
│ │ Avg confidence: 87%              ││
│ └──────────────────────────────────┘│
│                                      │
│ ┌─ COMPARISON ─────────────────────┐│
│ │ vs Previous: R² +0.03 ✓ (better) ││
│ │ vs Target: R² +0.03 ✓ (met)      ││
│ └──────────────────────────────────┘│
│                                      │
└──────────────────────────────────────┘
```

### Dashboard 3: Data Quality (Hourly)
```
Includes: Completeness chart, Nullability rate,
Accuracy by field, Freshness timeline, etc.
```

### Dashboard 4: Infrastructure (Real-time)
```
CPU, Memory, Disk, Network usage graphs
Kafka broker status, MongoDB replica status
```

---

## 5. LOGGING STANDARDS

### 5.1 Structured Logging (JSON format)

```python
# Example: Scraper logging
logger.info("scrape_complete", extra={
    "timestamp": "2024-04-29T10:30:00Z",
    "page": 5,
    "records_found": 42,
    "duration_seconds": 3.5,
    "errors": 0,
    "status": "success"
})

# Example: Error logging
logger.error("scrape_failed", extra={
    "timestamp": "2024-04-29T10:30:00Z",
    "url": "https://batdongsan.com.vn/...",
    "error_code": "001",
    "error_message": "Connection timeout",
    "retry_attempt": 3,
    "status": "failed",
    "action_taken": "Added to DLQ"
})
```

### 5.2 Log Levels
```
CRITICAL: System shutdown, data loss (e.g., MongoDB down)
ERROR: Operation failed, needs intervention (e.g., training failed)
WARNING: Degraded service, but continuing (e.g., slow latency)
INFO: Normal operations (scrape completed, model trained)
DEBUG: Detailed debugging info (variable values, flow tracing)
```

### 5.3 Log Retention
```
Application logs: 30 days
Error logs: 90 days
Audit logs: 1 year
Debug logs: 7 days
```

---

## 6. RUNBOOKS (Quick Start Guides)

### Runbook Template: `runbooks/README.md`

Each critical alert should have a runbook:
```
Title: [Alert Name]
Severity: [CRITICAL/HIGH/MEDIUM]
Components Affected: [List]

Symptoms:
- Alert triggered
- [Specific symptoms]

Diagnosis:
1. Check [specific metric/log]
2. Verify [service status]
3. Look for [error pattern]

Resolution:
Option A (Most likely):
  1. Step 1
  2. Step 2
  
Option B (Alternative):
  1. Step 1
  2. Step 2

Prevention:
- [Preventive measure]

Escalation:
- If not resolved in X minutes, page [team]
```

---

## 7. ON-CALL ROTATION

```
On-call schedule: Weekly rotation
Escalation: Page on-call if CRITICAL alert not acknowledged in 5 min
Page: PagerDuty (SMS + call)
Communication: Slack #data-incidents channel
Post-incident: Write postmortem within 24 hours
```

---

## 8. MONITORING CHECKLIST

**Daily Checks (5 min):**
- [ ] Pipeline health dashboard green
- [ ] No unresolved CRITICAL alerts
- [ ] Data quality score > 80%
- [ ] No DLQ messages (dead letter queue)

**Weekly Checks (30 min):**
- [ ] Review SLA metrics
- [ ] Check cost trends
- [ ] Analyze error patterns
- [ ] Review disk/resource usage

**Monthly Reviews (2 hours):**
- [ ] Analyze performance trends
- [ ] Review alert rules (accuracy)
- [ ] Update runbooks if needed
- [ ] Plan capacity for next month
