# Metrics, KPIs & SLA Definition

## 1. BUSINESS KPIs

### 1.1 Prediction Quality
| KPI | Target | Frequency | Owner |
|-----|--------|-----------|-------|
| R² Score | > 0.75 | Daily | Data Science |
| MAE (VND) | < 500M | Daily | Data Science |
| RMSE (VND) | < 800M | Daily | Data Science |
| MAPE (%) | < 15% | Weekly | Data Science |
| Accuracy within ±20% | > 80% | Weekly | Data Science |

### 1.2 Model Health
| KPI | Target | Frequency | Owner |
|-----|--------|-----------|-------|
| Model age (days) | < 7 | Daily | MLOps |
| Training dataset size | > 5K candidates | Daily | Data Eng |
| Data quality score | > 85% | Daily | Data Eng |
| Training success rate | 100% | Daily | MLOps |
| Model drift detection | < 5% deviation | Weekly | Data Science |

### 1.3 Pipeline Health
| KPI | Target | Frequency | Owner |
|-----|--------|-----------|-------|
| Data pipeline uptime | 99.5% | Daily | Data Eng |
| Daily listings scraped | 5K - 20K | Daily | Data Eng |
| Data freshness | < 1 hour lag | Hourly | Data Eng |
| Error rate | < 0.5% | Hourly | Data Eng |
| Data duplication rate | < 0.1% | Daily | Data Eng |

---

## 2. OPERATIONAL METRICS

### 2.1 Throughput
| Metric | Target | Measurement |
|--------|--------|-------------|
| Listings scraped/hour | 200+ | real_estate_raw topic produced |
| Messages normalized/hour | 180+ | real_estate_features topic produced |
| Records trained per batch | > 5K | training_features collection |
| Predictions per minute | 1K+ | Model serving capacity |

### 2.2 Latency
| Operation | Target | 95th Percentile | 99th Percentile |
|-----------|--------|---------|---------|
| Single scrape | 5 sec | 8 sec | 15 sec |
| Kafka → MongoDB | 2 sec | 5 sec | 10 sec |
| Model prediction | 100 ms | 200 ms | 500 ms |
| Batch prediction (1K) | 30 sec | 45 sec | 60 sec |
| Model training (10K) | 5 min | 7 min | 10 min |

### 2.3 Resource Utilization
| Resource | Target | Alert Level |
|----------|--------|------------|
| CPU usage | < 70% | > 85% |
| Memory usage | < 75% | > 90% |
| Disk space (MongoDB) | < 80% | > 95% |
| Kafka lag | < 1000 msgs | > 5000 msgs |
| Network I/O | < 10 Mbps | > 50 Mbps |

---

## 3. DATA QUALITY METRICS

### 3.1 Completeness
```
completeness_score = (fields_populated / total_fields) × 100

By field:
- price_text: > 90%
- area_text: > 85%
- property_type: > 98%
- province_slug: > 99%
- description: > 70%

Alert: If completeness < 80%
```

### 3.2 Accuracy
```
accuracy_score = (valid_records / total_records) × 100

Validation checks:
- Price: Must be > 0, < 500B VND
- Area: Must be > 0, < 10K m²
- Price per m²: Must be > 1M, < 1B VND
- Property type: Must be valid enum value

Alert: If accuracy < 95%
```

### 3.3 Timeliness (Freshness)
```
data_age = now() - scraped_at

Targets:
- Real-time scraped data: < 1 hour
- Training data: < 30 days
- Model: < 7 days

Alert levels:
- WARNING: Real-time data > 4 hours
- CRITICAL: Model > 14 days
```

### 3.4 Consistency
```
duplicate_rate = (duplicate_URLs / total_records) × 100

Alert: If duplicate_rate > 1%

Data consistency checks:
- URL uniqueness: Must be unique key
- Timestamp consistency: scraped_at ≤ updated_at
- Price consistency: price_per_m2 = price_vnd / area_m2 ± 1%
```

---

## 4. SERVICE LEVEL AGREEMENTS (SLA)

### 4.1 Scraper SLA
```
Service: Real Estate Listing Scraper
Owner: Data Engineering
Hours: 24/7

Availability: 99% (21.6 min downtime/week)
Response Time: 5 sec per listing (95th percentile)

Monthly target:
- Minimum 150K listings scraped
- Maximum 0.5% duplication rate
- Maximum 2% malformed records

SLA Credits:
- 99-99.5%: 10% refund
- 95-99%: 25% refund
- < 95%: 50% refund
```

