# 1. Current Architecture

Research runtime date: 2026-09-21; saved-evidence analysis completed 2026-09-22.
The research question is whether an agent can
regulate incoming traffic and improve measurable Kafka processing behavior. The
existing property-price application is retained as a separate legacy use case.
An improved price R² does not demonstrate traffic-control effectiveness.

The pre-implementation requirement matrix is preserved in
[LECTURER_AUDIT_BEFORE.md](LECTURER_AUDIT_BEFORE.md). That document records the
state after inspection and before this research implementation. Existing source
migration, extraction, application, and infrastructure work was preserved.

The verified application path is:

```text
source adapters / scheduled scraper
  -> scraper/kafka_producer.py
  -> real_estate_raw (Kafka)
  -> three processing/kafka_to_mongo.py workers
  -> raw Mongo archive + normalize/enrich/validate + anomaly annotation
  -> Mongo training_features + best-effort features-topic publication
  -> scheduled trainer queries Mongo -> price artifacts -> prediction API

optional extraction:
processor -> real_estate_ai_input -> agents/worker.py
  -> external extraction provider -> real_estate_ai_results -> processor

isolated traffic experiment:
seeded synthetic offered demand -> local admission gate
  -> real_estate_stress_raw -> same three processors -> real_estate_stress_db
  -> committed offsets + worker metrics + Docker resource counters
  -> research/telemetry.py -> agents/traffic_control.py
  -> bounded admission decision -> gate acknowledgement -> next observations
```

The extraction worker is an agent for structured information extraction. Its
external-call limits are unrelated to Kafka admission control. The new traffic
controller is separate and currently runs inside the research runner. It does
not automatically scale worker replicas or change the production scraper's rate.

`docker-compose.yml` defines brokers `kafka`, `kafka2`, and `kafka3`, broker IDs
1/2/3, ZooKeeper, external listeners 9092/9093/9094, and internal listeners
29092/29093/29094. `scripts/init_kafka_cluster.sh` provisions seven application
topics with three partitions, replication factor three, minimum ISR two, and
rotated preferred replicas. The processors share consumer group
`real_estate_training_pipeline`. Features topics have no repository consumer;
training reads MongoDB. Configuration alone is not evidence of balanced traffic.

Prometheus already scrapes application metrics and Grafana already has source,
pipeline, and agent dashboards. The research collector reuses worker endpoints
and Kafka metadata/offsets, adding Docker container measurements without
replacing that infrastructure. Its lag is the sum of high-watermark minus
committed group offset across all stress-topic partitions; the older worker lag
gauge reflects a polled partition and is not used for research conclusions.

# 2. Lecturer Feedback Audit

Statuses below describe the delivered implementation and available evidence.
SATISFIED means the stated item is verified within its explicitly named scope;
it does not imply proven production improvement. PARTIAL means some required
implementation or empirical evidence is still missing. The saved v4 experiment
has matched configurations and verified isolated traffic, but baseline stopped
early at its safety threshold. Its censored comparison is identified explicitly.

| Requirement | Current implementation | Status | Evidence / relevant files | Missing pieces | Recommended next step |
|---|---|---|---|---|---|
| Inspect architecture before editing | Preserved pre-change inspection and complete producer/consumer/control trace | SATISFIED | `docs/LECTURER_AUDIT_BEFORE.md`; Compose; cluster initializer; processor; extraction worker | None for repository architecture | Keep topology snapshots with each runtime experiment |
| Establish actual ML target and explain R² relevance | Existing target is property `price_vnd`, not traffic | SATISFIED | `modeling/price_model.py`, legacy benchmark metrics | Historical training snapshot unavailable | Keep legacy diagnosis separate from traffic conclusions |
| Reproduce R² approximately 0.2 | Historical price artifact has R² 0.236239; current snapshot rerun gives 0.488991 | PARTIAL | `artifacts/models/metadata_v20260917_062253.json`; `runtime/research/legacy-benchmark/metrics.json` | Exact historical data/order/code environment | Do not describe current rerun as reproduction of the historical dataset |
| Audit missingness, skew, outliers, duplicates, invalid values, encoding and leakage | Read-only 6,562-row quantitative audit; same-query 4,980-row training snapshot; nine plots | SATISFIED | `research/data_audit.py`; `runtime/research/data-audit/audit.json` | Audit does not establish semantic correctness of every listing | Investigate location parsing, price-bearing text, duplicate groups before any later price research |
| Verify previous Gemini cleaning | No Gemini-specific repository implementation or complete external provenance identified | CANNOT VERIFY | `processing/llm_review.py`, `agents/extraction.py`, `agents/results.py`, pre-audit | Original prompts, raw inputs, outputs, model/version and rejection log | Obtain original external cleaning evidence; do not infer it from current validators |
| Compare existing model with RF, Gradient Boosting and XGBoost | All four measured on identical 3,984/996 split, preprocessing and transformed target | SATISFIED | `research/benchmark.py`; legacy `metrics.json`, `split.json`, predictions and chart | One holdout; legacy leakage remains | Treat as requested legacy diagnostic, not traffic improvement |
| Align research with traffic/throughput | Separate traffic observations, controller, experiment and forecast benchmark | SATISFIED | `research/README.md`, `research/run_experiment.py`, `agents/traffic_control.py` | Learned predictive actuation has not been evaluated | First establish measured reactive baseline |
| Train traffic forecasts at 5/10 minutes | Past-only lag/rolling features; chronological holdout; horizon purge; persistence/RF/GBR/XGB implementation | PARTIAL | `research/benchmark.py`, `utils/tests/test_research_ml.py` | Sufficient uninterrupted and representative history | Collect longer genuine traffic history; honor `insufficient_data` |
| Observe-assess-decide-act-measure loop | Bounded feedback controller with actual gate acknowledgement and repeated observations | SATISFIED | `agents/traffic_control.py`, `research/run_experiment.py`, control/runner tests | Production deployment outside isolated research | Evaluate repeated runs before integrating production control |
| Structured action/state/reason/timing log | JSONL observations and decisions include offered rate, throughput, CPU/RAM, lag, latency, errors, old/new limits and timestamps | SATISFIED | Controller `Decision`, action receipts, runner JSONL | Predicted traffic is null because no forecast drives this policy | Preserve nulls rather than invent forecasts |
| Monitor all three brokers and quantify distribution | All brokers measured; ingress shares about 33% each, but broker 2 CPU is higher | SATISFIED | `research/telemetry.py`; v4 `analysis/summary.json`; Section 6 | Full JMX request/byte metrics absent | Preserve distinction between balanced leader ingress and unequal CPU |
| Reproducible fixed versus adaptive stress | Same finite seeded ramp, records and logical keys; isolated topic/DB; drain; manifests; process lock | SATISFIED | `research/experiment.json`, runner, runner tests | Repeated seeds/order and broader workload mixtures | Repeat both run orders; distinguish rejected demand from served load |
| Measure detection, actuation and recovery time | Measured T0/T1/T2/T3 and real acknowledgement; recovery is explicitly drain-phase | SATISFIED | v4 adaptive `report.json`, `actions.jsonl`; Section 6 | No demonstrated sustained-load recovery; one episode | Repeat episodes; include collection delay when interpreting submillisecond local control timings |
| Before/after throughput, CPU/RAM, lag, latency, errors, rate limits and actions | Actual common-window comparison and ten charts, with censored baseline | SATISFIED | v4 `analysis/summary.json`; Sections 6, 7 and 9 | Repeated complete pairs required for causal effectiveness claims | Keep unfavorable metrics and rejection counts visible |
| Determine sustainable load and saturation | Stable tested stages: baseline through 500/s, adaptive through 250/s; baseline 1000/s safety stop | PARTIAL | v4 stage checks; Section 8 | Longer repeated plateaus; exact saturation point and resource cause | Report observed bounds; do not claim increased capacity |
| Produce nine required chart categories | Legacy algorithm chart, data plots and ten system plots generated from saved evidence | SATISFIED | Section 9; `research/report.py` | No traffic algorithm chart because both horizons have zero usable pairs | Collect longer telemetry before forecast comparison |
| Select scientifically defensible approach | Explainable adaptive rules plus persistence/tree forecast benchmark | SATISFIED | Section 5; research README; forecast implementation | Sufficient data to justify more complex approaches | Defer LSTM/RL/clustering until their data assumptions are met |
| Demonstrate automatic resource scaling effectiveness | Admission control implemented; worker count fixed | NOT IMPLEMENTED | Compose worker services; controller action changes only rate gate | Replica/resource actuator and independent scaling experiment | Describe delivered mechanism as adaptive admission control; do not claim replica autoscaling |
| Preserve synthetic safety and working application | Stress topic/DB and synthetic filtering reused; production model not replaced by benchmark | SATISFIED | `agents/safety.py`, generator, stress runner, existing training filters and tests | None for implemented isolation | Keep finite bounds and separate research artifacts |

