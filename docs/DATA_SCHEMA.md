# Data Schema & Validation Rules

## 1. KAFKA TOPIC SCHEMAS

### Topic: `real_estate_raw` (Input from Scraper)
**Purpose**: Raw scraped listings  
**Retention**: 7 days  
**Partitions**: 3 (by URL hash)  
**Replication Factor**: 1

```json
{
  "url": "https://batdongsan.com.vn/....",
  "listing_id": "123456789",
  "title": "Nhà đẹp tại Hà Nội",
  "price_text": "1.5 tỷ",
  "area_text": "100 m²",
  "bedroom_text": "3",
  "bathroom_text": "2",
  "floor_text": "4",
  "front_width_text": "5m",
  "road_width_text": "10m",
  "legal_text": "Sổ đỏ",
  "direction_text": "Hướng Đông",
  "property_type": "apartment",
  "listing_type": "Bán",
  "province_slug": "ha-noi",
  "district_slug": "dong-da",
  "ward_slug": "bach-khoa",
  "furniture_text": "Có nội thất",
  "project_hint": "Project ABC",
  "description": "Mô tả chi tiết...",
  "posted_date_text": "10 ngày trước",
  "verified": 1,
  "source": "batdongsan",
  "location_slug": "...-tai-ha-noi",
  "scraped_at": "2024-04-29T10:30:00Z"
}
```

**Validation Rules**:
| Field | Type | Required | Validation |
|-------|------|----------|-----------|
| url | string | ✅ YES | Must start with https://batdongsan.com.vn |
| listing_id | string | ✅ YES | Alphanumeric, unique |
| title | string | ❌ NO | Max 500 chars |
| price_text | string | ❌ NO | Contains digit + unit (tỷ/triệu) |
| area_text | string | ❌ NO | Contains digit + "m²" |
| property_type | string | ✅ YES | One of: house, apartment, land, villa_townhouse, shophouse, warehouse |
| province_slug | string | ✅ YES | Valid province code |
| verified | int | ❌ NO | 0 or 1 |
| scraped_at | string | ✅ YES | ISO 8601 format |

---

### Topic: `real_estate_features` (Output from Processor)
**Purpose**: Normalized listings ready for modeling  
**Retention**: 30 days  
**Partitions**: 3  
**Replication Factor**: 1

```json
{
  "url": "https://...",
  "listing_id": "123456789",
  "title": "Nhà đẹp tại Hà Nội",
  "property_type": "apartment",
  "listing_type": "Bán",
  "province_slug": "ha-noi",
  "district_slug": "dong-da",
  "ward_slug": "bach-khoa",
  "location_slug": "...",
  "direction": "Hướng Đông",
  "legal": "Sổ đỏ",
  "furniture": "Có nội thất",
  "project_hint": "Project ABC",
  "description": "...",
  "verified": 1,
  "posted_date_text": "10 ngày trước",
  "raw_price_text": "1.5 tỷ",
  "raw_area_text": "100 m²",
  "raw_bedroom_text": "3",
  "raw_bathroom_text": "2",
  "raw_floor_text": "4",
  "raw_front_width_text": "5m",
  "raw_road_width_text": "10m",
  "area_m2": 100.0,
  "bedroom_count": 3,
  "bathroom_count": 2,
  "floor_count": 4,
  "front_width_m": 5.0,
  "road_width_m": 10.0,
  "price_vnd": 1500000000,
  "price_per_m2_vnd": 15000000,
  "has_target_price": true,
  "text_features": "Nhà đẹp | apartment | ha-noi | ...",
  "source": "batdongsan_verified",
  "scraped_at": "2024-04-29T10:30:00Z",
  "updated_at": "2024-04-29T10:35:00Z",
  "feature_coverage_score": 9,
  "is_model_candidate": true
}
```

---

## 2. MONGODB COLLECTIONS

### Collection: `listings_raw`
**Database**: real_estate_db  
**Purpose**: Archive of all raw scraped data  
**TTL**: 90 days  
**Indexes**: 
- `url` (unique)
- `scraped_at` (for retention)

```
Document structure: Same as real_estate_raw Kafka topic
```

### Collection: `training_features`
**Database**: real_estate_db  
**Purpose**: Normalized data for model training  
**TTL**: 365 days  
**Indexes**:
- `url` (unique)
- `price_vnd`
- `price_per_m2_vnd`
- `property_type`
- `province_slug`
- `district_slug`
- `is_model_candidate`
- `feature_coverage_score`
- `updated_at` (for time-based queries)

```
Document structure: Same as real_estate_features Kafka topic
```

