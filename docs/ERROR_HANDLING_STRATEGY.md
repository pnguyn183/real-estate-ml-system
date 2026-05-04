# Error Handling & Recovery Strategy

## 1. ERROR CLASSIFICATION

### 1.1 By Severity
```
Level 1 (CRITICAL): System shutdown required, data loss risk
Level 2 (HIGH): Feature unavailable, degraded service
Level 3 (MEDIUM): Partial functionality impact
Level 4 (LOW): User-facing warnings, auto-retry
```

### 1.2 By Recovery Time
```
Immediate (< 1 sec): Handled silently with retry
Quick (1-60 sec): Logged, automatic recovery attempted
Escalation (> 60 sec): Human intervention required
```

---

## 2. SCRAPER ERROR HANDLING

### 2.1 Network Errors

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Connection timeout | 001 | Website unreachable | L2 | Retry with exp backoff (2,4,8 sec) |
| DNS resolution | 002 | Invalid hostname | L2 | Skip URL, log, continue |
| SSL certificate error | 003 | HTTPS validation fail | L2 | Retry 3x, then skip |
| Rate limit (429) | 004 | Too many requests | L2 | Wait 60s, resume from checkpoint |
| 5xx Server error | 005 | Website error | L2 | Retry up to 4x with backoff |
| 404 Not found | 006 | URL deleted | L3 | Skip record, mark as removed |

**Recovery Strategy:**
```python
max_retries = 4
retry_delays = [2, 4, 8, 16]  # seconds

for attempt in range(max_retries):
    try:
        return fetch_html(url)
    except (TimeoutError, ConnectionError):
        if attempt < max_retries - 1:
            time.sleep(retry_delays[attempt])
        else:
            log_error("fetch_failed", url, attempt)
            raise
```

### 2.2 Parsing Errors

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| HTML parsing fail | 101 | Invalid HTML structure | L3 | Log error, skip record, log URL |
| Missing required field | 102 | Website structure changed | L3 | Mark record incomplete, store raw |
| Invalid scraped value | 103 | Unexpected data format | L4 | Use default/null, flag in coverage_score |
| Price parsing fail | 104 | New price format | L3 | Store raw, zero normalized price |

**Recovery Strategy:**
```python
try:
    record = parse_listing_detail(url)
except ParsingError as e:
    logger.error(f"parse_error: {e}, url={url}")
    # Option 1: Store raw HTML for manual review
    # Option 2: Mark record with is_parse_error=True
    # Option 3: Retry with different parser
    pass
```

### 2.3 State File Issues

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| State file corruption | 201 | Disk error, partial write | L2 | Rollback to 1000 URLs, start fresh |
| State file missing | 202 | Manual deletion | L2 | Start from --start-page, warn user |
| Permissions denied | 203 | File system error | L1 | CRITICAL: Cannot resume, shutdown |

**Recovery Strategy:**
```python
def load_state_safe(path):
    try:
        return json.load(path)
    except (json.JSONDecodeError, FileNotFoundError):
        logger.warning("State file invalid, starting fresh")
        return {"next_page": 1, "emitted_count": 0, "seen_urls": []}
```

---

## 3. KAFKA ERRORS

### 3.1 Producer Errors (Scraper → Kafka)

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Broker unreachable | 301 | Kafka down | L2 | Queue in memory, retry every 10s |
| Message too large | 302 | Payload > 1MB | L3 | Split message, log error |
| Serialization error | 303 | Invalid JSON | L2 | Log, fix, republish |
| Timeout | 304 | Broker slow | L2 | Increase timeout, retry |

**Recovery Strategy:**
```python
def delivery_report(err, msg):
    if err is not None:
        logger.error(f"Kafka delivery error: {err}")
        # Re-queue message or dead-letter topic
        send_to_dlq(msg)
    else:
        logger.info(f"Message produced: {msg.key()}")

producer = Producer({
    'bootstrap.servers': 'localhost:9092',
    'queue.buffering.max.messages': 10000,
    'delivery.timeout.ms': 60000,
    'retry.backoff.ms': 100
})
```