# 3. ML/Data Findings

The existing algorithm is a `VotingRegressor` averaging Ridge,
HistGradientBoostingRegressor, and SGDRegressor, wrapped in
`TransformedTargetRegressor(log1p/expm1)`. Its target **y is `price_vnd`**.
X contains area, bedrooms, bathrooms, floors, frontage/road width, geographic
features, property/direction/legal/listing/location categories, extracted text
attributes, 32 local hashed text-embedding coordinates, and TF-IDF listing text.
It does not consume Kafka telemetry or predict incoming traffic.

Numerical preprocessing uses training-fitted median imputation and scaling;
categories use training-fitted most-frequent imputation and one-hot encoding
with unknown categories ignored; TF-IDF is fitted on training text. These
operations are correctly inside the train-fitted preprocessing path. The
existing random 80/20 split with seed 42 is unsuitable for future traffic and is
not grouped by duplicate listing content. The audit benchmark retains that
split solely to diagnose the existing model. No replacement model was deployed.

The snapshot at `2026-09-21T04:40:24.599608+00:00` found 6,603 raw listings,
6,562 feature records and 4,980 candidates selected by the actual training query.
There are 109 observed feature-record columns with numeric, string, boolean,
list and dictionary values. The 1,582 feature records outside the training
query are not evidence of Gemini removal. The audit uses sequential read-only
Mongo cursors, so it is not a transactional snapshot of concurrent ingestion.
JSONL files are retained and SHA-256 hashes recorded; the candidates hash is
`85b31405edcf7bd0b96a3b69e37c41b6345a7d463772c5fcc83fb491a63a4eda`.

| Field | Feature rows missing | Candidate rows missing | Observed type / feature cardinality |
|---|---:|---:|---|
| Price | 1 / 6,562 (0.02%) | 0 / 4,980 | float / 1,165 values |
| Area | 0 | 0 | float / 1,738 values |
| Bedrooms | 3,441 (52.44%) | 2,305 (46.29%) | integer / 33 values |
| Bathrooms | 3,595 (54.79%) | 2,412 (48.43%) | integer / 33 values |
| Floors | 4,172 (63.58%) | 3,218 (64.62%) | integer / 13 values |
| Front width | 2,227 (33.94%) | 1,428 (28.67%) | float / 363 values |
| Road width | 2,444 (37.24%) | 1,490 (29.92%) | float / 96 values |
| Latitude / longitude | 6,562 each (100%) | 4,980 each (100%) | no present values |
| Province slug | 6,510 (99.21%) | 4,928 (98.96%) | string / 20 values |
| District slug | 6,511 (99.22%) | 4,929 (98.98%) | string / 35 values |
| Ward slug | 6,513 (99.25%) | 4,931 (99.02%) | string / 35 values |
| Property type | 606 (9.23%) | 0 | string / 6 values |
| Listing type | 0 | 0 | string / 5 values |
| Description | 1 (0.02%) | 1 (0.02%) | string / 6,552 values |

Price, area and price-per-area are strongly right-skewed. IQR outliers are
statistical extremes, not proof of erroneous listings; no audit outliers were
deleted or clipped.

| Variable | Median | 99th percentile | Maximum | Skewness | 1.5-IQR outlier count |
|---|---:|---:|---:|---:|---:|
| Price (VND) | 7.20 billion | 150 billion | 450 billion | 7.021 | 687 / 6,561 |
| Area (m²) | 94.5 | 2,549.19 | 10,000 | 9.871 | 777 / 6,562 |
| Price/m² (VND) | 84.0 million | 580.27 million | 2.099 billion | 3.733 | 433 / 6,561 |
| Bedrooms | 3 | 17 | 50 | 5.944 | 176 / 3,121 |
| Bathrooms | 3 | 17 | 47 | 5.221 | 108 / 2,967 |
| Floors | 4 | 8.11 | 14 | 0.885 | 47 / 2,390 |
| Front width (m) | 5 | 40 | 220 | 11.377 | 420 / 4,335 |
| Road width (m) | 8 | 42 | 300 | 8.581 | 329 / 4,118 |

