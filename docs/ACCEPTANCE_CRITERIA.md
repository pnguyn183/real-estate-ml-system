# Data Quality Acceptance Criteria

## 1. ACCEPTANCE CRITERIA BY STAGE

### 1.1 Scraper Output Acceptance

**Stage**: Real Estate Raw (Kafka topic `real_estate_raw`)

| Criteria | Threshold | Measurement | Action if Failed |
|----------|-----------|-------------|---------|
| **Completeness** | ≥ 90% | Records with url, listing_id, property_type | WARN if < 85% |
| **Deduplication** | < 0.5% | Duplicate URLs in daily batch | ALERT if > 1% |
| **Format Validity** | 100% | All JSON deserializable | REJECT batch if < 99.5% |
| **Scrape Rate** | 200+ /hour | Messages per hour to Kafka | WARN if < 150 /hour |
| **Timeout Errors** | < 5% | Network timeouts / total requests | ALERT if > 10% |

**Pass/Fail Decision**:
```
ACCEPT if:
  - ✅ All scrape errors < 10%
  - ✅ Deduplication rate < 0.5%
  - ✅ JSON format 100% valid
  - ✅ Daily volume > 1000 records

REJECT if:
  - ❌ Scrape errors > 20%
  - ❌ Cannot connect to Kafka for 30 minutes
  - ❌ Zero records received in 1 hour
```

---

### 1.2 Normalization Output Acceptance

**Stage**: Training Features (MongoDB collection `training_features`)

| Criteria | Threshold | Measurement | Action if Failed |
|----------|-----------|-------------|---------|
| **Feature Accuracy** | ≥ 95% | Correct parsed values (price, area, etc.) | Manual QA review |
| **Consistency** | ≥ 98% | Derived fields math (price/m² = price/area) | Log anomalies |
| **Coverage Score** | ≥ 5/10 | Average key field population | WARN if < 4.5 |
| **NULL Handling** | 100% | No unexpected NULL values | REJECT if found |
| **Candidate Rate** | ≥ 60% | is_model_candidate = True records | WARN if < 50% |
| **Deduplication** | 100% | Unique URLs in collection | Alert if duplicate found |

**Pass/Fail Decision**:
```
ACCEPT if:
  - ✅ Feature accuracy ≥ 95%
  - ✅ Candidate rate ≥ 60%
  - ✅ Duplicate URLs = 0
  - ✅ No NULL values in required fields

WARN if:
  - ⚠️ Feature accuracy 90-95%
  - ⚠️ Candidate rate 50-60%
  - ⚠️ Coverage score < 4.5

REJECT if:
  - ❌ Duplicates found (data integrity fail)
  - ❌ Feature accuracy < 80%
```

---

### 1.3 Model Training Data Acceptance

**Stage**: Before Model Training

| Criteria | Threshold | Measurement | Action if Failed |
|----------|-----------|-------------|---------|
| **Minimum Samples** | ≥ 200 | Model candidate records | REJECT training |
| **Target Distribution** | Reasonable | Price range coverage | Log distribution |
| **Feature Completeness** | ≥ 70% | Features populated per record | WARN if lower |
| **Outlier Rate** | < 5% | Records outside expected ranges | Flag and log |
| **Missing Data** | < 20% | Missing values per feature | WARN if higher |
| **Temporal Freshness** | ≤ 30 days | Age of oldest training record | WARN if older |

**Pass/Fail Decision**:
```
ACCEPT if:
  - ✅ Sample count ≥ 200
  - ✅ All critical features < 20% missing
  - ✅ Data freshness ≤ 30 days
  - ✅ Price range spans 100M - 100B VND

REJECT if:
  - ❌ Sample count < 100
  - ❌ Critical feature > 50% missing
  - ❌ Data age > 90 days
```

---

### 1.4 Model Performance Acceptance

**Stage**: After Model Training

| Metric | Pass Threshold | Warn Threshold | Fail Threshold |
|--------|---------|---------|---------|
| **R² Score** | ≥ 0.75 | 0.70-0.75 | < 0.70 |
| **MAE (VND)** | < 500M | 500M-800M | > 800M |
| **RMSE (VND)** | < 800M | 800M-1.2B | > 1.2B |
| **MAPE (%)** | < 15% | 15-20% | > 20% |
| **Sample Size** | ≥ 5K train | 3K-5K | < 3K |

**Pass/Fail Decision**:
```
ACCEPT & DEPLOY if:
  - ✅ R² ≥ 0.75 AND
  - ✅ MAE < 500M AND
  - ✅ Sample count ≥ 5K AND
  - ✅ Better than previous model (R² improved by > 1%)

WARN & REVIEW if:
  - ⚠️ One metric in WARN range
  - ⚠️ R² between previous model ± 1%
  
REJECT & ROLLBACK if:
  - ❌ R² < 0.70 OR
  - ❌ MAE > 800M OR
  - ❌ One metric in FAIL range
  - ❌ Training data all from single region
```

**Automatic Rollback Trigger**:
```python
if new_model_r2 < previous_model_r2 - 0.05:  # 5% drop
    # Automatic rollback
    deploy(previous_model)
    send_alert("Model performance degraded, rolled back")
```

---

### 1.5 Prediction Output Acceptance

**Stage**: At Prediction Time

| Criteria | Threshold | Action if Failed |
|----------|-----------|---------|
| **Input Validation** | 100% | Reject invalid input with error code |
| **Output Format** | Valid JSON | Return error if serialization fails |
| **Prediction Range** | 10M - 500B VND | Flag predictions outside range as anomalies |
| **Response Time** | < 500ms (95th) | Log if slower, investigate |
| **Error Rate** | < 0.1% | Alert if > 0.5% |