### 3.2 Consumer Errors (Kafka → MongoDB)

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Broker down | 401 | Kafka unavailable | L1 | Pause processing, wait for recovery |
| Consumer rebalance | 402 | Consumer group change | L3 | Pause, rebalance, resume from offset |
| Offset out of range | 403 | Data deleted | L2 | Reset to earliest, reprocess |
| Deserialization fail | 404 | Invalid message format | L3 | Skip message, log key, continue |
| Message processing timeout | 405 | Processing slow | L2 | Extend max.poll.interval.ms |

**Recovery Strategy:**
```python
max_poll_interval_ms = 900000  # 15 minutes
session_timeout_ms = 30000
auto_offset_reset = "earliest"  # Start from beginning if offset lost

consumer.subscribe([topic])
while True:
    try:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue
        process_message(msg)
    except Exception as e:
        logger.error(f"Consumer error: {e}")
        # Do not commit offset, message will be retried
```

---

## 4. DATABASE ERRORS

### 4.1 MongoDB Connection

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Connection refused | 501 | MongoDB down | L1 | Retry with exponential backoff |
| Authentication fail | 502 | Invalid credentials | L1 | Check env vars, shutdown |
| Connection timeout | 503 | Network issue | L2 | Increase timeout, retry |
| Resource exhausted | 504 | Too many connections | L2 | Close idle connections, retry |

**Recovery Strategy:**
```python
def connect_mongo(uri, max_retries=5):
    for attempt in range(max_retries):
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            client.server_info()  # Verify connection
            return client
        except Exception as e:
            logger.error(f"MongoDB connection failed (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise
```

### 4.2 Write Operations

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Duplicate key | 601 | Same URL exists | L4 | Upsert (update if exists) |
| Write concern error | 602 | Replication timeout | L2 | Retry, increase timeout |
| Validation error | 603 | Schema mismatch | L3 | Log, skip record |
| Out of disk space | 604 | Storage full | L1 | CRITICAL: Shutdown, increase storage |

**Recovery Strategy:**
```python
def insert_or_update(collection, document):
    try:
        collection.update_one(
            {"url": document["url"]},
            {"$set": document},
            upsert=True
        )
    except pymongo.errors.DuplicateKeyError:
        # This shouldn't happen with upsert, but handle just in case
        logger.warning(f"Duplicate URL: {document['url']}")
```

### 4.3 Read Operations

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Query timeout | 701 | Slow query | L3 | Add index, increase timeout |
| Cursor exhausted | 702 | Iterator error | L3 | Restart query |
| Aggregation error | 703 | Pipeline error | L3 | Log, fix pipeline, retry |

---

## 5. MODEL TRAINING ERRORS

### 5.1 Data Errors

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Insufficient data | 801 | < 200 candidates | L3 | Wait for more data, reschedule |
| All values missing | 802 | Feature all NaN | L2 | Drop feature, retrain |
| Invalid target | 803 | Price <= 0 | L2 | Filter records, retrain |
| Class imbalance | 804 | Unbalanced data | L3 | Log warning, continue |

**Recovery Strategy:**
```python
def train_with_fallback(records):
    if len(records) < 200:
        logger.error("Insufficient training data")
        return use_previous_model()
    
    try:
        return train_model(records)
    except ValueError as e:
        logger.error(f"Training failed: {e}")
        return use_previous_model()
```

### 5.2 Training Errors

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Out of memory | 901 | Batch too large | L2 | Reduce batch size, use streaming |
| NaN in output | 902 | Invalid calculation | L2 | Add feature validation |
| Convergence fail | 903 | Model won't converge | L3 | Adjust hyperparameters, log |
| Save failure | 904 | Disk write error | L1 | Retry, alert ops team |

