# Traffic prediction and adaptive admission research

The research target is incoming data traffic and sustainable Kafka processing.
The existing property-price API remains a separate legacy application. Read
[`docs/LECTURER_AUDIT_BEFORE.md`](../docs/LECTURER_AUDIT_BEFORE.md) for the
pre-implementation requirement matrix.

## Reproduce

Run these commands from the repository root with Python 3.12 and Docker Compose.
The measurement runner runs on the host: it uses the published Kafka listeners,
worker metrics ports, and the local Docker CLI. It never resets offsets or
deletes data. Every run needs a fresh output directory.

```powershell
python -m pip install -r requirements.txt -r research/requirements.txt
docker compose config --quiet
docker compose build processor processor-2 processor-3
docker compose up -d
python -m pytest -q

# Read-only snapshot; retains source data locally, outside version control.
python -m research.data_audit --output runtime/research/data-audit
python -m research.benchmark legacy --input runtime/research/data-audit/candidates.jsonl --output runtime/research/legacy-benchmark

# Prevent scheduled crawling/training from competing during paired runs.
# Pause only services that are already running; restore in finally.
docker compose pause scraper trainer
try {
    python -m research.run_experiment --config research/experiment.json --output runtime/research/paired-run
} finally {
    docker compose unpause scraper trainer
}
python -m research.report --input runtime/research/paired-run
python -m research.benchmark traffic --input runtime/research/paired-run/adaptive/observations.jsonl --output runtime/research/traffic-benchmark
```

A short paired ramp can evaluate control but cannot supply meaningful 5/10-minute
forecast holdouts. The forecasting command writes `insufficient_data` when
uninterrupted, chronological training and test samples do not survive the
horizon purge. Collect longer runs using a reviewed profile in a new config;
repeat baseline/adaptive in both orders and multiple seeds. Never lower the
minimum data requirement merely to obtain a metric. The separate collector can
save passive measurements, but its `incoming_rate` is **admitted Kafka traffic**,
not pre-admission demand:

```powershell
python -m research.telemetry --output runtime/research/passive.jsonl --samples 720 --interval 5
```

Passive collection requires an existing instrumented stress workload for
topic-specific latency/outcome data. The experiment runner supplies `run_id`,
offered demand, and limits; the collector by itself cannot invent those fields.

## Policy and safety

`agents/traffic_control.py` is an interpretable reactive baseline. On independently
sampled telemetry it detects CPU/RAM/lag/end-to-end latency/error thresholds.
Risk reduces the admission limit multiplicatively; sustained safe observations
allow additive increases when offered demand exceeds the current limit.
Configuration supplies bounds, hysteresis, cooldown, and missing-data protection.
The local token bucket applies the command before publishing; every rejection
is recorded. A small bucket (20 ms of limit, at least two tokens) accommodates
scheduler jitter. This is dynamic admission control, not replica autoscaling
and not a trained forecast.

`research/experiment.json` declares the workload and SLOs before execution:
lag <200, interval p95 <=2 seconds, selected pipeline CPU/RAM <=80% of Docker
engine capacity, errors <=1%, lag growth <=1 message/s, and >=95% of requested
demand admitted and processed over an observed plateau. Sustained recovery uses
the lower controller thresholds. These are experimental acceptance criteria,
not discovered universal constants. The separate hard stops bound runaway load.

The same seed, structured-message scenario, profile and logical Kafka keys are
used in each mode. Distinct run URLs isolate Mongo documents. Admission changes
which records enter Kafka; both requested and admitted demand are reported.
Queue drain is required between modes. Scheduler shortfalls and early safety
stops are retained, and incomplete stages cannot establish capacity. Only
structured synthetic records are used; no websites or paid LLM calls are part
of the workload. AI/stress background producers should remain disabled.

## Measurement semantics

- `throughput`: rate of committed input offsets across all three stress-topic
  partitions, including invalid/DLQ handling. Outcome counters separately expose
  errors. This does not claim successful delivery of the best-effort clean topic.
- `kafka_lag`: high watermark minus committed group offset summed across all
  stress partitions. The older processor lag gauge is not used for research.
- `latency_p95_seconds`: interval histogram estimate from message enqueue to
  successful input commit. It includes Kafka waiting; broad histogram buckets
  limit precision. Processing duration is also collected separately.
- `cpu_percent` / `ram_percent`: selected Kafka, worker and Mongo containers
  divided by Docker engine CPU/memory. This is not Windows host-wide usage.
  Per-container CPU uses Docker core-percent (100% means one core).
- Per-broker leader ingress is stress-partition log-offset growth attributed
  using observed leadership. It is not a JMX broker message counter. Actual
  Docker network rates include replication, clients and unrelated traffic;
  displayed Docker sizes have rounding error. Disk I/O, RAM, leadership and
  replica counts are also retained. Leader changes invalidate attribution.
- Missing data, counter resets, unavailable workers, and empty histograms stay
  unavailable. A version gauge makes missing instrumentation fail before load.
- Collection sources have separate timestamps and a recorded collection duration;
  they are not an atomic snapshot. Sampling limits onset/reaction precision.

T0 is the first observed threshold crossing; T1 is the controller decision that
detects risk; T2 is acknowledgement after the real gate changes; T3 requires a
continuous safe window. No actuation means no T2; no observed recovery means no
T3. Idle intervals lack latency samples and cannot establish recovery. Recovery
after workload removal is explicitly labeled `drain`, not credited as recovery
under sustained traffic. CPU/RAM safety totals exclude other services; isolate
other host workloads when making capacity claims.

## Artifacts

Each experiment saves configuration, source hashes, runtime versions/container
identity, topology, observations JSONL, action JSONL, stage counts, reports and
reaction episodes. `research.report` regenerates the comparison JSON/Markdown
and throughput, CPU, RAM, lag, latency, limit, input-vs-throughput, broker-load,
error and action plots from these files. Missing series never become demo data.

`research.data_audit` writes immutable snapshots, schema/missingness/cardinality,
duplicate counts, quantiles, skewness, IQR outliers, and real distribution plots.
`research.benchmark legacy` compares the existing voting ensemble, Random
Forest, Gradient Boosting and XGBoost using the same fitted preprocessing,
log target and original shuffled split. This diagnoses the legacy result;
price text and duplicate contamination prevent a clean generalization claim.
No production model is overwritten.

`research.benchmark traffic` uses past-only lag/rolling features, timestamp/run/
gap boundaries, chronological holdout and a label-horizon purge. It compares
persistence, RF, Gradient Boosting and XGBoost. LSTM lacks a justified long
sequence corpus; RL lacks safe repeated episodes and a validated simulator;
clustering lacks a demonstrated workload-cost labeling scheme. Defer them
until the simpler measured baseline establishes a need.
