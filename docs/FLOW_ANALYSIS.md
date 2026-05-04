# Flow Analysis & Recommendations

## 1. CURRENT FLOW ASSESSMENT

✅ **What's Good:**
- Multi-stage architecture is sound
- Checkpoint/resume mechanism for scraper
- Proper normalization pipeline
- Ensemble model approach
- Feature engineering with text

❌ **What Needs Fixing:**

### Critical Issues (Must Fix Before Production)

#### 1.1 **Kafka Consumer Infinite Loop** ⚠️ CRITICAL
**Current Code:**
```python
def consume_forever(self, max_messages: int | None = None) -> None:
    count = 0
    try:
        while True:  # ❌ Infinite loop!
            message = self.consumer.poll(1.0)
            # ...
    finally:
        self.close()
```

**Problems:**
- No graceful shutdown mechanism
- Can't stop cleanly without kill -9
- No offset management strategy
- Rebalancing not handled properly
- Message processing timeout not configured

**Fix Required:**
```python
# ✅ Add signal handlers for graceful shutdown
import signal

shutdown_requested = False

def handle_shutdown(signum, frame):
    global shutdown_requested
    shutdown_requested = True
    logger.info("Shutdown signal received")

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

def consume_forever(self, max_messages: int | None = None) -> None:
    count = 0
    try:
        while not shutdown_requested:
            message = self.consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                logger.error("Consumer error: %s", message.error())
                continue
            
            # ... process message ...
            
            count += 1
            if max_messages and count >= max_messages:
                break
    finally:
        self.close()
```

---

#### 1.2 **No Idempotency Check for Kafka Duplicates** ⚠️ CRITICAL
**Current Problem:**
- Scraper publishes with URL as key (good)
- But no way to detect if same message published twice
- If scraper crashes and state file is corrupted, might re-publish old records
- Kafka consumer relies on MongoDB unique index only

**Fix Required:**
```python
# ✅ Add message tracking
class KafkaToMongoPipeline:
    def __init__(self, ...):
        # ... existing code ...
        self.processed_offsets = {}  # Track processed messages
    
    def process_payload(self, payload, message_offset):
        # Check if already processed
        url = payload.get("url")
        if url in self.processed_offsets:
            if self.processed_offsets[url] == message_offset:
                logger.warning(f"Duplicate message detected: {url}")
                return None  # Skip duplicate
        
        # Process new message
        normalized = normalize_listing(payload)
        self.raw_collection.update_one(...)
        self.feature_collection.update_one(...)
        self.processed_offsets[url] = message_offset
        
        return normalized
```

---

#### 1.3 **No Data Validation Before Storing** ⚠️ HIGH
**Current Problem:**
- Normalized data stored directly to MongoDB without validation
- No schema validation
- Invalid values (negative prices, zero area) not caught
- Garbage data contaminates training dataset

**Fix Required:**
```python
# ✅ Add validation before storage
def validate_normalized_record(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors = []
    
    # Required fields
    if not record.get("url"):
        errors.append("Missing required field: url")
    
    # Price validation
    if record.get("price_vnd"):
        if record["price_vnd"] <= 0:
            errors.append("Price must be > 0")
        if record["price_vnd"] > 500_000_000_000:
            errors.append("Price > 500B (likely invalid)")
    
    # Area validation
    if record.get("area_m2"):
        if record["area_m2"] <= 0:
            errors.append("Area must be > 0")
        if record["area_m2"] > 10_000:
            errors.append("Area > 10K m² (likely invalid)")
    
    # Derived field consistency
    if record.get("price_vnd") and record.get("area_m2"):
        expected_price_per_m2 = record["price_vnd"] / record["area_m2"]
        actual_price_per_m2 = record.get("price_per_m2_vnd")
        if actual_price_per_m2 and abs(expected_price_per_m2 - actual_price_per_m2) > expected_price_per_m2 * 0.01:
            errors.append("Price per m² inconsistent")
    
    return len(errors) == 0, errors

# Use in process_payload:
is_valid, errors = validate_normalized_record(normalized)
if not is_valid:
    logger.warning(f"Validation failed for {normalized['url']}: {errors}")
    normalized["validation_errors"] = errors
    # Store to separate invalid_records collection for review
    invalid_collection.insert_one(normalized)
else:
    feature_collection.update_one(...)
```

