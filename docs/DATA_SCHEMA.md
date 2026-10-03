# Data schema and validation (implemented)

This is the schema implemented by `processing/kafka_to_mongo.py`, the `agents/`
modules, `modeling/price_model.py` and the scraper. Kafka topic provisioning is explicit
in `scripts/init_kafka_cluster.sh`; retention remains the broker default. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the full runtime flow.

## Kafka topics

New crawlers emit **listing schema version 2**, defined in
`processing/source_contract.py`. This is separate from version 1 of the AI
request/result *envelope*. The inner AI record retains its listing version.

Required identity: `source`, `source_listing_id`, `source_url`, `canonical_url`,
`url`, `schema_version`, timezone-aware `crawl_timestamp`, `raw_payload_hash`.
Original `price_raw`, `area_raw`, `address_raw` and extracted `raw_data` are
preserved. Numeric values use VND/m²; unknown values are null. Canonical fields
and source metadata survive Processor persistence and AI result handling.
See [full mapping and parsing rules](SOURCE_MIGRATION.md).

Malformed fields go to `invalid_records`. Safe partial records are excluded
with `training_excluded=true`; schema-v2 candidates must pass required-field,
sale, source and strict numeric checks. New invalid/pending versions invalidate
old training eligibility by URL. Historical unversioned records are retained;
there is no destructive database migration.

### `real_estate_raw`

Producer: `scraper/kafka_producer.py`. One UTF-8 JSON object per listing, keyed
by `url`. `kafka-init` creates/migrates this topic to three partitions with
replication factor three and minimum in-sync replicas two. Retention is broker
default.

Historical unversioned fields (still readable, no longer the crawler contract):

```json
{
  "url": "https://batdongsan.com.vn/...",
  "listing_id": "123456789",
  "title": "...",
  "price_text": "1.5 ty",
  "area_text": "100 m2",
  "bedroom_text": "3",
  "bathroom_text": "2",
  "floor_text": "4",
  "front_width_text": "5m",
  "road_width_text": "10m",
  "property_type": "apartment",
  "listing_type": "Ban",
  "province_slug": "ha-noi",
  "district_slug": "dong-da",
  "ward_slug": "bach-khoa",
  "direction_text": "...",
  "legal_text": "...",
  "furniture_text": "...",
  "project_hint": "...",
  "description": "...",
  "posted_date_text": "...",
  "verified": 1,
  "source": "batdongsan",
  "scraped_at": "2026-08-27T10:30:00+00:00"
}
```

The scraper may omit optional text fields. `url` is required for useful
processing; the processor stores the raw payload before validation.

### `real_estate_features`

Producer: processor, best-effort. There is no consumer in this repository. The
message is the normalized/audited record described below and is an extension or
audit feed, not the trainer input. The trainer reads MongoDB `training_features`.
The configured topic has three partitions and replication factor three
(minimum ISR two), with broker-default retention. Actual broker state must be
verified separately; see [PROJECT_STATUS.md](PROJECT_STATUS.md).

### Agent topics

All five additional topics are explicitly provisioned with three partitions,
replication factor three and `min.insync.replicas=2`; no retention override is
configured. They use the existing cluster, not another broker deployment.

| Topic | Producer | Consumer/group | Contract |
| --- | --- | --- | --- |
| `real_estate_stress_raw` | Stress agent | Same three Processors / `real_estate_training_pipeline` | Tagged raw synthetic JSON; key is a reserved synthetic URL |
| `real_estate_stress_features` | Processor stress branch | None | Best-effort normalized/invalid audit JSON; not training input |
| `real_estate_ai_input` | Processor fallback branch | AI worker / `real_estate_ai_extraction` | Request envelope below; key is original URL |
| `real_estate_ai_results` | AI worker | Same three Processors / `real_estate_training_pipeline` | Original envelope plus success/failure extraction result; key is URL |
| `real_estate_ai_dlq` | AI worker or Processor result handler | No automatic consumer | Failure envelope with `schema_version`, `event_id`, `error_code`, `failed_at`, `input`; malformed bytes are base64 |

The Processor group subscribes to raw, stress raw and AI results (nine
partitions total). The separate AI group consumes only AI input (three).

### AI request and result envelopes

Requests contain the untouched raw `record`, a source-version digest and an
explicit database origin. The following is illustrative, not a publishable
event: the actual ID must equal `agent_event_id(record)`.

```json
{
  "schema_version": 1,
  "origin": "real",
  "event_id": "<sha256-of-source-record>",
  "requested_at": "<UTC ISO timestamp>",
  "record": {
    "url": "https://batdongsan.com.vn/...",
    "description": "Bán căn hộ 70 mét vuông, giá năm tỷ đồng."
  }
}
```

`origin` is `real` or `stress`. Stress envelopes must retain synthetic markers
and a URL beginning `https://synthetic.invalid/`; real envelopes reject
synthetic markers. `event_id` hashes the source object excluding Mongo `_id`
and `_agent_event_id`. Timestamp changes may change the event ID; a separate
content cache permits reuse without another provider call.

