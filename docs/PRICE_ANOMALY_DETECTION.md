# Price anomaly detection

`processing/price_anomaly.py` is called by the processor after normalization
and validation and before the normalized document is upserted into MongoDB
`training_features`.

```text
real_estate_raw -> preserve raw -> normalize/enrich/validate -> historical IQR annotation
                 -> MongoDB training_features
                 -> optional Kafka real_estate_features audit feed
```

The trainer queries MongoDB; it does not consume `real_estate_features`.
The same routine is also called by `agents/results.py` for validated
`real_estate_ai_results` and by the synthetic-topic branch. AI results do not
invoke extraction again. Synthetic processing uses its own
`real_estate_stress_db.training_features` and anomaly-threshold collections,
not the real-data baseline or primary trainer dataset.
Raw source payloads are retained in `listings_raw` as the latest URL-keyed
document. Flagged records are retained for review and are never deleted or
modified in their ground-truth `price_vnd`.

## Detector

The detector evaluates `price_per_m2_vnd` rather than total price. Its primary
baseline group is `province_slug + district_slug + property_type`, with a
fallback to `province_slug + property_type`. For each usable historical group it
computes Q1, Q3 and IQR and applies `Q1 - multiplier*IQR` and
`Q3 + multiplier*IQR` bounds (default multiplier 1.5). The incoming URL is
excluded from the baseline before it is upserted.

Groups below the minimum size, missing grouping values, invalid price/area and
zero-IQR groups produce an auditable `UNAVAILABLE` result rather than a forced
flag. Zero-IQR groups may fall back to the wider grouping.

## Stored metadata

Normalized documents may include:

- `price_per_m2_vnd`, `is_price_anomaly`, `price_anomaly_status` (`NORMAL`,
  `FLAGGED`, `UNAVAILABLE`) and `price_anomaly_type` (`HIGH`/`LOW`);
- score, reason, group/columns, baseline size, quartiles, bounds and refresh
  timestamp for explainability;
- unified review fields such as `listing_review_status`, `anomaly_types`,
  `detection_method` and `detected_at`.

The default training policy is `FLAG`, so flagged records remain eligible. Set
`PRICE_ANOMALY_TRAINING_POLICY=EXCLUDE` to filter flagged records from training;
anomaly metadata is never included as a model feature.
Synthetic exclusion is a separate mandatory gate at ingestion, candidate
generation, Mongo query and model-training levels. Neither `KEEP` nor `FLAG`
enables synthetic training. Accepted real AI-derived features, however, follow
the same anomaly/candidate policy as other real records.

## Configuration

| Variable | Default |
| --- | --- |
| `PRICE_ANOMALY_IQR_MULTIPLIER` | `1.5` |
| `PRICE_ANOMALY_MIN_GROUP_SIZE` | `30` |
| `PRICE_ANOMALY_GROUP_COLUMNS` | `province_slug,district_slug,property_type` |
| `PRICE_ANOMALY_FALLBACK_GROUP_COLUMNS` | `province_slug,property_type` |
| `PRICE_ANOMALY_REFRESH_SECONDS` | `300` (in-process cache refresh interval) |
| `PRICE_ANOMALY_TRAINING_POLICY` | `FLAG` (`KEEP`, `FLAG` or `EXCLUDE`) |

Threshold documents are stored in `price_anomaly_thresholds`; there is no
configured MongoDB TTL or scheduled materialization job. The detector exposes
logs but no dedicated Prometheus anomaly metric/dashboard. Statistical flags are
not fraud labels and should be reviewed with domain context.

## Optional LLM review

`processing/llm_review.py` is a disabled-by-default provider boundary. When
`LLM_REVIEW_ENABLED=false` (the default), no network call is made. The current
Processor constructs it without a provider; merely enabling the variable does
not install one. The hook supports suspicious-record review without changing
`price_vnd`, but it is not the asynchronous extraction service.

The implemented, separate `ai-agent` is a fallback for missing/unusable
required source fields before IQR review: Processor → `real_estate_ai_input`
→ external API/cache → `real_estate_ai_results` → Processor validation/IQR.
It is controlled by `AI_ENABLED`, `AI_FALLBACK_ENABLED` and the independent
`AI_STRESS_ENABLED` gate, all false by default. A price anomaly alone does not
send a normally parsed record to this extraction agent. Failed extraction goes
to review/DLQ and never replaces the deterministic IQR algorithm.

**Implementation reviewed:** 2026-09-08. See [PROJECT_STATUS.md](PROJECT_STATUS.md)
for actual verification status.
