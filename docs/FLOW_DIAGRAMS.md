# Data Pipeline Flow Diagrams & Architecture

## 1. OVERALL PIPELINE ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────┐
│                    REAL ESTATE PRICE PREDICTION                  │
│                         DATA PIPELINE                            │
└─────────────────────────────────────────────────────────────────┘

                         ┌─────────────────┐
                         │ batdongsan.com  │
                         │   (Website)     │
                         └────────┬────────┘
                                  │ (HTTP)
                                  ▼
                    ┌──────────────────────────┐
                    │   Scraper Service        │
                    │ (kafka_producer.py)      │
                    │                          │
                    │ • Resume checkpoint      │
                    │ • Rate limiting          │
                    │ • Deduplication          │
                    └────────────┬─────────────┘
                                 │ (JSON payload)
                                 ▼
                    ┌──────────────────────────┐
                    │  Kafka Topic             │
                    │  real_estate_raw         │
                    │ (retention: 7 days)      │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────┬┴──────────────┐
                    │            │               │
          ┌─────────▼──┐ ┌───────▼────────┐ (DLQ)
          │  Processor  │ │   Raw Archive  │
          │  Service    │ │ (MongoDB coll) │
          │  (kafka_to_ │ │ listings_raw   │
          │  mongo.py)  │ └────────────────┘
          │             │
          │ • Parse     │
          │ • Normalize │
          │ • Validate  │
          └─────────┬───┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   ┌─────────┐ ┌────────────┐ ┌──────┐
   │Kafka    │ │ MongoDB    │ │Dead  │
   │topic:   │ │ collection:│ │Letter│
   │features │ │training_   │ │Queue │
   │         │ │features    │ │(DLQ) │
   └────┬────┘ └─────┬──────┘ └──────┘
        │            │
        │     (Indexed by:    )
        │      url, property_ )
        │      type, price_vnd)
        │      province_slug  )
        │                     │
        └─────────┬───────────┘
                  │
                  ▼
        ┌─────────────────────┐
        │  Model Training     │
        │  (train_model.py)   │
        │                     │
        │ • Load candidates   │
        │ • Feature engineer  │
        │ • Train ensemble    │
        │ • Validate metrics  │
        │ • Store artifacts   │
        └────────┬────────────┘
                 │
    ┌────────────┼────────────┐
    ▼            ▼            ▼
 ┌──────┐  ┌──────────┐  ┌─────────┐
 │Model │  │ Metrics  │  │ Dataset │
 │ JAR  │  │  JSON    │  │Exports  │
 │(.job │  │(.json)   │  │(parquet)│
 │ lib) │  │          │  │         │
 └──┬───┘  └──────────┘  └─────────┘
    │
    ▼
┌──────────────────────────┐
│  Prediction Service      │
│  (predict_price.py)      │
│  (Future: FastAPI)       │
│                          │
│ • Load model             │
│ • Validate input         │
│ • Generate prediction    │
│ • Return confidence      │
└──────────────────────────┘
```

---

## 2. DETAILED DATA FLOW (WITH DECISION POINTS)

```
START: Website Crawl
│
├─► [Checkpoint exists?]
│   ├─ Yes ─► Resume from page X
│   └─ No  ─► Start from page 1
│
├─► [Fetch page]
│   ├─ [Network error?]
│   │  ├─ Yes ─► Retry with backoff (max 4x)
│   │  │         ├─ [All retries failed?]
│   │  │         │  ├─ Yes ─► Log error, SKIP PAGE
│   │  │         │  └─ No  ─► Continue
│   │  └─ No  ─► Extract URLs
│   │
│   └─► [Parse listing page]
│        ├─ [Found URLs?]
│        │  ├─ Yes ─► Process each URL
│        │  └─ No  ─► Reached last page, STOP
│        │
│        └─► [For each URL]
│             ├─ [Seen before?] (dedup check)
│             │  ├─ Yes ─► Skip, log duplicate
│             │  └─ No  ─► Fetch detail page
│             │
│             └─ [Fetch detail page]
│                ├─ [Error?]
│                │  ├─ Yes ─► Retry or skip
│                │  └─ No  ─► Parse fields
│                │
│                └─ [Parse fields]
│                   ├─ [Parsing error?]
│                   │  ├─ Yes ─► Log error, store raw
│                   │  └─ No  ─► Publish to Kafka
│                   │
│                   └─ [Publish to Kafka]
│                      ├─ [Kafka error?]
│                      │  ├─ Yes ─► Retry, then DLQ
│                      │  └─ No  ─► Mark sent
│                      │
│                      └─ [Save checkpoint]
│                         └─ Next page
│
└─► [Limit reached?]
    ├─ [Yes: max_items] ─► STOP
    ├─ [Yes: max_pages] ─► STOP
    └─ [No] ─► Continue loop