The numeric audit found no present unparseable, infinite or nonpositive values
in those eight variables. The reused deterministic record validator reported
no errors on the feature snapshot. This establishes only the implemented
checks, not that the dataset is clean. Geographic coordinates, grids and project
hints are entirely missing; several status/anomaly columns are constant among
present values; source is dominated by one value (99.51%). Missingness and
constant columns reduce available information. Geographic slug values include
short legacy fragments, and direction spellings differ. Most-frequent
imputation and one-hot encoding cannot repair incorrect source semantics.

There are zero exact URL or listing-fingerprint duplicates, but 27 extra title
duplicates, 9 extra description duplicates, 8 extra text-feature duplicates,
and 5 extra text-content hashes in all features. The 4,980 candidates retain
22, 8, 7 and 4 respectively. In the actual random holdout, 9 title values,
2 description values and 2 text-feature values occur in both splits. This is
measured train/test contamination despite unique URLs. Duplicate annotations
already stored in Mongo are historical processing flags and are not equivalent
to these exact snapshot duplicate counts.

Price-unit tokens occur in 3,187 titles, 6,404 descriptions and 6,466 text-feature
strings out of 6,562 feature records. In training candidates they occur in
2,443 titles, 4,865 descriptions and 4,917 text-feature strings. These counts
identify substantial target-leakage risk: the asking price can be present in X
while asking price is y. The token detector does not prove each token reveals
the exact target. A later credible price study would require price-text
redaction, duplicate-group isolation and a time-aware holdout. Those changes
are outside the traffic research target and were not silently presented as
traffic optimization.

The archived result closest to the lecturer's R² approximately 0.2 is
`artifacts/models/metadata_v20260917_062253.json`: R² **0.236239**, MAE
5,521,854,105 VND, RMSE 18,557,037,967 VND, 4,960 samples. That artifact is
verified; its original training snapshot is unavailable, so exact historical
reproduction is **not** claimed. The pre-audit latest saved result was
0.579663 on 4,966 samples. The current 4,980-row snapshot produces a different
0.488991. Changes among these artifacts cannot be attributed to this task as a
controlled optimization.

The requested fair diagnostic comparison uses the same 3,984 training / 996
test records, 2,029 transformed features, log target, and fixed holdout for all
models. MAE/RMSE below are in **billions of VND**; JSON stores original VND.

| Model | R² | MAE (billion VND) | RMSE (billion VND) | R² difference from existing |
|---|---:|---:|---:|---:|
| Existing voting ensemble | 0.488991 | 4.616007 | 14.192591 | 0 |
| Random Forest Regressor | 0.510214 | 4.583648 | 13.894742 | +0.021223 |
| Gradient Boosting Regressor | 0.446412 | 4.910597 | 14.772045 | -0.042579 |
| XGBoost | 0.592611 | 4.261827 | 12.672202 | +0.103620 |

RF/XGBoost improved this measured holdout; Gradient Boosting did not. There is
no confidence interval or hyperparameter search, and known leakage remains.
These are property-price findings only. Versions and estimator parameters are
saved in `runtime/research/legacy-benchmark/metrics.json` (Python 3.12.10,
NumPy 2.5.2, pandas 3.0.5, scikit-learn 1.9.0, XGBoost 3.4.1).

No Gemini-specific cleaning implementation was found. The repository's optional
OpenAI-compatible extraction path validates JSON schema, types, evidence and
domain constraints before accepting results; these checks reduce but cannot
eliminate semantic inconsistency. Raw storage uses latest-URL upserts, so a
later scrape can replace an earlier raw document. AI result handling preserves
its archived input, but this is not immutable historical crawl provenance.
No before/after Gemini corpus exists here to quantify removed rows or determine
whether legitimate extreme records were removed. No source data was repaired
or overwritten by the audit.

Traffic observations now record explicit timestamps/run IDs, rates, limits,
resources, backlog and outcomes. The forecast benchmark audits timestamp
validity/duplicates, sample intervals, gaps, phase boundaries, missing targets,
skewness, trend and lag-1 autocorrelation. Features and labels cannot cross
runs, gaps or load/drain boundaries; train label timestamps must precede the
first test observation. Short scripted ramps do not establish daily/weekly
seasonality or representative 5/10-minute forecasting skill. The recorded
`runtime/research/traffic-benchmark-v4/metrics.json` reports `insufficient_data`
at both 300 and 600 seconds, with zero valid horizon pairs. The adaptive file
contains 60 rows spanning 285.511 seconds, median interval 4.986 seconds, zero
invalid/duplicate timestamps, zero detected gaps, one load/drain boundary and
zero missing offered-demand targets. Requested-rate skewness is 1.217 and lag-1
autocorrelation 0.904; these summarize the scripted ramp, not production
seasonality. No traffic R²/MAE/RMSE is claimed.

# 4. Changes Made

This inventory covers the research work from the preserved checkpoint through
this continuation. Some files were already committed by the user between
turns; the current `git diff` alone therefore does not describe the whole task.
Unrelated source migration, frontend, extraction and documentation work is not
claimed as a research change.

The subsequent crawler reliability review requested by the user has its own
complete file inventory, live-source evidence and validation in
[CRAWL_STABILITY.md](CRAWL_STABILITY.md).