**Recovery Strategy:**
```python
def train_robust(records, model_path):
    try:
        model.fit(X_train, y_train)
        # Validate predictions
        predictions = model.predict(X_test)
        if np.any(np.isnan(predictions)):
            raise ValueError("NaN in predictions")
        joblib.dump(model, model_path)
    except MemoryError:
        return train_with_reduced_data(records)
    except Exception as e:
        logger.error(f"Training error: {e}")
        return use_previous_model()
```

---

## 6. PREDICTION ERRORS

### 6.1 Input Validation

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Missing required field | 1001 | Input incomplete | L4 | Return error with field name |
| Invalid data type | 1002 | Type mismatch | L4 | Return 400 error |
| Out of range value | 1003 | Extreme value | L4 | Clamp to range or reject |
| Invalid enum | 1004 | Unknown category | L4 | Return error, suggest valid values |

### 6.2 Prediction Errors

| Error | Code | Cause | Severity | Recovery |
|-------|------|-------|----------|----------|
| Model not found | 1101 | Model file missing | L1 | Fallback model or error |
| Prediction NaN | 1102 | Invalid calculation | L2 | Log, return fallback estimate |
| Feature mismatch | 1103 | Model expects different features | L2 | Retrain model |

---

## 7. ERROR CODES MAPPING

```json
{
  "001-099": "Scraper network errors",
  "101-199": "Scraper parsing errors",
  "201-299": "State management errors",
  "301-399": "Kafka producer errors",
  "401-499": "Kafka consumer errors",
  "501-599": "MongoDB connection errors",
  "601-699": "MongoDB write errors",
  "701-799": "MongoDB read errors",
  "801-899": "Model training data errors",
  "901-999": "Model training execution errors",
  "1001-1099": "Prediction input validation",
  "1101-1199": "Prediction execution errors"
}
```

---

## 8. ALERTING RULES

### Critical Alerts (Page on-call engineer)
```
- Kafka broker down (no messages for 5 min)
- MongoDB connection lost
- Model training failed for 2 consecutive days
- Data pipeline stuck (no updates for 1 hour)
- Prediction API error rate > 5%
```

### High Priority (Email + Slack)
```
- Error rate > 2%
- Data freshness > 6 hours
- Model accuracy drops > 10%
- Duplicate rate > 1%
```

### Medium Priority (Slack channel)
```
- Error rate > 0.5% but < 2%
- Scraper takes > 2x expected time
- Data quality score < 80%
```

---

## 9. POST-INCIDENT PROCEDURES

### 1. Detection & Response
```
1. Alert triggered → PagerDuty notification
2. On-call engineer acknowledges (5 min SLA)
3. Investigate root cause (15 min)
4. Apply immediate mitigation if possible
5. Implement permanent fix
```

### 2. Rollback Procedure
```
If training produces bad model (R² drops > 20%):
1. Detection: Automatic via metrics comparison
2. Alert: Trigger CRITICAL alert
3. Action: Automatically rollback to previous model
4. Communication: Slack notification to team
5. Investigation: Post-incident review
```

### 3. Communication Template
```
[INCIDENT] Real Estate Pipeline Error

Severity: HIGH | CRITICAL | MEDIUM
Component: Scraper | Pipeline | Model | Prediction
Error Code: XXX
Duration: [start] - [end] (X minutes)

Impact:
- X records affected
- Y% of predictions failed
- Z services impacted

Status: ONGOING | RESOLVED | INVESTIGATING

Action Items:
1. ...
2. ...

ETA: [time]

Updates: Will notify every 15 minutes
```

---

## 10. ERROR BUDGET

```
Monthly error budget: 99.5% availability = 21.6 minutes downtime

Allocation by component:
- Scraper: 8 minutes (37%)
- Pipeline: 8 minutes (37%)
- Model: 4 minutes (18%)
- Prediction API: 2 minutes (8%)

Track in dashboard:
- Used this month: X minutes
- Remaining: Y minutes
- Burn rate: Z min/day
```