END
```

---

## 3. KAFKA → MONGODB PROCESSING FLOW

```
START: Consume from real_estate_raw
│
├─► [Poll message (timeout: 1.0s)]
│   ├─ [Message available?]
│   │  ├─ No  ─► Continue loop
│   │  └─ Yes ─► Parse message
│   │
│   └─► [Parse JSON]
│        ├─ [Valid JSON?]
│        │  ├─ No  ─► Log error, skip (offset auto-commits)
│        │  └─ Yes ─► Normalize
│        │
│        └─► [Normalize]
│             ├─ [Parse numeric fields]
│             │  └─ [Valid numbers?]
│             │     ├─ No ─► Set to None
│             │     └─ Yes ─► Proceed
│             │
│             ├─ [Parse price]
│             │  ├─ [Multiple formats?]
│             │  │  ├─ "1.5 tỷ" ─► 1.5B VND
│             │  │  ├─ "150 triệu" ─► 150M VND
│             │  │  ├─ "100M/m²" ─► calc price_per_m2
│             │  │  └─ Invalid ─► Set None
│             │  │
│             │  └─ [Calculate price_per_m2]
│             │     └─ price_vnd / area_m2
│             │
│             ├─ [Score feature coverage]
│             │  └─ Count populated key fields (0-10)
│             │
│             └─ [Mark as model candidate?]
│                ├─ Conditions:
│                │  ✓ has_target_price (price_vnd > 0)
│                │  ✓ area_m2 > 0
│                │  ✓ property_type exists
│                │  ✓ coverage_score >= 5
│                │
│                ├─ All met ─► is_model_candidate = True
│                └─ Any fail ─► is_model_candidate = False
│
├─► [Store to MongoDB]
│   ├─ [URL unique index?]
│   │  ├─ Yes (exists) ─► UPDATE record
│   │  └─ No (new) ──► INSERT record
│   │
│   └─ [Write error?]
│       ├─ Yes ─► Log error, don't commit offset, RETRY
│       └─ No  ─► Publish to real_estate_features topic
│
├─► [Publish normalized to Kafka]
│   ├─ [Error?]
│   │  ├─ Yes ─► Log warning (best effort)
│   │  └─ No  ─► Continue
│   │
│   └─ [Auto-commit offset]
│       └─ Message processed
│
└─► Loop back to consume next

END
```

---

## 4. MODEL TRAINING FLOW

```
START: Daily Model Retraining (2 AM UTC)
│
├─► [Check prerequisites]
│   ├─ [Load training data]
│   │  ├─ Source: MongoDB training_features
│   │  ├─ Query: is_model_candidate = True, price_vnd > 0
│   │  └─ Filter: Data freshness <= 30 days
│   │
│   └─ [Check sample count]
│       ├─ [Samples >= 200?]
│       │  ├─ Yes ─► Proceed
│       │  └─ No  ─► SKIP training, use previous model
│       │
│       └─ [Log: "Training data ready"]
│           └─ Samples: X, coverage: Y%, freshness: Z days
│
├─► [Feature engineering]
│   ├─ [Numeric features (6)]
│   │  └─ Impute missing (median)
│   │
│   ├─ [Categorical features (8)]
│   │  └─ OneHotEncode + impute (most_frequent)
│   │
│   └─ [Text features (1)]
│       ├─ Flatten & clean description
│       └─ TF-IDF vectorize (max 3000 features, ngrams 1-2)
│
├─► [Split train/test]
│   ├─ Train: 80% (X_train, y_train)
│   └─ Test: 20% (X_test, y_test)
│
├─► [Build ensemble model]
│   ├─ Ridge (alpha=3.0)
│   ├─ HistGradientBoosting (depth=8, lr=0.06, iter=250)
│   └─ VotingRegressor (weight equally)
│
├─► [Transform target]
│   ├─ Apply log transformation: log1p(price_vnd)
│   └─ Fit on log scale
│
├─► [Train model]
│   └─ model.fit(X_train, log1p(y_train))
│
├─► [Evaluate]
│   ├─ [Predict on test set]
│   │  └─ y_pred = model.predict(X_test)
│   │
│   ├─ [Calculate metrics]
│   │  ├─ R² = r2_score(y_test, y_pred)
│   │  ├─ MAE = mean_absolute_error(y_test, y_pred)
│   │  └─ RMSE = sqrt(mean_squared_error(...))
│   │
│   └─ [Check quality gates]
│       ├─ [R² >= 0.75?]
│       │  └─ No ─► ROLLBACK to previous model
│       │
│       ├─ [MAE < 500M?]
│       │  └─ No ─► ROLLBACK to previous model
│       │
│       ├─ [Better than previous?]
│       │  └─ No ─► KEEP previous model
│       │
│       └─ [All pass?]
│           └─ Yes ─► Proceed to save
│
├─► [Save artifacts]
│   ├─ Model: price_model_v{N}_{date}_{status}.joblib
│   ├─ Metrics: price_model_metrics_{date}.json
│   │  └─ {R², MAE, RMSE, train_size, test_size, ...}
│   │
│   └─ [Backup previous version]
│       └─ Keep 3 versions for rollback
│
└─► [Notify]
    ├─ Slack: "Model trained successfully"
    │  └─ R²: 0.78, MAE: 420M, samples: 5,534
    │
    └─ Log metrics for monitoring