**Pass/Fail Decision**:
```
ACCEPT prediction if:
  - ✅ All required fields populated
  - ✅ Prediction within reasonable range
  - ✅ Generated in < 500ms

RETURN ERROR if:
  - ❌ Required field missing (with field name)
  - ❌ Data type mismatch (with expected type)
  - ❌ Categorical value not in allowed list
```

---

## 2. QUALITY GATES

### Gate 1: Scraper → Kafka
```
Trigger: Every 1 hour
Check: 
  - Records in last hour > 200
  - Error rate < 5%
  - Kafka broker healthy

Action on FAIL:
  - Alert data team
  - Email notification
  - Continue (don't block pipeline)
```

### Gate 2: Kafka → MongoDB
```
Trigger: Every 1 hour
Check:
  - Normalized records > 150
  - Candidate rate > 50%
  - Duplicate URLs = 0
  
Action on FAIL:
  - Check data quality report
  - If critical (duplicates), stop processing
  - Otherwise warn and continue
```

### Gate 3: Pre-Training
```
Trigger: Before daily retraining
Check:
  - Training samples ≥ 200
  - Data freshness ≤ 30 days
  - No schema mismatches

Action on FAIL:
  - Cancel training
  - Use previous model
  - Alert: "Insufficient training data"
```

### Gate 4: Post-Training
```
Trigger: After model training
Check:
  - R² ≥ 0.75
  - MAE < 500M
  - Better than previous model

Action on FAIL:
  - Automatic rollback
  - Use previous model
  - Alert: "Model training failed quality checks"
```

### Gate 5: Deployment
```
Trigger: Before deploying new model
Check:
  - All tests pass (70% coverage)
  - Performance tests OK
  - Integration tests OK

Action on FAIL:
  - Block deployment
  - Require manual approval
  - Email engineering team
```

---

## 3. DATA QUALITY SCORING

### Overall Quality Score Formula
```
Quality Score = (C + N + A + F) / 4

Where:
- C = Completeness score (0-100)
- N = Nullability score (0-100)
- A = Accuracy score (0-100)
- F = Freshness score (0-100)

Target: Score ≥ 85
```

### Completeness Scoring
```
Completeness = (Required fields populated / Total required fields) × 100

Required fields (10):
- url ✅
- listing_id ✅
- property_type ✅
- province_slug ✅
- price_vnd or price_text ✅
- area_m2 or area_text ✅
- (4 others)

Scoring:
- 10/10 fields = 100
- 8/10 fields = 80
- 5/10 fields = 50
- < 5/10 = REJECT
```

### Nullability Scoring
```
Nullability = (Non-null records / Total records) × 100

Critical fields that must never be NULL:
- url
- listing_id
- property_type
- province_slug

Scoring:
- 0% nulls = 100
- 1% nulls = 99
- > 5% nulls = REJECT
```

### Accuracy Scoring
```
Accuracy = (Valid records / Total records) × 100

Validation rules:
- Price: 10M < price_vnd < 500B
- Area: 0 < area_m2 < 10K
- Price per m²: 1M < price/m² < 1B
- Rooms: 0 ≤ bedroom ≤ 20
- Property type: In enum list

Scoring:
- 100% valid = 100
- 95-100% valid = 80-99
- 90-95% valid = 60-79
- < 90% valid = REJECT
```

### Freshness Scoring
```
Freshness = 100 - (hours_since_update / 24 × 100)

Scoring:
- Updated < 1 hour = 100
- Updated 1-6 hours = 75-99
- Updated 6-24 hours = 25-74
- Updated > 24 hours = WARN
- Updated > 30 days = REJECT
```

---

## 4. ACCEPTANCE REPORT

Generate daily report with:

```
═══════════════════════════════════════
   DATA QUALITY ACCEPTANCE REPORT
        [Date: 2024-04-29]
═══════════════════════════════════════

SCRAPER OUTPUT:
  ✅ ACCEPTED - All criteria met
  - Records scraped: 8,234
  - Errors: 42 (0.5%)
  - Duplicates: 0.1%
  - Format valid: 100%

NORMALIZATION:
  ✅ ACCEPTED - All criteria met
  - Records normalized: 8,156
  - Candidate rate: 68%
  - Coverage score: 7.2/10
  - Duplicates: 0

PRE-TRAINING:
  ✅ ACCEPTED - Ready for training
  - Training samples: 5,534 (> 200 ✓)
  - Data freshness: 15 days (< 30 ✓)
  - Quality score: 87% (> 85 ✓)

POST-TRAINING:
  ✅ ACCEPTED - Model deployed
  - R² Score: 0.78 (> 0.75 ✓)
  - MAE: 420M VND (< 500M ✓)
  - Improvement: +0.02 vs previous ✓
  - Sample size: 5,534 (> 5K ✓)

═══════════════════════════════════════
OVERALL: ✅ ALL GATES PASSED
═══════════════════════════════════════
```

---

## 5. STAKEHOLDER SIGN-OFF

Before going to production:

```
Requirement | Owner | Status | Sign-off
-----------|-------|--------|----------
Data Requirements | BA/Product | ✅ APPROVED | Jane (PO)
Schema Definition | Data Engineer | ✅ APPROVED | Bob (Data Lead)
Quality Standards | ML Engineer | ✅ APPROVED | Alice (ML Lead)
Error Handling | DevOps | ✅ APPROVED | Charlie (Ops)
Monitoring Setup | DevOps | ✅ APPROVED | Charlie (Ops)
Infrastructure | DevOps | ✅ APPROVED | Charlie (Ops)
Documentation | Tech Writer | ✅ APPROVED | Diana (Doc)
Security Review | Security | ✅ APPROVED | Eve (SecOps)

All gates: ✅ APPROVED FOR PRODUCTION
```
