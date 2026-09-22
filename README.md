# Real Estate Price Prediction System

The current **research objective is traffic prediction and adaptive Kafka
admission control**. The price API below remains the legacy application. See
[the pre-change lecturer audit](docs/LECTURER_AUDIT_BEFORE.md),
[reproducible research commands](research/README.md), and
[measured findings and remaining gaps](docs/LECTURER_RESEARCH_REPORT.md).

Python/Kafka/MongoDB/scikit-learn pipeline with an authenticated FastAPI API and
React frontend. The supported deployment is a single-host Docker Compose stack
for local development and validation. Kafka runs as three ZooKeeper-mode
brokers with replicated, partitioned topics; the brokers still share one host
and are not production host-level HA.

## Runtime flow

```text
Alonhadat / Homedy (opt-in); Guland adapter disabled pending permission/review
  -> source-specific adapters -> canonical listing schema v2
  -> scripts/auto_scrape.py + scraper/kafka_producer.py
  -> Kafka cluster (real_estate_raw, 3 partitions, RF=3)
  -> processor workers (one consumer group, three instances)
  -> MongoDB listings_raw + training_features
  -> scripts/auto_train.py
  -> artifacts/models/price_model.joblib
  -> FastAPI :8000
  -> React/Nginx :3000
```

The separate legacy predictor also loads the model on port 8002, but the UI
does not call it. It is unauthenticated and published on the host; keep access
restricted to a trusted development network.

The optional AI fallback is asynchronous: difficult records go from a Processor
to `real_estate_ai_input`, the external-API `ai-agent`, then
`real_estate_ai_results`, and back to a Processor for validation and MongoDB
persistence. Structured records continue through the deterministic path without
an LLM. The agent writes extraction/cache state, not training features directly.

The deterministic `stress-agent` publishes clearly tagged synthetic data to
`real_estate_stress_raw`. The same three Processors route it to
`real_estate_stress_db`, never the primary training database. Primary Mongo
training queries, manual/JSON training, model training and dataset export also
exclude synthetic markers. Stress traffic shares worker/broker capacity and can
slow real ingestion; database isolation is not workload isolation.

Crawler, Processor, trainer and both agent metrics are scraped by Prometheus.
Grafana provisions Pipeline, Agent Operations and Source Ingestion dashboards.
There is no Spark, physical Bronze/Silver/Gold lake, relational DB,
ELK, Jaeger, Alertmanager or Kubernetes deployment in this repository.

For the complete implementation map and detailed Mermaid diagrams, see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[flow_diagram.md](flow_diagram.md).

See [current verification evidence](docs/PROJECT_STATUS.md) before a demo:
mixed-source fixture ingestion and mock AI integration are verified. Batdongsan
is no longer selectable by the crawler. Real external LLM verification is still
pending usable local provider credentials. A mock result is not a real API test.

## Repository structure

```text
modeling/       FastAPI, auth, model training/inference and Dockerfiles
agents/         External-API extraction worker, result handler, stress generator and safety
processing/     Kafka consumer, normalization, enrichment and anomaly review
scraper/        Three source adapters, bounded HTTP, canonical Kafka producer
scripts/        scheduled scraper/trainer and health helper
frontend/       React/TypeScript/Vite source and Nginx image
monitoring/     Prometheus config, alert rules and Grafana provisioning
utils/tests/    Python unit tests
docs/           Architecture, schema, operations, targets and audits
```

## Quick start

```bash
docker compose config --quiet
docker compose build --no-cache
docker compose up -d
docker compose ps
```

The 19-service Compose configuration includes one completed `kafka-init` job.
It provisions seven application topics with three partitions, replication
factor three and minimum ISR two. The three Processors share
`real_estate_training_pipeline` and subscribe to raw, stress-raw and AI-result
topics. The AI worker uses `real_estate_ai_extraction` for AI requests.