END
```

---

## 5. PREDICTION FLOW

```
START: Get Price Prediction
│
├─► [Input: Feature JSON]
│   └─ Example: {area_m2: 100, bedroom_count: 3, ...}
│
├─► [Validate input]
│   ├─ [All required fields present?]
│   │  ├─ No ─► Return error 1004: Missing field X
│   │  └─ Yes ─► Continue
│   │
│   ├─ [Data types correct?]
│   │  ├─ No ─► Return error 1002: Invalid type for field X
│   │  └─ Yes ─► Continue
│   │
│   ├─ [Categorical values valid?]
│   │  ├─ No ─► Return error 1004: Invalid value for field X
│   │  └─ Yes ─► Continue
│   │
│   └─ [Numeric ranges reasonable?]
│       ├─ [area_m2 in (0, 10000]?]
│       ├─ [bedroom in [0, 20]?]
│       └─ [etc.]
│           └─ Out of range ─► Log warning, clamp or reject
│
├─► [Load model]
│   ├─ [Model cached in memory?]
│   │  ├─ Yes ─► Use cached version
│   │  └─ No  ─► Load from disk
│   │
│   ├─ [Load error?]
│   │  ├─ Yes ─► Return error 1101: Model not found
│   │  └─ No  ─► Continue
│   │
│   └─ [Check model version]
│       └─ Log: Model v3, trained 2024-04-28
│
├─► [Preprocess input]
│   ├─ Apply same transformations as training:
│   │  ├─ Numeric: Scale with StandardScaler
│   │  ├─ Categorical: OneHotEncode
│   │  └─ Text: TF-IDF vectorize
│   │
│   └─ Handle missing values (impute same as training)
│
├─► [Predict]
│   ├─ y_log_pred = model.predict(X_preprocessed)
│   ├─ y_pred = expm1(y_log_pred)  # Inverse transform
│   │
│   ├─ [Prediction valid?]
│   │  ├─ [NaN or Inf?]
│   │  │  ├─ Yes ─► Log error 1102, return fallback
│   │  │  └─ No  ─► Continue
│   │  │
│   │  └─ [In reasonable range (10M - 500B VND)?]
│   │     ├─ No (too low/high) ─► Flag as anomaly, log
│   │     └─ Yes ─► Continue
│   │
│   └─ Round to nearest 1M VND
│
├─► [Calculate confidence]
│   ├─ Based on:
│   │  ├─ Feature coverage score
│   │  ├─ Distance to training data center
│   │  ├─ Ensemble agreement (Ridge vs HGB)
│   │  └─ Expected RMSE ±10-20%
│   │
│   └─ confidence_interval = [pred * 0.85, pred * 1.15]
│
├─► [Format response]
│   └─ {
│        "predicted_price_vnd": 1_350_000_000,
│        "predicted_price_billion_vnd": 1.35,
│        "confidence_lower": 1_147_500_000,
│        "confidence_upper": 1_552_500_000,
│        "model_version": "v3",
│        "model_trained_date": "2024-04-28",
│        "prediction_timestamp": "2024-04-29T10:30:00Z"
│      }
│
└─► [Log prediction]
    ├─ input_features: {...}
    ├─ output_prediction: 1.35B
    ├─ latency_ms: 123
    └─ status: success

