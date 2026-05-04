# Product Requirements Document (PRD)

## 1. BUSINESS OBJECTIVES

### Primary Goal
Build an **automated real estate price prediction system** that:
- Accurately predicts property prices based on market data
- Enables data-driven pricing strategies for real estate platforms
- Provides consistent pricing benchmarks across listings

### Success Metrics
- **Prediction Accuracy**: RMSE < 500M VND, R² > 0.75
- **Data Freshness**: Model trained daily from latest 30 days of listings
- **System Uptime**: 99.5% availability for data pipeline
- **Data Quality**: 85%+ of scraped listings are model candidates
- **Latency**: Batch prediction < 100ms per record, API response < 500ms

---

## 2. FUNCTIONAL REQUIREMENTS

### 2.1 Data Ingestion (Scraper)
| Req | Description | Acceptance Criteria |
|-----|-------------|-----|
| F1 | Scrape property listings from batdongsan.com | >5,000 listings/day with <1% duplicate |
| F2 | Resume scraping from last checkpoint | State preserved across restarts |
| F3 | Extract 19 essential features | All required fields populated or null (not error) |
| F4 | Handle pagination | Support up to 1,000 pages per crawl |
| F5 | Verify listings | Priority for verified sellers |

### 2.2 Data Processing
| Req | Description | Acceptance Criteria |
|-----|-------------|-----|
| F6 | Parse price in multiple formats | Correctly parse "1.5 tỷ", "150 triệu", "100M/m²" |
| F7 | Normalize numeric fields | Convert text to numeric with validation |
| F8 | Calculate derived metrics | Price per m² = price_vnd / area_m2 |
| F9 | Score feature coverage | Rate 0-10 based on field completeness |
| F10 | Mark model candidates | Flag records suitable for training |

### 2.3 Model Training
| Req | Description | Acceptance Criteria |
|-----|-------------|-----|
| F11 | Train regression model | Ridge + HistGradientBoosting ensemble |
| F12 | Use text features | TF-IDF vectors from description (3K features) |
| F13 | Handle missing values | Median/most_frequent imputation |
| F14 | Split train/test | 80/20 split, consistent random state |
| F15 | Log metrics | Save MAE, RMSE, R², sample count |

### 2.4 Predictions
| Req | Description | Acceptance Criteria |
|-----|-------------|-----|
| F16 | Single record prediction | CLI interface with JSON input |
| F17 | Batch prediction | Support 1K+ records efficiently |
| F18 | Model versioning | Track model timestamp and training date |
| F19 | Confidence scores | Output prediction interval ±10% |

---

## 3. NON-FUNCTIONAL REQUIREMENTS

### Performance
- **Throughput**: Process 1,000 records/min through pipeline
- **Latency**: Kafka → MongoDB < 5 seconds per message
- **Model Training**: Train on 10K records < 5 minutes
- **Prediction**: Predict 1K records < 30 seconds

### Reliability
- **Idempotency**: Can safely retry failed operations
- **Exactly-once**: No duplicate data in MongoDB
- **Recovery**: Auto-resume after failure
- **Graceful Shutdown**: No data loss on shutdown

### Data Quality
- **Schema Validation**: All fields must match expected types
- **Completeness**: 85%+ of model candidates have 5+ quality fields
- **Accuracy**: Price must be > 0, area_m2 must be > 0
- **Freshness**: Training data not older than 30 days

### Maintainability
- **Code Coverage**: >70% test coverage
- **Logging**: Structured logging (JSON format)
- **Documentation**: All functions documented
- **Type Hints**: 100% type annotation

---

## 4. DATA REQUIREMENTS

### Input Data (Scraped)
```
Source: batdongsan.com.vn
Format: HTML → Parsed JSON
Fields: 19 (see DATA_SCHEMA.md)
Volume: 5,000 - 20,000 records/day
```

### Processing Data
```
Kafka Topics:
- real_estate_raw (input)
- real_estate_features (normalized, 100+ consumers may exist)

MongoDB Database:
- real_estate_db.listings_raw (raw scraped data)
- real_estate_db.training_features (normalized, indexed)
```

### Output Data
```
Model Artifact:
- artifacts/price_model.joblib (binary scikit-learn model)
- artifacts/price_model_metrics.json (metrics)
- Training dataset: parquet/csv/jsonl

Predictions:
- JSON format with predicted_price_vnd and confidence
```

---

## 5. CONSTRAINTS & DEPENDENCIES

### Technical
- Python 3.9+
- Kafka 7.5+
- MongoDB 7.0+
- scikit-learn 1.3+
- Docker for deployment

### Operational
- Can only scrape 5-20K records/day (website rate limits)
- Model retraining required weekly minimum
- Must respect website's robots.txt and crawl delays

### Business
- Must comply with batdongsan.com's terms of service
- Data retention: Keep 30 days rolling window
- Model accuracy acceptance: Must beat 0.75 R² or revert to previous

---

## 6. ASSUMPTIONS

1. Website structure remains consistent
2. Historical price data is reliable
3. Features scraped are predictive of price
4. Normal market conditions (no economic shocks)
5. Listing volume stable at 5-20K/day

---

## 7. OUT OF SCOPE

- ❌ Real-time streaming predictions (batch only)
- ❌ Multiple property types modeling (single model)
- ❌ A/B testing framework
- ❌ Web UI for predictions
- ❌ Data lake (Parquet only, not Spark)