---

## 3. MODEL INPUT FEATURES

### Numeric Features (6)
| Feature | Type | Range | Unit | Validation |
|---------|------|-------|------|-----------|
| area_m2 | float | 5 - 10,000 | m² | Must be > 0 |
| bedroom_count | int | 0 - 10 | count | >= 0 |
| bathroom_count | int | 0 - 10 | count | >= 0 |
| floor_count | int | 0 - 50 | count | >= 0 |
| front_width_m | float | 0 - 100 | meter | >= 0 |
| road_width_m | float | 0 - 100 | meter | >= 0 |

### Categorical Features (8)
| Feature | Type | Valid Values | Notes |
|---------|------|-------|-------|
| property_type | string | house, apartment, land, villa_townhouse, shophouse, warehouse | Required |
| direction | string | hướng_đông, hướng_tây, ... | 9 directions total |
| legal | string | sổ_đỏ, sổ_hồng, ... | 5 types |
| listing_type | string | Bán, Cho thuê | 2 types |
| province_slug | string | ha-noi, ho-chi-minh, ... | 63 provinces |
| district_slug | string | dong-da, thanh-xuan, ... | Variable per province |
| ward_slug | string | bach-khoa, ngoc-khanh, ... | Variable per district |
| project_hint | string | Project name | Max 200 chars, optional |

### Text Feature (1)
| Feature | Type | Example |
|---------|------|---------|
| text_features | string | "Nhà đẹp \| apartment \| ha-noi \| ... " (pipe-separated) |

### Target Variable
| Feature | Type | Range | Unit |
|---------|------|-------|------|
| price_vnd | float | 100M - 100B | Vietnamese Đồng |

---

## 4. MODEL CANDIDATE CRITERIA

A listing is marked as `is_model_candidate = true` if:
1. ✅ `has_target_price` = true (price_vnd > 0)
2. ✅ `area_m2` > 0
3. ✅ `property_type` is not null
4. ✅ `feature_coverage_score` >= 5 (at least 5 out of 10 key fields populated)

**Current distribution** (from quality reports):
- ~60% of scraped listings are candidates
- ~25% lack price
- ~15% lack area or property type

---

## 5. DATA QUALITY RULES

### Outlier Detection
| Rule | Threshold | Action |
|------|-----------|--------|
| Price too low | < 100M VND | Flag as outlier, exclude from training |
| Price too high | > 500B VND | Flag as outlier, exclude from training |
| Area too small | < 5 m² | Flag as outlier, exclude |
| Area too large | > 10,000 m² | Flag as outlier, exclude |
| Price per m² too low | < 1M VND/m² | Flag as outlier |
| Price per m² too high | > 1B VND/m² | Flag as outlier |

### Missing Data Rules
| Field | Missing Rate | Action |
|-------|-------|--------|
| price_vnd | > 30% | ALERT, may affect model |
| area_m2 | > 30% | ALERT, may affect model |
| property_type | > 10% | ALERT, training data invalid |
| description | > 50% | OK (text features optional) |

### Freshness Rules
| Check | Threshold | Alert Level |
|-------|-----------|-------------|
| Data age | > 30 days | WARNING |
| Last update | > 24 hours | WARNING |
| Model age | > 7 days | CRITICAL |
| Training data freshness | > 30 days | CRITICAL |

---

## 6. DATA LINEAGE

```
batdongsan.com.vn
    ↓ [Scraper + Kafka]
real_estate_raw (Kafka topic + MongoDB collection)
    ↓ [Processor + Kafka → MongoDB]
training_features (Kafka topic + MongoDB collection)
    ↓ [Model Training]
price_model.joblib + metrics.json
    ↓ [Serving]
Predictions (CLI + API)
```

---

## 7. VERSIONING STRATEGY

### Data Versioning
```
Version Format: YYYYMMDD_HHmmss_commit_hash

Example:
- listings_raw_v20240429_103000_abc123f.parquet
- training_features_v20240429_153000_def456g.parquet
```

### Model Versioning
```
Naming: price_model_v{version}_{date}_{status}.joblib

Example:
- price_model_v1_20240429_production.joblib
- price_model_v2_20240430_staging.joblib
- price_model_v2_20240430_dev.joblib
```

---

## 8. ENCODING STANDARDS

- **Text Encoding**: UTF-8 (for Vietnamese characters)
- **Date Format**: ISO 8601 (YYYY-MM-DDTHH:mm:ssZ)
- **Number Format**: Float64 for prices, Int32 for counts
- **String Case**: snake_case for field names, lowercase for enum values