---

#### 1.4 **Dead Topic (real_estate_features)** ⚠️ MEDIUM
**Current Problem:**
- Processor publishes to `real_estate_features` topic
- NO consumer subscribes to it
- Data is lost after 30 days (retention)
- Purpose unclear

**Fix Required:**
```
Option A: Remove the dead topic
  - Delete the produce call in processor
  - Update monitoring dashboard

Option B: Add consumer for analytics
  - Create stats/analytics consumer
  - Aggregate metrics by property_type, province
  - Store analytics results in MongoDB

Option C: Keep for audit trail
  - Clarify in code: "Audit trail only"
  - Add separate archival consumer
  - Store to data lake (Parquet)
```

**Recommended: Option A** (remove if not used)

---

#### 1.5 **No Model Versioning System** ⚠️ HIGH
**Current Problem:**
- Model saved as `artifacts/price_model.joblib` (overwritten each time)
- Metrics saved as `artifacts/price_model_metrics.json` (overwritten)
- No rollback capability
- Can't compare model versions
- Can't investigate which model produced bad prediction

**Fix Required:**
```python
# ✅ Implement versioning
import datetime

def train(self, records, model_path, metrics_path=None):
    # ... training code ...
    
    timestamp = datetime.datetime.now().isoformat()
    version = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save versioned model
    versioned_model_path = Path("artifacts") / f"models" / f"price_model_v{version}.joblib"
    versioned_model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(self.model, versioned_model_path)
    
    # Save metadata
    metadata = {
        "version": version,
        "timestamp": timestamp,
        "training_date": datetime.date.today().isoformat(),
        "sample_count": len(frame),
        "metrics": metrics,
        "features": NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TEXT_FEATURE],
        "status": "active"  # or staging, archive, rollback
    }
    
    metadata_path = versioned_model_path.parent / f"metadata_v{version}.json"
    metadata_path.write_text(json.dumps(metadata, indent=2))
    
    # Update current symlink
    current_link = Path("artifacts") / "models" / "price_model_current.joblib"
    if current_link.exists():
        current_link.unlink()
    current_link.symlink_to(versioned_model_path)
    
    # Keep last 3 models for rollback
    all_models = sorted(versioned_model_path.parent.glob("price_model_v*.joblib"), reverse=True)
    for old_model in all_models[3:]:
        old_model.unlink()
    
    return TrainResult(model_path=str(versioned_model_path), ...)
```

---

#### 1.6 **No Recovery/Replay Strategy for Data Loss** ⚠️ HIGH
**Current Problem:**
- If MongoDB consumer crashes, messages already consumed won't be re-processed
- No replay capability for past data
- Only keeps Kafka messages for 7 days
- Can't re-normalize if parsing logic changes

**Fix Required:**
```python
# ✅ Add offset management
class KafkaToMongoPipeline:
    def __init__(self, ...):
        self.consumer = Consumer({
            "bootstrap.servers": kafka_bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": "earliest",  # Start from beginning if offset lost
            "enable.auto.commit": False,  # Manual commit for control
            "max.poll.interval.ms": 900000,  # 15 min timeout
        })
    
    def consume_forever(self, ...):
        for message in consumer:
            try:
                payload = json.loads(message.value().decode("utf-8"))
                normalized = self.process_payload(payload)
                
                # Only commit after successful processing
                self.consumer.commit(asynchronous=False)
                
                # Save checkpoint to MongoDB for recovery
                self.db.consumer_checkpoints.update_one(
                    {"_id": "real_estate_raw_processor"},
                    {"$set": {
                        "last_offset": message.offset(),
                        "last_partition": message.partition(),
                        "last_timestamp": datetime.now(timezone.utc).isoformat(),
                        "processed_count": ...
                    }},
                    upsert=True
                )
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                # Don't commit on error - will retry on restart
                raise
```

---

#### 1.7 **No Feature Store / Data Catalog** ⚠️ MEDIUM
**Current Problem:**
- No documentation of what each field means
- No tracking of field definitions across versions
- Data scientist doesn't know what transformations happened
- Hard to debug if output schema changed