Results retain `schema_version`, `origin`, `event_id` and the original
`record`, adding `result`:

```json
{
  "status": "success",
  "record": {
    "url": "https://batdongsan.com.vn/...",
    "description": "Bán căn hộ 70 mét vuông, giá năm tỷ đồng.",
    "price_text": "5.0 tỷ",
    "area_text": "70.0 m2",
    "property_type": "apartment"
  },
  "confidence": 0.9,
  "provider": "openai_compatible",
  "model": "<configured-model>",
  "error_code": null,
  "attempts": 1,
  "duration_seconds": 1.2
}
```

Terminal result status is `success`, `failed` or `disabled`; failures may omit
`record`/provider metadata and contain a bounded machine-readable error code.
The result handler accepts only extraction fields and preserves source URL,
existing parseable structured values and synthetic provenance. It normalizes
and validates again before Mongo feature writes. Stale source versions are
recorded as stale receipts without intentionally overwriting newer data.

### External extraction schema

The model returns one JSON object with `fields`, `confidence` and `evidence`;
it does not return the Kafka envelope. `agents/extraction.py` validates it with
strict Pydantic schemas (`extra="forbid"`). Nullable `fields` are:

- `price_vnd` (positive, at most 500 billion), `area_m2` (positive, at most
  10,000), `front_width_m`, `road_width_m` (positive finite numbers);
- `bedroom_count`, `bathroom_count`, `floor_count` (integers from 0 to 100);
- `property_type` (`apartment`, `house`, `land`, `villa_townhouse`, `shophouse`,
  `office`, `warehouse`, `other`), `listing_type` (`sell`, `rent`, `other`);
- `direction`, `legal`, `furniture`, `province`, `district`, `address`.

Confidence must be finite, between zero and one, and meet `AI_MIN_CONFIDENCE`
(default 0.75). Each newly applied value needs a literal quotation from the
allowed source text. Locations also need a matching location phrase; the
model must not infer a province from a district. Missing values stay absent or
null. These checks do not prove a model's numeric interpretation is correct.
URL, title, source text and provenance are not generated by the model. There
is no LLM-generated coordinate/currency schema separate from the existing VND
pipeline. Numeric fields are adapted to the existing raw `*_text` fields, then
the existing parser remains authoritative.

## Normalized feature document

The processor writes real normalized documents to `real_estate_db` and tagged
synthetic documents to `real_estate_stress_db`, never mixing their feature
collections or anomaly baselines.
Raw text is retained in `raw_*` fields; parsed fields are numeric where possible.

| Group | Fields |
| --- | --- |
| Identity/source | `url`, `listing_id`, `source`, `scraped_at`, `updated_at` |
| Location/property text | `title`, `description`, `property_type`, `listing_type`, `province_slug`, `district_slug`, `ward_slug`, `location_slug`, `direction`, `legal`, `furniture`, `project_hint`, `posted_date_text`, `verified` |
| Raw values | `raw_price_text`, `raw_area_text`, `raw_bedroom_text`, `raw_bathroom_text`, `raw_floor_text`, `raw_front_width_text`, `raw_road_width_text` |
| Parsed values | `price_vnd`, `area_m2`, `bedroom_count`, `bathroom_count`, `floor_count`, `front_width_m`, `road_width_m`, `price_per_m2_vnd`, `has_target_price` |
| Enrichment | `text_features`, `text_embedding` (32-dimensional local hash), `text_embedding_provider`, `text_embedding_dimension`, `text_content_hash`, structured `extracted_*` fields, `latitude`, `longitude`, `geo_grid_2dp`, `geo_coordinate_status` |
| Review/candidate | `feature_coverage_score`, `listing_fingerprint`, `listing_review_status`, `validation_errors`, `is_model_candidate`, anomaly metadata and `llm_review_status` |
| Synthetic provenance | `source_type`, `is_synthetic`, `generated_by`, `scenario`, `run_id`, `original_record_id` |
| AI lineage | `_agent_event_id`, `processing_method`, `ai_status`, `ai_attempted`, `ai_event_id`, `ai_provider`, `ai_model`, `ai_confidence`, `ai_error_code`, `ai_attempts`, `ai_duration_seconds`, `ai_completed_at`; request return metadata can include `ai_requested_at` |

Coordinates are accepted only when supplied and within WGS84 bounds. The
current scraper normally provides no coordinates, so geographic status is
usually `MISSING`; no geocoding is performed.

## Validation and candidate rule

`validate_normalized_record()` rejects a record only for a missing URL, invalid
or non-finite numeric values, non-positive price/area, price above 500 billion
VND, area above 10,000 m2, or a price-per-m2 inconsistency greater than 5%.
Invalid records are upserted into `invalid_records`; raw records are retained.

`is_model_candidate` is true when:

1. `price_vnd > 0` (`has_target_price`),
2. `area_m2 > 0`,
3. `property_type` is present,
4. `feature_coverage_score >= 5` across area, price, rooms, dimensions and
   location/property fields, and
