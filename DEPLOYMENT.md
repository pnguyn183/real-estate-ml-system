# Deployment guide

The supported deployment in this repository is a local/single-host Docker
Compose stack. Source-of-truth service topology is documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). There are no Kubernetes manifests,
systemd units, PostgreSQL services or cloud deployment files in this checkout.

## Prerequisites

- Docker Desktop/Engine with Compose v2
- Python 3.11+ for local scripts/tests
- Node.js 18+ and npm for frontend development
- Network access to `batdongsan.com.vn` when running the scraper

## Compose deployment

An `.env` file is optional. AI extraction, AI routing and synthetic generation
default to disabled and require no API key. Validate and start:

```bash
docker compose config --quiet
docker compose build --no-cache
docker compose up -d
docker compose ps
```

The Compose file starts ZooKeeper, three Kafka brokers, the one-shot
`kafka-init` topic/replica provisioner, MongoDB, mongo-express, Prometheus,
Grafana, three Processor workers, trainer, API, scraper, frontend, the
legacy predictor, `ai-agent` and `stress-agent`: 19 services including the
one-shot initializer. Application images use the repository root as build context
except the frontend image, which uses `frontend/`.

The named volume `mongo_data` persists MongoDB data. Bind-mounted
`./artifacts` supplies model and auth files to trainer, API and predictor.
`./runtime/stress` retains bounded stress-run manifests, ground truth and reports.
Kafka brokers have no persistent volumes: container replacement can lose topic
data and committed offsets. Replication across three containers on one host is
not a host-level backup.

## Optional agent configuration

Put credentials only in the ignored root `.env`; never commit them, paste them
into commands, publish resolved `docker compose config` output containing them,
or add them to documentation. Only the AI service receives the LLM credential.
The names below are the implemented variables (not `AI_PROVIDER`/`AI_API_KEY`):

```dotenv
AI_ENABLED=true
AI_FALLBACK_ENABLED=true
AI_STRESS_ENABLED=false
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=<JSON-capable-model-enabled-in-your-provider-account>
LLM_API_KEY=<your-private-provider-key>
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=3
AI_RATE_LIMIT_PER_MINUTE=20
AI_MAX_CONCURRENT_REQUESTS=1
AI_TOTAL_BUDGET_SECONDS=120
```

The transport is configurable external HTTPS chat-completions JSON mode; Groq
is the Compose default endpoint, not a hard-coded business dependency. Model
availability, account access and free-tier quota must be checked with the
provider. No model is selected automatically and no local LLM/GPU is used.
`AI_ENABLED` enables the consumer; `AI_FALLBACK_ENABLED` enables real-record
routing. `AI_STRESS_ENABLED` is a separate opt-in for synthetic API usage.
Enabling routing while the worker is disabled leaves requests queued.

```bash
docker compose config --quiet
docker compose up -d ai-agent processor processor-2 processor-3
```

Each AI worker handles records sequentially. Optional `--scale ai-agent=2`
creates independent consumers in one group, with at most three active consumers
for three request partitions. Rate/concurrency budgets and circuit breakers are
process-local: divide the account quota among replicas before scaling.

For a bounded stress-only run, keep all AI flags false and configure `.env`:

```dotenv
STRESS_ENABLED=true
STRESS_RUN_ID=local-check-001
STRESS_SCENARIO=mixed
STRESS_MULTIPLIER=1
STRESS_RATE_PER_SECOND=1
STRESS_DURATION_SECONDS=60
STRESS_MAX_RECORDS=60
STRESS_UNSTRUCTURED_RATIO=0.30
STRESS_DUPLICATE_RATIO=0.10
```

```bash
docker compose up -d stress-agent
docker compose logs --tail=100 stress-agent
```

Use a new run ID for each intentional run; claimed IDs do not restart
automatically after a process failure. Effective non-burst rate is
`STRESS_RATE_PER_SECOND * STRESS_MULTIPLIER * profile factor` (normal=1,
medium=5, high=10), bounded by `STRESS_MAX_RATE`. Both time and record limits
stop generation. Scenarios and all settings are in `.env.example` and
`agents/generator.py`; sources are built-in seeds or a bounded Mongo snapshot.
Outputs use `real_estate_stress_raw` and `real_estate_stress_db`; the primary
trainer has no synthetic opt-in. Set `STRESS_ENABLED=false` after the run.

## Verification