END
```

---

## 6. ERROR HANDLING FLOW

```
┌─────────────────────────────────────┐
│        ERROR DETECTION              │
├─────────────────────────────────────┤
│                                     │
│ 1. Network Error (001-099)          │
│    ├─ Retry with exponential backoff│
│    ├─ Max 4 retries (2,4,8,16 sec) │
│    └─ On failure: DLQ or skip       │
│                                     │
│ 2. Parsing Error (101-199)          │
│    ├─ Log detailed error            │
│    ├─ Store raw HTML/JSON for debug │
│    └─ Continue processing           │
│                                     │
│ 3. Kafka Error (301-499)            │
│    ├─ Retry with exponential backoff│
│    ├─ Send to DLQ on failure        │
│    └─ Alert if DLQ growing          │
│                                     │
│ 4. Database Error (501-799)         │
│    ├─ Connection: Retry with backoff│
│    ├─ Write: Upsert or DLQ          │
│    └─ CRITICAL: Alert ops team      │
│                                     │
│ 5. Model Error (801-999)            │
│    ├─ Insufficient data: Skip       │
│    ├─ Failed training: Rollback     │
│    └─ Bad metrics: Keep previous    │
│                                     │
│ 6. Prediction Error (1001-1199)     │
│    ├─ Validation: Return 400 error  │
│    ├─ Model error: Return 500 error │
│    └─ Log for investigation         │
│                                     │
└─────────────────────────────────────┘
        │
        ▼
    ┌─────────────────┐
    │  ALERTING       │
    ├─────────────────┤
    │ CRITICAL:       │
    │ • Page on-call  │
    │ • 5 min SLA     │
    │                 │
    │ HIGH:           │
    │ • Email + Slack │
    │ • 30 min SLA    │
    │                 │
    │ MEDIUM:         │
    │ • Slack only    │
    │ • 1 hour SLA    │
    └─────────────────┘
        │
        ▼
    ┌─────────────────┐
    │  RECOVERY       │
    ├─────────────────┤
    │ • Auto-retry    │
    │ • Fallback mode │
    │ • Rollback      │
    │ • Manual review │
    └─────────────────┘
```

---

## 7. DATA QUALITY GATES

```
┌──────────────────────────────────────────────┐
│  DATA PIPELINE WITH QUALITY GATES            │
├──────────────────────────────────────────────┤
│                                              │
│  Scraper Output                              │
│      │                                       │
│      ▼ GATE 1: Scraper Health                │
│   [Check: volume > 200/hr, errors < 10%]    │
│   [Action: WARN if failed, continue]        │
│      │                                       │
│      ▼ Kafka (real_estate_raw)               │
│      │                                       │
│      ▼ Processor (normalize)                 │
│      │                                       │
│      ▼ GATE 2: Normalization Quality         │
│   [Check: candidates > 50%, duplicates = 0] │
│   [Action: ALERT if failed]                 │
│      │                                       │
│      ▼ MongoDB (training_features)           │
│      │                                       │
│      ▼ GATE 3: Pre-Training Data             │
│   [Check: samples >= 200, data <= 30 days]  │
│   [Action: SKIP training if failed]         │
│      │                                       │
│      ▼ Model Training                        │
│      │                                       │
│      ▼ GATE 4: Model Performance             │
│   [Check: R² >= 0.75, MAE < 500M]           │
│   [Action: ROLLBACK if failed]              │
│      │                                       │
│      ▼ GATE 5: Pre-Deployment                │
│   [Check: Tests pass, metrics OK]           │
│   [Action: Block deployment if failed]      │
│      │                                       │
│      ▼ Model Server (Serving)                │
│      │                                       │
│      ▼ GATE 6: Prediction Validation         │
│   [Check: Input valid, prediction in range] │
│   [Action: Return error if failed]          │
│      │                                       │
│      ▼ Prediction Output                     │
│                                              │
└──────────────────────────────────────────────┘
```