| File | Change | Reason |
|---|---|---|
| `README.md` | Linked the pre-audit, traffic research workflow and this report near the project introduction | Make the revised research direction visible without rewriting the application documentation |
| `docs/LECTURER_AUDIT_BEFORE.md` | Recorded architecture, requirement matrix and risks before implementation | Preserve inspect-first evidence |
| `docs/LECTURER_RESEARCH_REPORT.md` | This standalone findings/deliverables report | Connect conclusions to real artifacts and gaps |
| `research/__init__.py` | Research package entry point | Keep experiments separate from production price code |
| `research/README.md` | Reproduction commands, metric definitions, safety and interpretation | Make results independently repeatable |
| `research/requirements.txt` | Optional plotting/XGBoost scientific dependencies | Avoid replacing application requirements |
| `research/experiment.json` | Finite staged workload, controller limits, hard stops and stability criteria | Declare policy/SLOs before measurement |
| `research/data_audit.py` | Read-only immutable local snapshots, statistical profiles, leakage indicators and plots | Quantify current training data quality |
| `research/benchmark.py` | Same-split legacy comparison plus purged chronological traffic benchmark | Answer model comparison without mixing targets or future/past |
| `research/telemetry.py` | Three-broker offsets/leadership, workers and Docker resource collector | Measure actual throughput, backlog, resources and distribution |
| `research/run_experiment.py` | Paired replay, rate gate, observations/actions, manifests, drain and fail-safe stops; resumed cross-process experiment lock | Ensure comparable workloads and prevent overlapping experiments |
| `research/report.py` | Regenerable summaries, common-window before/after comparison, stability/balance/reaction analysis and plots; readable event labels, load-end markers and explicit drain recovery labels | Separate measured findings from missing/censored evidence and prevent drain from appearing as recovery under continued traffic |
| `agents/traffic_control.py` | Configurable bounded reactive controller, acknowledgement and T0–T3 episodes | Implement measurable admission control |
| `utils/metrics.py` | Topic-labeled input outcome and handling/end-to-end histograms plus instrumentation version | Measure durable input completion and queue-inclusive latency |
| `processing/kafka_to_mongo.py` | Record topic-specific outcomes and durations at durable input commit; read the legacy per-partition lag gauge from cached high watermarks | Tie metrics to actual processing semantics and remove a measured per-record remote watermark delay; authoritative research lag still uses independent group offsets |
| `utils/tests/test_agent_pipeline.py` | Added processor metric/outcome assertions around input handling | Verify instrumentation does not change successful processing |
| `utils/tests/test_research_ml.py` | Snapshot profiling, epoch timestamps, chronological leakage purge and gap/phase boundaries | Verify scientific split/data assumptions |
| `utils/tests/test_traffic_control.py` | Thresholds, bounds, stale/missing telemetry, acknowledgement and reaction episodes | Verify safe decisions and honest timings |
| `utils/tests/test_traffic_telemetry.py` | Committed offsets, broker attribution, counter resets, worker instrumentation/restarts and resource units | Prevent false zero or misleading rates |
| `utils/tests/test_traffic_runner.py` | Gate pacing, same seeded workload, publication/preflight failure handling | Verify fair bounded experiments |
| `utils/tests/test_traffic_report.py` | Stability/censoring, missing metrics, common window, imbalance, reactions and chart provenance | Prevent unsupported improvement/capacity claims |

Generated runtime JSON/JSONL/CSV/PNG files are research outputs, not production
source changes. Full price/listing snapshots remain local under ignored
`runtime/`; summary results in this report avoid publishing raw records.

# 5. Agent Strategy

The selected policy is an explainable reactive admission baseline with a
separate forecasting evaluation path. It observes offered demand, committed
throughput, stress-topic consumer lag, enqueue-to-commit p95 latency, error
fraction, and selected pipeline CPU/RAM. `predicted_traffic` stays null in
decisions because this policy does not use a trained forecast.

The runner's `research/experiment.json` explicitly sets:

| Setting | Value |
|---|---:|
| Observation cadence / decision cadence | 5 s / 10 s |
| Admission minimum / initial / maximum | 1 / 1000 / 1000 messages/s |
| Congestion CPU/RAM thresholds | 80% / 80% of Docker engine capacity |
| Safe-recovery CPU/RAM thresholds | 65% / 65% |
| Congestion lag / recovery lag | 200 / 30 messages |
| Congestion p95 / recovery p95 | 2 / 1 seconds |
| Congestion error fraction / recovery error fraction | 0.01 / 0.001 |
| Multiplicative decrease / additive increase | ×0.7 / +20 messages/s |
| Cooldown / sustained safe window / maximum telemetry age | 10 / 15 / 15 seconds |
| Experiment hard-stop lag / CPU / RAM | 10,000 messages / 95% / 90% |
| Maximum records / drain bound | 100,000 / 120 seconds |

Risk triggers a bounded multiplicative reduction after cooldown. A sustained
safe window allows an additive increase only when offered demand exceeds the
current limit. Missing/invalid/stale telemetry forces the configured minimum;
it cannot justify an increase. This can reduce throughput sharply when
measurement fails and must be visible in the action log. The local token bucket
applies each change before publication; an acknowledgement records actual T2.
Rejected demand is counted explicitly. Hysteresis, cooldown, bounds, limited
burst credit and hard stops prevent uncontrolled oscillation or runaway load.

For eventual forecasting, the directly measured targets are future
`requested_rate` (pre-admission offered demand supplied by the runner) or future
`incoming_rate` (admitted Kafka ingress). They have different meanings and are
not substituted silently. The implemented 300/600-second benchmark uses current
and lagged telemetry, past rolling averages, time-of-day/day-of-week features,
chronological holdout and a horizon purge. Persistence is the baseline against
RF, Gradient Boosting and XGBoost. It requires at least 100 train and 30 test
samples after purging; short or fragmented histories fail explicitly.

LSTM is deferred because there is no demonstrated long sequential corpus. RL
(Q-Learning/DQN) is deferred because repeated safe episodes, a validated
environment/simulator and an evaluated reward are absent. No RL state/action/
reward/episode is claimed. Clustering plus rules is deferred because measured
workload-cost labels and sufficiently varied workload mixtures are absent.
These choices keep complexity proportional to evidence; adding any of these
algorithms now would not resolve missing experimental data.

# 6. Kafka Stress-Test Findings

**Measured result: the controller reduced peak backlog and avoided the hard
safety stop by rejecting demand. It did not demonstrate higher sustainable
throughput, lower CPU/RAM, or better mean latency.** The saved experiment is
`runtime/research/paired-v4-20260921`, analyzed on 2026-09-22 without starting
another workload. Both manifests have identical configuration, source hashes,
dependency versions and container identities. Both runs started with zero
backlog, all partitions in sync, and one stress-partition leader on each broker.
Acknowledged messages equal final topic offset growth in each mode, confirming
exclusive stress-topic publication. There were no collector errors or observed
under-replicated partitions in the retained 59 baseline / 60 adaptive samples.

The shared workload profile requests 10, 40, 100, 250, 500 and 1,000 messages/s,
each for 45 seconds, seed 42, identical structured synthetic facts/logical keys,
and the same 1,000/s initial admission limit. Baseline ran first; neither workers
nor broker/Mongo state were reset between modes. Scraper/trainer were paused
for the saved pair. The common worker image includes the cached-watermark fix.

| Full-run measurement | Fixed baseline | Adaptive |
|---|---:|---:|
| Status | Safety stopped: lag >=10,000 | Completed |
| Load duration (s) | 252.094 | 270.000 |
| Actual offered messages | 67,584 | 85,474 |
| Admitted / Kafka acknowledged | 67,430 | 49,821 |
| Rejected at local gate | 154 (0.228%) | 35,653 (41.712%) |
| Enqueue failures / delivery failures / undelivered | 0 / 0 / 0 | 0 / 0 / 0 |
| Drain duration (s) | 23.516 | 17.500 |
| Final Kafka lag | 0 | 0 |
| Applied limit changes | 0 | 5 reductions |