```bash
curl http://localhost:8000/health
curl http://localhost:3000/
curl http://localhost:8003/metrics
curl http://localhost:8004/metrics
curl http://localhost:8005/metrics
curl http://localhost:8001/metrics
curl http://localhost:8002/health
curl http://localhost:9090/-/healthy
curl http://localhost:3001/api/health
docker compose exec -T ai-agent curl -fsS http://localhost:8006/health
docker compose exec -T stress-agent curl -fsS http://localhost:8007/health
docker compose logs --tail=200
```

`mongo-express` uses basic authentication. The legacy predictor on port 8002 is
unauthenticated and must not be exposed publicly.
Disabled AI health should return `{"alive": true, "enabled": false}`; this
does not prove Kafka connectivity or external extraction. See
[PROJECT_STATUS](docs/PROJECT_STATUS.md) for actual verification results.

## Local development without Compose

Install Python dependencies and run the API/frontend separately. Kafka and
MongoDB still need to be available at the configured host endpoints.

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
python -m pip install -r requirements.txt

python -m uvicorn modeling.api:app --host 0.0.0.0 --port 8000 --reload
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

For a manual pipeline, run the scraper producer, processor and trainer scripts
from the repository root with
`KAFKA_BOOTSTRAP_SERVERS=localhost:9092,localhost:9093,localhost:9094` and
`MONGO_URI=mongodb://localhost:27017/`. The Compose workers are the canonical
scheduled entrypoints.

## Configuration

Compose defaults include Kafka `kafka:29092,kafka2:29093,kafka3:29094` (host
listeners `localhost:9092,localhost:9093,localhost:9094`), MongoDB
`mongodb:27017`, API port 8000, frontend port 3000, processor metrics ports
8003/8004/8005 and trainer metrics port 8001, periodic
scrape/train checks every 1800 seconds, initial scrape limit 10/max 1 page per
source, and minimum training candidates 3000. Crawling defaults to disabled;
when enabled, initial and periodic runs preserve the shared checkpoint and
refresh acknowledged URLs after 86400 seconds by default. See `.env.example` and
[docs/QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md) for all variable names.

Set a strong `AUTH_SECRET_KEY`, production CORS origins and database credentials
before any non-local deployment. The current Compose file intentionally has no
MongoDB authentication, TLS termination, backup controller or alert receiver.

## Build and release checks

```bash
python -m compileall -q .
pytest -q
cd frontend && npm.cmd run build
cd ..
docker compose config --quiet
docker compose build --no-cache
```

The frontend has no test script and no ESLint configuration. Dependency versions
are pinned by the existing lockfiles; do not upgrade them as part of deployment
without a separate compatibility review.

## Stop, backup and recovery

```bash
docker compose down       # stop containers, keep mongo_data
docker compose down -v    # destructive: delete mongo_data
```

Use `mongodump`/`mongorestore` against `localhost:27017` for an operator-managed
Mongo backup. Kafka offsets are committed manually by the processor and a
checkpoint is kept in `offset_checkpoint`; replay requires Kafka tooling and is
not automated by this repository. Model artifacts are versioned by the trainer
under `artifacts/models/`, with the stable `price_model.joblib` copy used by
serving.

## Troubleshooting

```bash
docker compose ps
docker compose logs --tail=200 <service>
```

- API `503` on prediction: check `/health` and `artifacts/models/price_model.joblib`.
- API `401`/`403`: sign in again or use an account with the required role.
- Processor issues: inspect `invalid_records`, `dlq_raw`, `offset_checkpoint` and
  Kafka consumer lag. AI requests/results use `real_estate_ai_input` and
  `real_estate_ai_results`. Malformed AI requests use `real_estate_ai_dlq`;
  malformed result envelopes are archived in Mongo `dlq_raw`. Terminal
  extraction failures return through the result topic to Processor
  `invalid_records`/`ai_failures` plus acknowledged `real_estate_ai_dlq` handling.
  No automatic DLQ replay is implemented.
- AI is enabled but idle: verify both the worker flag and the relevant routing
  flag, queue lag, external HTTPS base URL, model and account credentials. Never
  include credential values in a bug report.
- Frontend issues: check API `/health`, browser `VITE_API_URL` and CORS origins.
- Mongo/Kafka startup: verify Compose healthchecks and service-name endpoints;
  MongoDB also advertises the `mongo` alias for mongo-express.