### 4.2 Processing Pipeline SLA
```
Service: Kafka → MongoDB Normalization
Owner: Data Engineering
Hours: 24/7

Availability: 99.5%
End-to-end latency: < 5 min (95th percentile)

Monthly target:
- Process 95% of scraped data within 5 minutes
- Zero data loss
- < 0.1% duplicate records in MongoDB

SLA Credits:
- Data loss incident: 100% outage credit
- Latency > 10 min (sustained): 25% refund
```

### 4.3 Model Training SLA
```
Service: Daily Model Retraining
Owner: ML Engineering
Hours: Daily 2 AM - 4 AM UTC

Training window: < 10 minutes
Success rate: 100% (or automatic rollback)

Monthly target:
- 30/30 successful trainings
- Model accuracy maintained (R² > 0.75)
- Zero failed deployments

Alert: If training takes > 15 min, escalate
```

### 4.4 Prediction API SLA
```
Service: Real Estate Price Prediction
Owner: ML Engineering
Hours: 24/7

Availability: 99.5%
Response time: < 500 ms (95th percentile)
Error rate: < 0.1%

Monthly target:
- 99.5% of requests succeed
- < 1 prediction error per 1000 requests
- Prediction confidence ± 10-20%

SLA Credits:
- 99-99.5%: 10% refund
- 95-99%: 25% refund
- < 95%: 50% refund
```

---

## 5. ERROR RATE TARGETS

### By Severity
| Severity | Error Rate | Alert Threshold | Action |
|----------|-----------|---------|--------|
| Critical | < 0.01% | > 0.05% | IMMEDIATE page |
| High | < 0.1% | > 0.5% | 30 min escalation |
| Medium | < 1% | > 5% | 1 hour escalation |
| Low | < 5% | > 20% | Monitor |

### By Stage
| Stage | Target Error Rate | Alert Level |
|-------|---------|------------|
| Scraping | < 2% | > 5% |
| Parsing | < 1% | > 2% |
| Normalization | < 0.5% | > 1% |
| Training | < 0.1% | > 0.5% |
| Prediction | < 0.1% | > 0.5% |

---

## 6. COST METRICS

### Infrastructure Costs
| Component | Monthly Cost | Alert Threshold |
|-----------|---------|------------|
| Kafka cluster | $200 | Budget variance > 10% |
| MongoDB | $150 | Data growth > 50GB |
| Compute (scraper) | $100 | CPU overprovisioned |
| Storage (model artifacts) | $50 | | Archive old models |
| **Total** | **$500** | | |

### Cost per Prediction
```
Cost per prediction = Total monthly cost / Total predictions

Target: < $0.001 per prediction
```

---

## 7. COMPLIANCE METRICS

### Data Privacy
| Metric | Target | Audit Frequency |
|--------|--------|---------|
| PII exposure incidents | 0 | Real-time |
| Data retention compliance | 100% | Monthly |
| GDPR delete requests SLA | < 30 days | Per request |

### Audit & Logging
| Metric | Target |
|--------|--------|
| Log retention | 90 days |
| Audit trail completeness | 100% |
| Access log detail | User, action, resource, timestamp |

---

## 8. MONITORING DASHBOARD QUERIES

### Real-time Dashboard (Updated every 1 min)
```sql
-- Pipeline Health
SELECT
  COUNT(*) as total_records,
  SUM(CASE WHEN updated_at > now() - interval '1 hour' THEN 1 ELSE 0 END) as records_last_hour,
  MAX(updated_at) as last_update,
  COUNT(DISTINCT property_type) as property_types_covered
FROM training_features
WHERE scraped_at > now() - interval '24 hours';

-- Quality Score
SELECT
  COUNT(*) as total,
  SUM(CASE WHEN is_model_candidate THEN 1 ELSE 0 END) as candidates,
  ROUND(100.0 * SUM(CASE WHEN is_model_candidate THEN 1 ELSE 0 END) / COUNT(*), 2) as candidate_ratio,
  ROUND(AVG(feature_coverage_score), 2) as avg_coverage
FROM training_features
WHERE updated_at > now() - interval '24 hours';

-- Error Rate
SELECT
  COUNT(*) as total,
  SUM(CASE WHEN has_error THEN 1 ELSE 0 END) as errors,
  ROUND(100.0 * SUM(CASE WHEN has_error THEN 1 ELSE 0 END) / COUNT(*), 2) as error_rate
FROM pipeline_logs
WHERE timestamp > now() - interval '1 hour';
```

---

## 9. REPORT FREQUENCY

| Report | Frequency | Owner | Audience |
|--------|-----------|-------|----------|
| Data Quality Report | Daily | Data Eng | Team |
| Model Performance Report | Daily | ML Eng | Team + Stakeholders |
| Pipeline Incident Report | As-needed | DevOps | Leadership |
| Monthly SLA Report | Monthly | Data Eng Lead | Executives |
| Cost Analysis Report | Monthly | Finance | Budget review |
