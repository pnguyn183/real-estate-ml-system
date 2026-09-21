# Lecturer requirement audit — before implementation

Audit date: 2026-09-21. This matrix was written after read-only inspection and
before implementation changes for this task. Existing uncommitted work is the
input baseline and must be preserved. Status refers to that baseline, not the
subsequent implementation. Runtime data and additional quantitative findings
belong in the final research report.

## Verified architecture

Source adapters (`scraper/sources`, `scraper/multi_source.py`) normalize source
structure; `scripts/auto_scrape.py` invokes `scraper/kafka_producer.py`.
Kafka brokers `kafka`, `kafka2`, `kafka3` have IDs 1, 2, 3 and ZooKeeper discovery.
Host ports are 9092/9093/9094; internal ports 29092/29093/29094.
`scripts/init_kafka_cluster.sh` provisions seven application topics with three
partitions, replication factor three, minimum ISR two, and rotated preferred
replicas. Broker health alone does not establish actual load balance.

Three `processing/kafka_to_mongo.py` workers share consumer group
`real_estate_training_pipeline`, consuming real raw, stress raw, and AI results.
They archive raw listings, normalize/enrich/validate, annotate price anomalies,
upsert MongoDB features, and best-effort publish a features topic. Features topics
have no repository consumer. The trainer queries MongoDB, not these topics.
Stress messages use `real_estate_stress_raw` and `real_estate_stress_db` with
synthetic markers excluded from every price-training entry point.

Optional extraction is processor → `real_estate_ai_input` → `agents/worker.py`
→ external OpenAI-compatible transport → validated `real_estate_ai_results`
→ processor. This agent does not control traffic. Its limits and circuit breaker
bound external extraction calls. `agents/stress.py` generates finite seeded
fixed/burst traffic; it has no observation or adaptive decision loop.

Prometheus scrapes application metrics and Grafana has three dashboards. No
broker/JMX or Docker resource exporter exists. The processor lag gauge is the
last polled partition's high watermark minus that record's offset, not total
consumer-group committed backlog (`processing/kafka_to_mongo.py:866`). The
processed counter increments after a features enqueue attempt, not broker
acknowledgment (`processing/kafka_to_mongo.py:744`).

## Requirement matrix