These unequal-duration totals are descriptive, not a fair count-based
before/after comparison. The shorter baseline finished five stages and about
27.1 seconds of its sixth stage, then triggered the declared safety stop.
`paired_runs_completed` is false. Section 7 compares only observed intervals
inside the shared 252.094-second load window. Synthetic publication, final
drain and collection succeeded; the baseline's censored stage remains censored.

The broker table integrates leader-offset growth during each mode's observed
load intervals. Samples collected after a load boundary are excluded, so those
growth counts need not equal full-run acknowledgements.

| Mode | Broker | Leader growth (messages) | Ingress share | Mean CPU (% of one core) | Mean RAM (MiB) | Mean RX / TX (MB/s) |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 1 | 21,698 | 33.232% | 15.306 | 858.22 | 1.214 / 0.899 |
| baseline | 2 | 21,748 | 33.308% | 31.798 | 1071.17 | 1.277 / 1.062 |
| baseline | 3 | 21,847 | 33.460% | 15.253 | 880.76 | 1.214 / 0.908 |
| adaptive | 1 | 16,218 | 33.101% | 17.055 | 1118.13 | 1.007 / 0.745 |
| adaptive | 2 | 16,245 | 33.156% | 31.370 | 1177.69 | 1.059 / 0.880 |
| adaptive | 3 | 16,532 | 33.742% | 18.205 | 1107.46 | 1.014 / 0.752 |

Baseline ingress max/mean is **1.003798**, coefficient of variation **0.002845**;
adaptive is **1.012267**, CV **0.008700**. Leader ingress was close to one third
per broker; CPU was not equally distributed. Broker 2 used roughly twice the
baseline CPU of brokers 1/3, and CPU max/mean was 1.530 baseline / 1.412 adaptive.
This supports balanced stress-message ingress, not universally balanced broker
resource load. Each broker led one partition and held all three replicas;
leadership did not change in observed samples. Disk counters and per-container
resources are also preserved in `observations.jsonl` and `analysis/summary.json`.

Offset-derived ingress is stress-partition log growth attributed to the observed
leader, not a JMX broker message/request counter. Docker network includes
replication and other traffic; rounded CLI sizes limit byte/disk rate precision.
CPU/RAM aggregates cover the selected Kafka/processor/Mongo containers divided
by Docker-engine capacity (16 logical CPUs, 8,174,338,048 bytes memory), not all
Windows-host utilization. A per-container CPU value of 100% means one core.
The two-second collection is not an atomic snapshot of Kafka, workers and Docker.

Throughput means committed input offsets, including durably handled invalid or
DLQ records; outcome counters distinguish them. It does not guarantee downstream
feature-topic delivery. Error fraction was zero in observed nonempty intervals;
gate rejection is a separate measurement and is not hidden in that zero.
Enqueue-to-commit latency includes Kafka waiting. Histogram p95 is approximate;
the mean of interval p95 values is not the run's event-level p95. An empty
histogram remains missing, not zero.

There was one adaptive congestion episode. The exact UTC timestamps below are
from `adaptive/report.json` and gate acknowledgements in `actions.jsonl`:

| Event | Definition | UTC on 2026-09-21 |
|---|---|---|
| T0 | First completed sample showing congestion (lag 296 >200) | 10:05:06.133672 |
| T1 | Decision detecting that episode | 10:05:06.134658 |
| T2 | Acknowledgement after admission gate changes 1000 ->700/s | 10:05:06.135156 |
| T3 | Sample completing the continuous safe window | 10:06:26.151915 |

| Recorded duration | Measured value |
|---|---:|
| Detection: T1 - T0 | 0.000986 s |
| Adjustment: T2 - T1 | 0.000498 s |
| Recovery: T3 - T2 | 80.016758 s |
| Total: T3 - T0 | 80.018243 s |

The submillisecond detection value measures local decision handling after a
completed sample, **not time from physical congestion onset**. Sampling is every
5 seconds and decisions every 10 seconds; this crossing happened to coincide
with a decision opportunity. The Kafka source timestamp preceded T0 by 2.110377
seconds while resource collection completed, so Kafka-source-sample-to-decision
time was about 2.111363 seconds. Median collection duration was 2.087/2.091
seconds, and maximum was 2.125/2.371 seconds for baseline/adaptive respectively.
The actual threshold crossing can predate the first Kafka sample too.

The first 700/s limit was still above the then-offered 500/s. Acknowledgement
therefore proves a changed gate setting, not an immediate reduction in admitted
traffic. Subsequent measured changes were:

| Elapsed time (s) | Applied limit (messages/s) | Reason |
|---|---:|---|
| 192.125 | 1000 ->700 | Kafka lag |
| 202.141 | 700 ->490 | Kafka lag and p95 latency |
| 217.172 | 490 ->343 | Kafka lag and p95 latency |
| 237.188 | 343 ->240.10 | Kafka lag and p95 latency |
| 252.094 | 240.10 ->168.07 | p95 latency |

T3 was 2.149 seconds **after offered traffic ended**. It is labeled `drain`,
and cannot establish sustained-load recovery caused by the agent. Baseline's
episode has null T1/T2/T3 because no agent acted and the safe recovery window
was not observed, even though final drain backlog reached zero. There was no
measured additive increase. The controller ended at 168.07/s.

`runtime/research/clock-check.json` retains the host/worker clock comparison;
zero offset was compatible with measurement uncertainty, so no manual latency
correction was applied. Submillisecond local gate timing must not be interpreted
as submillisecond precision of end-to-end system latency.

Earlier attempts are excluded from comparison: initial smoke and smoke-v2
failed preflight before workload; smoke-v3 overlapped a prematurely started
original paired run, and both have `INVALID.json`. Paired-v2 encountered
unavailable Docker before workload. Paired-v3 baseline failed its 120-second
drain with 4,843 messages outstanding, so no adaptive mode started. Its old
worker queried a remote watermark for every record: a read-only probe measured
0.5006-0.5024 seconds per uncached call versus about 0.6-7.3 microseconds for
cached reads (`runtime/research/watermark-probe.json`). The cached gauge fix is
present in **both v4 modes**, and its improvement is not credited to the agent.
Independent committed offsets remain the authoritative research lag source.

# 7. Before vs After

The following values come directly from v4 `analysis/summary.json`'s
`before_after` array: weighted means over complete observed intervals within
the common 252.094-second load window, with no interpolation. There are 51
baseline endpoints (2.031-252.094 s) and 50 adaptive endpoints (7.078-252.094 s).
The baseline's first latency/error sample is unavailable and omitted. Independent
sample endpoints and stage-boundary overlap explain the small observed schedule
mean difference even though the configured profile is identical.