**Fix Required:**
Create [DATA_CATALOG.md](docs/DATA_CATALOG.md):
```markdown
# Data Catalog

## Dataset: training_features
Owner: Data Engineering
Location: MongoDB > real_estate_db > training_features

### Fields:
| Field | Type | Definition | Source | Transformation |
|-------|------|-----------|--------|-----------------|
| url | string | Property listing URL | scraped directly | None |
| area_m2 | float | Property area in square meters | parsed from area_text | parse_number() |
| price_vnd | float | Total property price in VND | parsed from price_text | parse_price_to_vnd() |
| ...

### Indexing:
- url (unique): Fast lookup by property
- price_vnd: Filtering for data quality
```

---

#### 1.8 **No SLA / Response Time Tracking** ⚠️ MEDIUM
**Current Problem:**
- No latency metrics tracked
- Don't know if prediction is slow
- Can't detect degradation
- No capacity planning data

**Fix Required:**
```python
import time

# ✅ Add timing instrumentation
def predict(self, record):
    start_time = time.time()
    
    try:
        frame = build_feature_frame([record])
        X = frame[...] 
        price_vnd = float(self.model.predict(X)[0])
        
        latency_ms = (time.time() - start_time) * 1000
        logger.info("prediction_success", extra={
            "latency_ms": latency_ms,
            "price_vnd": price_vnd
        })
        
        # Alert if slow
        if latency_ms > 500:
            logger.warning(f"Slow prediction: {latency_ms}ms")
        
        return {
            "predicted_price_vnd": price_vnd,
            "predicted_price_billion_vnd": round(price_vnd / 1_000_000_000, 4),
            "latency_ms": latency_ms
        }
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.error(f"prediction_error: {e}", extra={"latency_ms": latency_ms})
        raise
```

---

## 2. PRIORITY FIXES FOR BA ROLE

**Tier 1 - Must Do First (Foundation):**
1. ✅ **Create REQUIREMENTS.md** - Define what system should do
2. ✅ **Create DATA_SCHEMA.md** - Define data formats & validation
3. ✅ **Create METRICS_AND_SLA.md** - Define success criteria
4. ✅ **Create ACCEPTANCE_CRITERIA.md** - Define quality gates
5. ⚠️ **Fix infinite loop** - Add graceful shutdown to Kafka consumer
6. ⚠️ **Add data validation** - Validate records before storing

**Tier 2 - Should Do (Quality Assurance):**
7. ✅ **Create ERROR_HANDLING_STRATEGY.md** - Define error codes & recovery
8. ✅ **Create MONITORING_AND_ALERTING.md** - Define what to monitor & alerts
9. ⚠️ **Implement versioning** - Version models, datasets
10. ⚠️ **Add recovery strategy** - Implement replay capability
11. ⚠️ **Remove dead topic** - Or add consumer for real_estate_features

**Tier 3 - Nice to Have (Scalability):**
12. ⚠️ **Add feature store** - Create data catalog
13. ⚠️ **Add latency tracking** - Instrument all stages
14. ⚠️ **Implement idempotency checks** - Better duplicate detection

---

## 3. RECOMMENDED ACTIONS FOR NEXT SPRINT

```
SPRINT 1 (This week):
✅ Create all BA documentation (done by you)
🔧 Implement graceful shutdown in Kafka consumer
🔧 Add data validation before MongoDB write
🔧 Add structured logging (JSON format)

SPRINT 2 (Next week):
🔧 Implement model versioning
🔧 Add offset management & recovery
🔧 Create monitoring dashboard

SPRINT 3:
🔧 Add unit tests (70% coverage)
🔧 Dockerize all services
🔧 Setup CI/CD pipeline
```

---

## 4. FLOW DIAGRAM SUMMARY

Current flow is **80% correct**, but needs:

```
SCRAPER → Kafka ✓ (good)
            ↓ (❌ needs: idempotency, validation)
PROCESSOR → MongoDB ✓ (mostly good)
            ↓ (❌ needs: versioning, recovery)
TRAINING → Model ✓ (good)
            ↓ (❌ needs: versioning, metrics tracking)
PREDICTION ✓ (good for CLI)
            ↓ (⚠️ needs: API, batch processing)
END
```

**Key Gap:** No monitoring, alerting, or recovery strategy for production.