| Requirement | Current implementation | Status | Evidence / relevant files | Missing pieces | Recommended change |
|---|---|---|---|---|---|
| Understand actual data flow and topology | Three brokers, three processors, Mongo, separate extraction and synthetic paths | SATISFIED | `docker-compose.yml`, `scripts/init_kafka_cluster.sh`, `processing/kafka_to_mongo.py`, `agents/worker.py`; live Docker services running | Runtime load evidence distinct from configuration | Preserve infrastructure; inspect metadata during experiments |
| Identify ML target and relevance | Predicts `price_vnd`, log1p transformed target; listing numeric/categorical/text X | SATISFIED | `modeling/price_model.py:106-200,238-248`; `docs/ML_PIPELINE_AUDIT.md` | Price model is unrelated to future traffic | Keep as legacy application; build separate traffic research path |
| Reproduce R² ≈ 0.2 | Latest saved price artifact R² 0.5796634, 4,966 samples | CANNOT VERIFY | `artifacts/price_model_metrics.json`, `artifacts/models/metadata_*.json` | Dataset/version for lecturer's 0.2 result not identified | Snapshot current training data and reproduce existing evaluation; report discrepancy |
| Review dataset and Gemini cleaning | Deterministic normalization, IQR flags, optional strict extraction; no Gemini-specific source found | PARTIAL | `processing/dataset_quality_report.py`, `processing/llm_review.py`, `agents/extraction.py`, `agents/results.py` | Full numerical audit; prior external Gemini provenance unavailable | Profile actual Mongo snapshot, missingness, duplicates, skewness, outliers, leakage; do not claim historical Gemini verified |
| RF / Gradient Boosting / XGBoost comparison | VotingRegressor of Ridge, HistGradientBoosting, SGD; no requested comparison | NOT IMPLEMENTED | `modeling/price_model.py:153-196`; random 80/20 seed 42 at :238 | Common features/split, metrics and artifacts | Add diagnostic legacy comparison plus separate chronological traffic benchmark |
| Historical traffic features and 5/10-minute targets | Application counters scraped every 15 seconds; no saved aligned forecasting dataset | PARTIAL | `utils/metrics.py`, `monitoring/prometheus.yml`; live Prometheus has short fragmented histories | True group lag, requested/admitted input, broker/resource telemetry, limits and aligned series | Save observed telemetry with timestamps, run IDs and units; fail explicitly on insufficient horizon data |
| Traffic rather than price research objective | Existing application and documents focus on prices | NOT IMPLEMENTED | `README.md`, `docs/REQUIREMENTS.md`, `modeling/price_model.py` | Traffic model/control experiment | Select explainable adaptive rules first, train forecast only when sufficient telemetry exists |
| Observe → assess/predict → decide → act → measure | Extraction worker polls requests; stress sends preconfigured schedule | NOT IMPLEMENTED | `agents/worker.py:172,260`, `agents/stress.py:134-246` | Feedback controller and actuation | Add bounded rate gate using actual CPU/RAM/lag/latency/errors, cooldown and hysteresis |
| Decision log with states, limits, reasons and duration | Extraction/request counters, run-level stress report | NOT IMPLEMENTED | `agents/metrics.py`, `agents/stress_metrics.py` | Structured decisions and timing | Append JSONL observations/actions and explicit applied timestamps |
| All three brokers under stress; quantify balance | All three brokers configured, delivery partition counts only | PARTIAL | `docker-compose.yml`, `runtime/stress/final-verification-20260909/report.json` | Broker ingest rates, leadership, CPU/RAM/network/I/O and imbalance | Measure leader ingress from offsets and metadata, container counters; distinguish ingress from replication/network traffic |
| Fair baseline vs adaptive stress | Seeded bounded generator and synthetic isolation | PARTIAL | `agents/generator.py:39-51`, `agents/stress.py:76-94`, `agents/safety.py` | Equivalent staged ramp, policy toggle, drain/warmup, time series | Reuse generator and workers; save same profile/seed and explicit rejected demand |
| T0 congestion, T1 detection, T2 adjustment, T3 recovery | No such event model | NOT IMPLEMENTED | `agents/stress.py:140-153,246`; repository search | Independent observation/controller cadence, episodes | Measure timestamps automatically; retain incomplete episodes as null, report observation resolution |
| CPU/RAM/latency/errors/backlog throughput before/after | Application counters, process metrics, processing histogram only | PARTIAL | `utils/metrics.py`, `monitoring/grafana/dashboards/agent_operations.json` | Broker/container measurements, end-to-end queue latency, consistent windows | Add run collection and topic-labeled outcome/latency metrics |
| Sustainable load and saturation | Historical smoke delivered 12 records in 5.514s at 2.176/s | NOT IMPLEMENTED | `runtime/stress/final-verification-20260909/report.json` | Stable criteria and staged plateau evaluation | Define SLOs before run; distinguish accepted stable load from rejected offered demand; do not extrapolate |
| Reproducible before/after charts | Existing dashboards, no paired saved experiment plots | PARTIAL | `monitoring/grafana/dashboards/*.json`, `docs/assets/project-flow.svg` | All nine requested evidence plots and source logs | Generate charts from saved JSONL/results with missing metrics shown as unavailable |
| Algorithm/agent approach selection | No forecasting/RL/clustering policy | NOT IMPLEMENTED | `agents/` and `modeling/` inspection | Defensible data-dependent choice | Start with configurable rules and persistence baseline; compare tree forecasts with chronological horizon purge; defer RL/LSTM until suitable data/episodes |
| Synthetic isolation and bounded safety | Dedicated topic/DB, train exclusion, finite count/rate/time | SATISFIED | `agents/safety.py:13-39`, `agents/stress.py:23-29,76-94`, `utils/tests/test_synthetic_safety.py` | New experiment must preserve those invariants | Reuse synthetic generator and guardrail; never train prices on stress listings |
| Automatic scaling effectiveness | Fixed worker instances; no automatic replica scaling | NOT IMPLEMENTED | `docker-compose.yml:251-367` | Scaling actuator and experiments | Evaluate dynamic admission control explicitly; do not relabel it as replica autoscaling |

## Data/model risks found before edits

The current price ensemble uses median-imputed/scaled numeric listing fields,
one-hot categorical fields and TF-IDF text plus local hashed text enrichment.
Preprocessing is fit after the split, but the random split is neither temporal
nor cross-URL duplicate-group aware. Description/title text may contain the
target asking price. Raw Mongo storage is a latest-URL upsert, not immutable
source history. AI-result handling preserves the archived input, but successive
crawls can overwrite earlier raw snapshots. Strict evidence/schema checks reduce
extraction errors; they cannot prove truth or establish what an earlier external
Gemini process did. Existing audit prose is therefore not proof of clean data.

The requested implementation will keep production price serving intact, retain
the original audit as a historical record, and label test fixtures, live measured
results, legacy price results, and unexecuted future experiments separately.
