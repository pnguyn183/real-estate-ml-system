# Traffic prediction and adaptive admission research

The separate [LLM feedback agent](../docs/LLM_FEEDBACK_CONTROL.md) on `dev`
controls the real stress/crawl publishers and dedicated trainer. This directory
retains the reproducible rule-based comparison and history-collection workflow;
its historical results are not measurements of the new LLM policy.

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
python -m research.telemetry --output runtime/research/passive.jsonl --samples 720 --interval 5 --topic real_estate_raw
python -m research.benchmark traffic --input runtime/research/passive.jsonl --target incoming_rate --output runtime/research/passive-forecast

# Six hours of live crawl-topic history, one sample every five seconds.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/collect_traffic_history.ps1 -Hours 6 -IntervalSeconds 5
```

Passive collection requires an existing instrumented workload on the selected
topic for latency/outcome data. The CLI assigns one stable `run_id` per invocation;
`--topic` and `--group-id` select the measured Kafka stream and committed offsets.
The history script defaults to the live crawl topic `real_estate_raw`; direct
telemetry CLI calls retain the legacy stress-topic default when `--topic` is omitted.
Only the experiment runner supplies offered demand and admission limits; passive
collection cannot invent those fields. Use `--target incoming_rate` for passive
forecasting. Idle intervals may have no latency/error samples, and six hours of
sparse crawling does not guarantee enough traffic variation for a useful forecast.
The collection aims for 4,320 samples; slow measurements can extend elapsed time.
Keep Windows awake and Docker running until it completes. The PowerShell
execution-policy override above applies only to that process.

Audit collected history before forecasting:

```powershell
python -m research.history_audit --input runtime/research/passive.jsonl --output runtime/research/passive-audit
```

The history report separates run/topic/phase boundaries and collection outages.
Empty or unusable timestamp files produce an `insufficient_data` report without
an invented chart. Explicit collection errors or unavailable instrumentation
break forecasting feature and label windows even when the offered rate is still
numeric. Older logs without quality flags remain readable, but their measurement
health cannot be inferred. Run provenance is still required by the benchmark.

## User-controlled history sessions

`python -m research.collect_session start --hours 2` creates a seeded variable
workload and collects telemetry in one foreground process. `--minutes 90` sets
a different length; `--dry-run` previews the plan without touching Kafka or
creating session files. Use `status`/`stop` from another terminal, or Ctrl+C for
a graceful stop and drain. Windows users can use `scripts/traffic_session.ps1`.

Sessions reuse the runner's exclusive lock, three-broker preflight, synthetic
storage isolation and safety thresholds. They use fixed-limit baseline only,
disable resource actuation, and never change `.env`. Service state is unchanged
unless the user explicitly selects `--lean`. The default
paired experiment keeps its one-hour-per-mode guard; the session entry point
explicitly permits up to eight hours with the existing one-million-record cap.
This cap and successful collection do not establish sufficient forecast data.

History sessions pause admission for classified worker HTTP, Docker timeout and
Kafka transport errors and resample within a bounded budget (five consecutive
failed samples or 60 seconds). Two consecutive healthy samples resume admission.
Missing core measurements qualify only with a matching classified source failure;
resource thresholds, unexplained missing data and non-transport errors still stop
immediately. Error
rows remain intact; `telemetry_usable=false` excludes recovery warmup from feature
and label windows. Paused demand is counted as rejected/withheld without a catch-up
burst. Recovery events and the initiating stop observation are saved in the report.
The paired runner retains fail-fast defaults unless explicitly configured otherwise.

Session preflight retries only classified transport failures for up to 60 seconds,
requiring two healthy idle observations before publication. Occupied queues, wrong
topology and memory pressure fail immediately. Attempts are retained in
`baseline/report.json.preflight_attempts`. Deadlines are checked between calls;
in-flight operations have their own timeouts and draining adds time. CLI options
`--recovery-seconds`, `--recovery-errors` and `--preflight-seconds` expose the budgets.

VM memory is sampled from `/proc/meminfo` through a processor. Session guards stop
when MemAvailable is at most 10%, or swap use is at least 95% while MemFree is at
most 5%. The thresholds are stored in `config.json.vm_memory_safety`. They cover
memory pressure missed by the selected-container `ram_percent` metric.

On the reviewed laptop, Docker/WSL kernel evidence from September 28 showed a
512 KiB allocation failure, about 61.6 MiB free RAM and all 2 GiB of swap consumed
during later failed sessions. The local sanitized evidence is
`runtime/research/telemetry-diagnosis-20261001.json`. This identifies VM memory
pressure, not a physical hardware fault or the individual memory-consuming service.

For a memory-constrained laptop, explicitly opt into a shorter validation first:

```powershell
python -m research.collect_session start --minutes 20 --lean
# After inspecting the completed session's quality and memory measurements:
python -m research.collect_session start --hours 2 --lean
```

Lean mode stops running optional services (Airflow, scraper/trainer, API/predictor,
frontend, Grafana/Mongo Express and both agents) while retaining the measured
Kafka/processor/Mongo pipeline and Prometheus. These optional features are offline
during the session. `environment.json` records exact container IDs before stopping;
normal completion, cooperative stop and exceptions restore only the previously
running containers, after identity/configuration checks. Initially stopped/paused
containers remain untouched. Kill/power loss can prevent restoration; inspect the
receipt if interrupted or `restore_failed`. No container is recreated or volume
deleted. The PowerShell wrapper supports `-Lean`; direct Python commands avoid
PowerShell execution-policy restrictions for start, status and stop.

As of this reliability update, Docker Engine is unavailable for a new runtime
trial. Tests exercise the recovery logic and service restoration; the earlier
September 28 short Kafka sessions do not verify the new guard/preflight/lean code
or prove that multi-hour collection succeeds.

Each session saves its exact profile, seed, progress, raw observations and quality
audit. `export --sessions <dir> <dir> --output <new.jsonl>` combines finalized
sessions without bridging gaps, removing error rows or rewriting run IDs. The
manifest records provenance and input hashes. Train on `requested_rate` for this
controlled offered-demand dataset; do not mix it with passive `incoming_rate`.
See [the session guide](../docs/TRAFFIC_SESSIONS.md) for commands and limitations.

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

The optional resource actuator increases processor CPU quotas only; it does not
change memory limits, decrease CPU in response to admission increases, or scale
replicas. Both policies prepare the same initial CPU budget. Enabled experiments
require explicit, nonzero original Docker CPU quotas so the actuator can verify
and restore those exact allocations. Unlimited originals are rejected before any
mutation, because `docker update --cpus 0` cannot reliably restore that state.
Keep this option disabled when that prerequisite has not been provisioned.

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
- `vm_memory_available_percent`, `vm_memory_free_percent`, `vm_swap_used_percent`:
  Linux VM-wide memory from `/proc/meminfo`, distinct from selected-container RAM
  and Windows host memory. Zero configured swap produces no swap-use percentage.
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

The benchmark selects an experimental export candidate using a separate purged
validation split. Export requires positive R² and the configured RMSE improvement
over persistence on both validation and the untouched chronological test. The
`research.forecast.TrafficForecaster` API loads only an eligible local artifact
with its matching hash and returns unavailable on flagged telemetry or history
discontinuities. It is an experimental inference component; the runner does not
use its output for admission decisions. Predictive control and its causal benefit
remain pending a suitable dataset and an independently evaluated control policy.