Live crawling, both agents and AI routing are disabled by default: no `.env`, model selection
or LLM API key is needed for ordinary startup. See
[deployment configuration](DEPLOYMENT.md#optional-agent-configuration) for
deliberate, bounded enablement. Agent health/metrics ports 8006/8007 are
Compose-network-only, not published host endpoints.

Open:

- Frontend: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001` (local Compose credentials)
- Mongo Express: `http://localhost:8081` (basic auth)

Kafka host listeners are `localhost:9092`, `localhost:9093` and
`localhost:9094`; processor metrics are exposed on ports 8003, 8004 and 8005.

## Three-source ingestion

Latest crawl reliability review and fixes:
[Crawl stability (2026-09-22)](docs/CRAWL_STABILITY.md). Checkpoints now persist
between scheduled runs, each source has its own timeout, and failed details
cannot block the other source. Homedy still shows oversized sequential
responses in live checks; this is tracked as a source limitation.

Different property websites present information differently. The ingestion
layer acts as a translator: it converts the selected website's information
into one common format before sending it into the existing Kafka pipeline.

- `alonhadat`: `/can-ban-nha-dat`, independent static parser, small live sample tested.
- `homedy`: `/ban-nha-dat`, independent adapter reusing the tested Homedy parser.
- `guland`: offline parser only; live collection disabled pending permission
  and fresh schema verification. Its fixture does **not** prove live crawling.

Default `ENABLED_SOURCES=alonhadat,homedy`, `CRAWL_ENABLED=false`. Review collection
and ML reuse permissions before enabling real ingestion. No Batdongsan option
is registered. Historical data/parsing tests remain for compatibility.
See [audit, contract, capabilities and limitations](docs/SOURCE_MIGRATION.md).

```bash
# Run only while automatic crawling is disabled (CRAWL_ENABLED=false)
# and the Airflow crawl DAG is paused: checkpoint/metrics have one writer.
# Controlled test after permission/reuse review (maximum two real records):
docker compose exec scraper python -m scraper.kafka_producer --crawl-enabled --source alonhadat --limit 2 --max-pages 1
# Repeatable synthetic fixtures through actual Kafka/Mongo; never trains:
docker compose exec processor python scripts/verify_source_pipeline.py
```

Optional Airflow: see [airflow/README.md](airflow/README.md). Its DAG starts
paused; stop the legacy scraper/trainer loops before using that scheduler.

## Alternative source trial (not scheduled ingestion)

`scraper/homedy_scraper.py` is a bounded, opt-in compatibility check for public
Homedy sale listings. It uses the existing Processor's deterministic normalizer
and validator, but **never publishes to Kafka, connects to MongoDB or trains**.
It does not replace `auto_scrape.py` or resolve Batdongsan's HTTP 403.

Run this trial only while scheduled crawling for Homedy is stopped, so the
combined request rate stays within the configured source pacing. Restore the
previous scheduler state after the trial. From the repository root, with Python
dependencies installed:

```bash
python -m scraper.homedy_scraper --limit 5 --delay 3
```

Or use the existing scraper image from PowerShell (Docker Engine required):

```powershell
docker compose build scraper
docker compose run --rm --no-deps --volume "${PWD}/runtime:/app/runtime" scraper python -m scraper.homedy_scraper --limit 5 --delay 3
```

Each invocation creates `runtime/source_trials/homedy-<timestamp>-<id>/` with
`records.jsonl`, `validation.jsonl`, `robots.txt` and `report.json` when access
allows collection. This directory is already excluded from Git and Docker
build context. Source descriptions can contain contact details: keep these
local files private, not in commits or shared logs.

The trial requests one index page and at most 1–10 sale details, uses a truthful
crawler User-Agent, checks robots rules and spaces requests by 2–30 seconds
(default 3). It does not bypass challenges, follow redirects or immediately
retry 403/429 responses. A detail exceeding 8 MB of decompressed HTML or the
response deadline is recorded as rejected; access failures stop the run.
Exit 0 means all requested records passed compatibility checks; exit 1 means
`needs_review` or `failed`, with partial output preserved.

**Observed on 2026-09-10:** the five-detail Docker trial produced one usable
real record; four sequential responses hit the size guard. Isolated retrieval
of one affected URL returned about 605 KB, so the sequential-response problem
is unresolved; this is **not** evidence that bulk crawling is ready. See
[the trial evidence](docs/PROJECT_STATUS.md#homedy-source-trial--2026-09-10).

Records retain `source=homedy`, `is_synthetic=false`, source URL/ID and scrape
time, with `verified=0`. These are asking prices in advertisements, not verified
transaction prices. `training_approved=false` in the report is an operator
notice, not a primary-trainer filter: **do not replay these trial records into
the production topic or manually train on the JSONL**. Local-only output is the
isolation boundary. Review source terms, collection/reuse permission and data
quality before any bulk-ingestion integration; HTTP 200/robots access is not a
data licence. This old trial command remains local-only; the separate new
`sources/homedy.py` adapter integrates with the opt-in canonical Kafka producer.

## API

`GET /health` is public. Authentication uses bearer tokens and the file-backed
user store `artifacts/auth/users.json`.

| Method | Path | Access |
| --- | --- | --- |
| POST | `/auth/register` | Public; first account becomes admin |
| POST | `/auth/login` | Public |
| GET | `/auth/me`, `/auth/roles` | Authenticated |
| GET/PATCH | `/auth/users*` | Admin |
| GET | `/model/info` | Manager/admin |
| POST | `/predict` | User/manager/admin |
| POST | `/predict/batch` | Manager/admin |

Example:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"xample.com","password":"StrongPass1","full_name":"Admin"}'

curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass1"}'
```

Use the returned token for prediction and model-info requests. Set a strong
`AUTH_SECRET_KEY` outside local development.

## Data and model

The processor parses localized price/area values, validates records, computes
derived fields, adds deterministic local text/optional geographic enrichment,
and writes MongoDB collections. Invalid records go to `invalid_records`; failed
raw/database paths may go to `dlq_raw`. The clean Kafka topic
`real_estate_features` is an audit/extension feed with no in-repository consumer.

The trainer uses non-synthetic candidates from `real_estate_db.training_features`
and a train/test split with
an sklearn preprocessing pipeline and Ridge, HistGradientBoosting and SGD voting
regressors over a log-transformed target. It writes versioned joblib/metadata and
the stable model copy under `artifacts/models/`.

## Development checks

```bash
python -m compileall -q .
pytest -q
cd frontend && npm.cmd run build
```

The frontend package has `dev`, `build`, `preview`, `lint` and `type-check`
scripts, but no test script or ESLint config. Use `npm.cmd` on PowerShell when
the `npm.ps1` execution policy is restricted.

## Documentation index

- [Implemented architecture](docs/ARCHITECTURE.md)
- [Deployment](DEPLOYMENT.md)
- [Quick reference](docs/QUICK_REFERENCE.md)
- [Runtime runbook](docs/RUNBOOK.md)
- [Data schema](docs/DATA_SCHEMA.md)
- [Monitoring](docs/MONITORING_AND_ALERTING.md)
- [Product requirements](docs/REQUIREMENTS.md) (targets, not current guarantees)
- [Acceptance criteria](docs/ACCEPTANCE_CRITERIA.md) (quality targets)
- [Documentation index](docs/README.md)