5. the record is not synthetic.

Ordinary validation allows absent price/area fields, which makes a record
non-candidate rather than necessarily invalid. When fallback is enabled, or a
record is an AI result, extraction additionally requires usable price, area
and property type. Missing only optional bedroom/bathroom/location fields does
not automatically trigger AI. Parser type exceptions become invalid records;
they are not automatically delegated to the LLM.

`agents/safety.py` recognizes `is_synthetic` values `true`, `"true"`, `"True"`
or `1`; `source_type` values `synthetic`, `stress_agent`, `stress`, `test`;
`generated_by=stress_agent`; and reserved synthetic URL prefixes. Automatic
and manual Mongo queries apply `real_data_query()`. The model's `train()` and
feature-variant evaluator apply `real_records()` before building features,
protecting manual JSON as well. This remains enforced under every anomaly
training policy.

The implemented generator uses `source_type="stress_agent"`,
`is_synthetic=true`, `generated_by="stress_agent"` and
`https://synthetic.invalid/<run_id>/...` URLs. Raw events also contain
`generated_at`; normalized lineage uses the explicit allowlist above and does
not copy every raw key. Ground-truth expected values live in the ignored
stress run files, not as fields for the AI to read.

Price anomaly metadata comes from contextual IQR over `price_per_m2_vnd`; it
does not automatically delete or exclude the record. Trainer inclusion also
honors `PRICE_ANOMALY_TRAINING_POLICY`.

## MongoDB collections and indexes

Collections created by the processor are:

- `listings_raw`: latest raw payload per URL (unique `url` index; not immutable
  event history and no TTL index).
- `training_features`: latest normalized feature per URL (unique `url` plus
  indexes on price, location, candidate, coverage and fingerprint fields).
- `invalid_records`: normalized records that fail validation.
- `dlq_raw`: payloads whose processing/database path failed.
- `price_anomaly_thresholds`: persisted IQR baselines.
- `offset_checkpoint`: committed Kafka group/topic/partition checkpoints.

The same collection names are used in the isolated stress database. Agent
state is written in the database chosen by validated origin:

- `ai_extractions`: `_id=event_id`, original URL, extraction result,
  `extracted`/`published` state and outcome. Source-version checks may also
  record `completed`/`stale` state.
- `ai_response_cache`: `_id` hashes origin, URL, allowed text/raw fields,
  provider, model, minimum confidence and schema version; stores reusable
  successful extraction results. It is not a fuzzy duplicate index.
- `ai_result_receipts`: `_id=event_id`, completed outcome/origin/URL/timestamp,
  suppressing repeated completed result application.
- `ai_failures`: `_id=event_id`, source record, origin, error and non-candidate
  marker for result failures.

Mongo's built-in unique `_id` index backs those agent keys. No TTL index or
automatic cache/DLQ cleanup is configured. `invalid_records` is URL-upserted
but has no explicit unique URL index; it should not be described as an
immutable or transactionally deduplicated log. The feature fingerprint index
is non-unique, and its digest includes URL, so cross-URL near duplicates are
not automatically merged.

Mongo data persists in the Compose named volume `mongo_data`.

## Model feature schema

Target: `price_vnd`, transformed with `log1p` during training and converted
back with `expm1` for predictions.

Base numeric fields: `area_m2`, `bedroom_count`, `bathroom_count`,
`floor_count`, `front_width_m`, `road_width_m`.

Base categorical fields: `property_type`, `direction`, `legal`, `listing_type`,
`province_slug`, `district_slug`, `ward_slug`, `project_hint`.

When enabled (the current default), the model also uses deterministic geographic
fields, `extracted_bedrooms`, `extracted_bathrooms`,
`extracted_amenity_count`, 32 local text-embedding columns,
`extracted_direction`, `extracted_furnishing`, `extracted_legal_status`, and
the `text_features` TF-IDF column. Missing numeric/categorical values are
imputed by the sklearn pipeline; unknown categories are ignored.

## Storage lineage

```text
batdongsan HTML -> real_estate_raw -> existing Processors
  -> real_estate_db.listings_raw -> normalize/enrich/validate
  -> real_estate_db.training_features -> real-only candidate query
  -> model-level synthetic exclusion -> trained artifact -> FastAPI/frontend

difficult source + enabled routing -> real_estate_ai_input
  -> ai-agent -> external HTTPS API or successful response cache
  -> schema/evidence/business validation -> real_estate_ai_results
  -> existing Processors/result handler -> correct origin database
  -> valid features OR invalid_records + ai_failures + acknowledged ai_dlq

stress-agent -> real_estate_stress_raw -> same Processors
  -> real_estate_stress_db (AI branch separately opt-in)
  -> synthetic non-candidates; NO primary training path
```

There are no Parquet datasets, physical Bronze/Silver/Gold directories or
Spark jobs in the current runtime. `processing/export_training_dataset.py` is a
manual export utility, not a pipeline stage.