| Metric | Before Agent | After Agent | Difference |
|---|---:|---:|---:|
| Mean committed throughput (messages/s) | 214.172187 | 184.958143 | -29.214043 |
| Mean gate-accepted rate (messages/s) | 267.477766 | 187.311659 | -80.166107 |
| Mean pipeline CPU (% engine capacity) | 10.187381 | 11.198132 | +1.010751 |
| Mean pipeline RAM (% engine capacity) | 47.380575 | 57.139621 | +9.759047 |
| Mean Kafka lag (messages) | 680.750106 | 449.458887 | -231.291219 |
| Mean interval p95 latency (s) | 1.226664 | 1.956334 | +0.729670 |
| Mean invalid/DLQ/failed fraction | 0.000000 | 0.000000 | +0.000000 |
| Mean observed requested schedule (messages/s) | 279.578523 | 281.610792 | +2.032269 |
| Mean Kafka ingress (messages/s) | 259.014991 | 185.387415 | -73.627576 |
| Mean acknowledged rate (messages/s) | 267.247059 | 187.283780 | -79.963279 |
| Mean admission limit (messages/s) | 1000.000000 | 859.436748 | -140.563252 |
| Peak pipeline CPU (%) | 23.101250 | 30.643125 | +7.541875 |
| Peak pipeline RAM (%) | 57.690948 | 62.227372 | +4.536424 |
| Peak Kafka lag (messages) | 11318.000000 | 2843.000000 | -8475.000000 |
| Peak interval p95 latency (s) | 28.455524 | 9.750000 | -18.705524 |

CPU/RAM differences are percentage points. Peak lag fell 74.88% and peak
interval p95 fell 65.74%, but mean committed throughput fell 13.64%; mean CPU,
RAM and interval p95 increased. The adaptive run started with substantially
higher resident RAM (about 56% versus 40% at the first low-load plateau).
Run order, retained state/cache and temporal performance changes are confounders.
These measurements demonstrate a trade-off from admission control, not a
universal optimization or a statistically isolated causal effect. The complete
ledger also retains unfavorable outcomes and 35,653 rejected requests.

# 8. Sustainable/Optimal Load

A stable offered-load stage must complete, have at least four valid observations
covering its final 25 seconds, max lag <=200, lag growth <=1 message/s,
max interval p95 <=2 s, pipeline CPU/RAM <=80%, error fraction <=1%, and at
least 95% of requested demand admitted and processed. Peaks cannot be hidden
by good means. Missing coverage and an early stop make a stage inconclusive.

| Requested plateau (messages/s) | Baseline classification | Baseline tail throughput (messages/s) | Adaptive classification | Adaptive tail throughput (messages/s) |
|---|---|---:|---|---:|
| 10 | stable_offered | 9.999 | stable_offered | 10.000 |
| 40 | stable_offered | 39.933 | stable_offered | 40.033 |
| 100 | stable_offered | 100.113 | stable_offered | 100.031 |
| 250 | stable_offered | 250.066 | stable_offered | 249.329 |
| 500 | stable_offered | 500.012 | unstable | 404.170 |
| 1000 | inconclusive | 548.269 | unstable | 268.198 |

The 1,000/s baseline tail has only two samples before the safety stop; its
548.269/s is a transient measurement, not a completed-stage capacity estimate.
The highest passing baseline plateau was **500 offered/s**, with 500.012/s
processed, CPU peak 22.651%, RAM peak 54.130%, lag peak 153, p95 peak 0.926 s,
and zero observed errors in its evaluated tail. At 1,000/s, lag grew rapidly
(about 455.8 messages/s in the two late samples), reaching 11,318 before load
stopped; p95 reached 28.456 s, with CPU/RAM peaks 22.421%/57.691% in that tail.
This demonstrates congestion at the higher offered rate despite aggregate
CPU/RAM headroom. It does not identify which downstream resource caused it.

The highest passing adaptive offered plateau was **250/s**, with 249.329/s
processed, CPU peak 19.717%, RAM peak 57.894%, lag peak 16, p95 peak 0.256 s,
and zero observed errors. Its 500/s stage failed backlog, latency, backlog-growth
and service criteria (93.84% of offered traffic admitted; tail p95 peak 9.75 s,
lag peak 2,795). Its 1,000/s stage admitted only 23.83% of offered demand and
failed lag/latency peaks; a declining backlog does not make that demand
sustainable. No stage passed `stable_admitted_only` in this run.

Thus the observed passing offered range was **10-500/s baseline and 10-250/s
adaptive** among the tested stages. This is not evidence of capacity gain.
Baseline shows congestion by 1,000/s after passing the short 500/s stage;
the exact sustainable boundary between those rates is unresolved. Neither mode
produced a valid completed-stage throughput plateau under the predeclared
heuristic (>10% input increase with <=5% output increase). Longer plateaus,
intermediate load steps, reversed run order, repeated seeds, comparable starting
resource state and varied workload costs are required before naming an optimal
operating point. A configured ceiling is never counted as measured capacity.

# 9. Generated Charts

All paths below exist and were regenerated from measured snapshots/logs.
Distribution figures contain histograms and boxplots; missing-value statistics
are also saved in JSON. No traffic model-comparison chart is produced because
both forecast horizons have zero usable supervised rows.

| Chart | Output path |
|---|---|
| Legacy algorithm R² with MAE/RMSE labels | `runtime/research/legacy-benchmark/algorithm_comparison.png` |
| price_vnd histogram and boxplot | `runtime/research/data-audit/charts/price_vnd_distribution.png` |
| area_m2 histogram and boxplot | `runtime/research/data-audit/charts/area_m2_distribution.png` |
| price_per_m2_vnd histogram and boxplot | `runtime/research/data-audit/charts/price_per_m2_vnd_distribution.png` |
| bedroom_count histogram and boxplot | `runtime/research/data-audit/charts/bedroom_count_distribution.png` |
| bathroom_count histogram and boxplot | `runtime/research/data-audit/charts/bathroom_count_distribution.png` |
| floor_count histogram and boxplot | `runtime/research/data-audit/charts/floor_count_distribution.png` |
| front_width_m histogram and boxplot | `runtime/research/data-audit/charts/front_width_m_distribution.png` |
| road_width_m histogram and boxplot | `runtime/research/data-audit/charts/road_width_m_distribution.png` |
| Listing missing values | `runtime/research/data-audit/charts/missing_values.png` |
| Committed throughput | `runtime/research/paired-v4-20260921/analysis/throughput.png` |
| CPU | `runtime/research/paired-v4-20260921/analysis/cpu.png` |
| RAM | `runtime/research/paired-v4-20260921/analysis/ram.png` |
| Kafka lag | `runtime/research/paired-v4-20260921/analysis/kafka_lag.png` |
| Enqueue-to-commit interval p95 | `runtime/research/paired-v4-20260921/analysis/latency.png` |
| Applied rate limit | `runtime/research/paired-v4-20260921/analysis/rate_limit.png` |
| Invalid/DLQ/failed fraction | `runtime/research/paired-v4-20260921/analysis/error_rate.png` |
| Agent decisions | `runtime/research/paired-v4-20260921/analysis/agent_actions.png` |
| Requested/admitted/processed traffic | `runtime/research/paired-v4-20260921/analysis/input_vs_throughput.png` |
| Broker 1/2/3 leader ingress | `runtime/research/paired-v4-20260921/analysis/broker_load.png` |
| Telemetry requested rate distribution | `runtime/research/traffic-benchmark-v4/data_charts/requested_rate_distribution.png` |
| Telemetry incoming rate distribution | `runtime/research/traffic-benchmark-v4/data_charts/incoming_rate_distribution.png` |
| Telemetry throughput distribution | `runtime/research/traffic-benchmark-v4/data_charts/throughput_distribution.png` |
| Telemetry cpu percent distribution | `runtime/research/traffic-benchmark-v4/data_charts/cpu_percent_distribution.png` |
| Telemetry ram percent distribution | `runtime/research/traffic-benchmark-v4/data_charts/ram_percent_distribution.png` |
| Telemetry kafka lag distribution | `runtime/research/traffic-benchmark-v4/data_charts/kafka_lag_distribution.png` |
| Telemetry latency p95 seconds distribution | `runtime/research/traffic-benchmark-v4/data_charts/latency_p95_seconds_distribution.png` |
| Telemetry error rate distribution | `runtime/research/traffic-benchmark-v4/data_charts/error_rate_distribution.png` |
| Telemetry current limit distribution | `runtime/research/traffic-benchmark-v4/data_charts/current_limit_distribution.png` |
| Telemetry missing values | `runtime/research/traffic-benchmark-v4/data_charts/missing_values.png` |

The ten system charts show full recorded timelines, including drain. Dotted
vertical lines identify each mode's load end; adaptive T0/T1/T2/T3 annotations
are taken from real logs and the recovery label explicitly says `drain`.
Closely spaced detection/action labels use separate heights so they remain
readable. All ten system PNGs were visually reviewed. Section 7's comparison
table deliberately excludes unshared later load and drain intervals.

# 10. Validation

The retained earlier ML command journal is `runtime/research/ml_validation.json`.
Intermediate failures and invalid-run markers are preserved. The prior full-suite
455-pass result predates this continuation. After the research and crawler fixes,
the final whole-repository run passes 526 tests (373 dependency deprecation
warnings); its JUnit evidence is `runtime/crawl_review/tests.xml`. The focused
research suite also passes 80 tests.

| Command / action actually executed | Result |
|---|---|
| `python -m pip install "matplotlib>=3.8,<4" "xgboost>=2.1,<4"` | Earlier journal: matplotlib 3.11.2 / XGBoost 3.4.1 installed; research minimum matplotlib subsequently set to 3.10 |
| `python -m pytest utils/tests/test_research_ml.py -q` (initial) | 2 failed / 3 passed; pandas 3 timestamp-unit assumption fixed |
| `python -m research.data_audit --output runtime/research/data-audit` (initial) | Mongo connection refused while Docker stopped; no snapshot written |
| Same audit command after Docker startup | 6,562 features, 4,980 candidates, nine plots |
| `python -m research.benchmark legacy --input runtime/research/data-audit/candidates.jsonl --output runtime/research/legacy-benchmark --jobs 2` | Four algorithms measured on same split; metrics/predictions/chart saved |
| `python -m pytest utils/tests/test_research_ml.py -q` (saved journal final) | 7 passed |
| `python -m compileall -q research/data_audit.py research/benchmark.py` | Passed in saved journal |
| `python -m pytest -q` (prior checkpoint) | 455 passed; 373 dependency deprecation warnings; predates latest edits |
| `python -m pytest -q --junitxml=runtime/crawl_review/tests.xml` (2026-09-22, final combined changes) | 526 passed; 373 dependency deprecation warnings |
| Earlier targeted controller/report/runner checks | An intermediate XML records 3 failures/84 cases; saved checkpoint subsequently records 83 passing checks after corrections |
| Earlier targeted telemetry checks | Saved checkpoint records 46 passing checks |
| Docker processor build / `docker compose up -d --no-deps processor processor-2 processor-3` | Instrumented workers deployed; resumed build transient TLS timeout succeeded on retry |
| Initial smoke / smoke-v2 | Preflight failures before load |
| Smoke-v3 / original paired attempt | Excluded: overlapping experiments; retained `INVALID.json` |
| Paired-v2 | Docker unavailable before workload |
| `python -m research.run_experiment --config research/experiment.json --output runtime/research/paired-v3-20260921` | Baseline drain timeout, 4,843 backlog; adaptive not started |
| Read-only cached/uncached watermark and host/worker clock probes | Probe JSON preserved; uncached monitoring bottleneck identified |
| `python -m research.run_experiment --config research/experiment.json --output runtime/research/paired-v4-20260921` | Saved runtime: baseline safety-stopped; adaptive completed; both drained, exclusive topic confirmed |
| `python -m research.report --input runtime/research/paired-v4-20260921` | Analysis JSON/Markdown and ten actual plots generated; rerun after annotation/layout fix |
| `python -m research.benchmark traffic --input runtime/research/paired-v4-20260921/adaptive/observations.jsonl --output runtime/research/traffic-benchmark-v4` | 300s/600s both `insufficient_data`; zero horizon pairs; ten telemetry audit plots |
| `python -m pytest utils/tests/test_traffic_report.py utils/tests/test_research_ml.py -q` | 27 passed before chart presentation fix |
| `python -m pytest utils/tests/test_traffic_control.py utils/tests/test_traffic_telemetry.py utils/tests/test_traffic_runner.py utils/tests/test_traffic_report.py utils/tests/test_research_ml.py -q` | Final 80 passed; earlier render run had two empty-legend warnings, corrected and rerun without warnings |
| `python -m compileall -q research/report.py` | Passed after chart presentation changes |
| Visual inspection with image viewer | Legacy algorithm/missing-value plots previously checked; all ten v4 system plots checked after labels and load boundaries were corrected |

Use a fresh output directory for new snapshots/runs; existing raw snapshots are
not overwritten. Analysis can be regenerated from the immutable raw JSONL:

```powershell
python -m research.report --input runtime/research/paired-v4-20260921
python -m research.benchmark traffic --input runtime/research/paired-v4-20260921/adaptive/observations.jsonl --output runtime/research/traffic-benchmark-v4

# New live repeat: requires instrumented Docker services and an exclusive topic.
docker compose config --quiet
docker compose pause scraper trainer
try {
    python -m research.run_experiment --config research/experiment.json --output runtime/research/paired-repeat-NEW
} finally {
    docker compose unpause scraper trainer
}
python -m research.report --input runtime/research/paired-repeat-NEW

# Optional passive history; no synthetic load is generated by this command.
python -m research.telemetry --output runtime/research/passive-NEW.jsonl --samples 720 --interval 5
```

Pause only services that were running and restore their original state. The
passive command needs an existing instrumented workload for topic latency/error
data; it does not supply pre-admission demand or experiment `run_id`. The runner
adds these fields. For forecasting, collect a longer bounded profile in a new
configuration/output directory, or provide genuine upstream demand/run metadata;
do not lower horizon/data minimums just to obtain R². Do not interpret a scripted
ramp as natural seasonality. No new live experiment was run on 2026-09-22 for
this saved-evidence finalization.

# 11. Remaining Gaps

1. **Prediction and proactive control remain unproven.** The 60-row adaptive
   history is shorter than either prediction horizon. Longer representative
   telemetry, persistence/tree holdouts and an independent predictive-control
   experiment are required. No traffic model was trained or deployed.
2. **Higher capacity is not demonstrated.** Baseline's 1,000/s stage is censored;
   adaptive fails the specified 500/1,000/s SLOs. Repeat longer/intermediate
   stages, both run orders and seeds with comparable starting conditions.
   Investigate the processing bottleneck while retaining the same baseline.
3. **Recovery under sustained load remains unverified.** The one recorded
   recovery is after offered demand stops. First reduction 1000 ->700/s exceeds
   the then-offered rate. Examine whether a capacity-aware bounded policy can
   recover sooner without excessive rejection, then evaluate on fresh runs.
4. **Production and resource scaling are outside the delivered actuator.**
   Admission control affects the isolated research producer. Production crawl
   integration, consumer replica autoscaling and resource allocation are absent.
5. Exact reproduction of historical R² 0.236239 needs its original training
   snapshot/order/environment. Current price-model results are a different
   snapshot and remain vulnerable to price-bearing text and duplicate leakage.
6. External Gemini cleaning provenance is unavailable. Its rejected rows,
   transformations, raw preservation and semantic quality cannot be verified.
   Location/coordinate missingness and listing skew remain documented issues.
7. Broker monitoring uses leader offsets plus Docker counters, not full JMX
   request/byte/disk-service instrumentation. Broker ingress is balanced, but
   broker 2 CPU is higher; this requires diagnosis before claiming even resource
   load. Aggregate CPU headroom cannot rule out serialized/IO bottlenecks.
8. Sampling, two-second source collection, histogram buckets, host/worker clock
   uncertainty, persistent memory state and run order limit precision/causal
   conclusions. Keep missing values, admission rejection and censored stages
   explicit. Raw ignored runtime artifacts must accompany any submitted report.

# 12. Final Lecturer Checklist

- [x] Inspect architecture before editing and verify the entire Kafka pipeline:
  `docs/LECTURER_AUDIT_BEFORE.md`, Section 1.
- [x] Identify actual existing algorithm, features and target as property price:
  `modeling/price_model.py`, Section 3.
- [x] Find the approximately 0.2 result: saved metadata R² 0.236239, Section 3.
- [ ] Reproduce that exact historical result: original training snapshot absent.
- [x] Quantify rows/schema/types, missingness, duplicates, invalid values,
  constants/cardinality, outliers, skewness and leakage: `data-audit/audit.json`.
- [x] Produce distribution/boxplot/missing-value evidence: nine listing charts
  and ten telemetry charts, Section 9.
- [ ] Verify external Gemini cleaning and deleted/overwritten records: provenance
  and a before/after corpus are unavailable.
- [x] Compare existing/RF/Gradient Boosting/XGBoost fairly with R²/MAE/RMSE:
  saved legacy benchmark, Section 3; improved price R² is not traffic evidence.
- [x] Align the research with traffic prediction/control and retain the legacy
  application separately: research package, Sections 1 and 5.
- [x] Implement time-stamped offered/admitted traffic, throughput, lag, latency,
  errors, CPU/RAM and action history: v4 observations/actions and telemetry code.
- [x] Implement chronology-safe 5/10-minute forecast evaluation and report
  insufficient data honestly: `traffic-benchmark-v4/metrics.json`, zero pairs.
- [ ] Demonstrate traffic forecasting skill and proactive predictive control:
  longer representative history and a measured model/control experiment needed.
- [x] Implement observe-assess-decide-act-measure with explicit bounds,
  thresholds/cooldown and applied-change receipt: controller code, v4 actions.
- [x] Monitor all three brokers and quantify distribution: Section 6,
  ingress shares/CV/max-mean plus CPU/RAM/network evidence in v4 summary.
- [x] Execute the same seeded profile with fixed/adaptive policy, isolation,
  guardrails and drain; retain baseline safety-stop censoring: v4 run reports.
- [x] Publish measured T0/T1/T2/T3 and detection/adjustment/recovery latencies,
  with collection-delay and drain caveats: Section 6, adaptive report/actions.
- [ ] Establish reliable recovery while offered load continues: observed T3 is
  in drain, not sustained load.
- [x] Publish measured before/after throughput, CPU/RAM, lag, latency, errors,
  limits and actions, including unfavorable values: Sections 6-7 and v4 summary.
- [x] Define stable offered load and publish tested operating-range evidence:
  baseline 10-500/s; adaptive 10-250/s among tested stages, Section 8.
- [ ] Establish a repeatable optimal operating point/exact saturation threshold
  or demonstrate increased sustainable capacity: repeated longer runs needed.
- [x] Generate and inspect all nine required chart categories from saved data,
  with actual control events and drain boundaries: Section 9.
- [x] Select a simple defensible policy and explain why LSTM/RL/clustering are
  deferred: Section 5 and `research/README.md`.
- [ ] Demonstrate automatic replica/resource scaling: no such actuator exists.
- [x] Preserve reproducibility, source hashes, runtime identity, raw evidence,
  failed/invalid attempts and honest conclusions: manifests, summaries and
  Section 10; final focused research validation is 80 passing tests.
