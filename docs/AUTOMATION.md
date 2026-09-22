# Runtime automation

This page documents the automation that exists in the current checkout. For
the complete topology and data contracts, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Compose lifecycle

`docker compose up -d` starts the 19 services in `docker-compose.yml`, including
three Kafka brokers, the one-shot `kafka-init` provisioner and three parallel
Processor workers and the disabled-by-default AI/stress agents. Existing
long-running services use `restart: always`; the two agents use
`restart: unless-stopped`. Compose waits
for all three healthy Kafka brokers and the completed `kafka-init`, MongoDB,
Prometheus or API dependencies where configured. Zookeeper,
mongo-express do not define healthchecks. Scraper health checks its metrics
server, not website availability or crawl success.

```bash
docker compose config --quiet
docker compose build --no-cache
docker compose up -d
docker compose ps
docker compose logs --tail=200
```

`scripts/start_all.sh` is a Bash convenience wrapper. It exports local
development defaults, starts Compose, restarts the scraper and prints URLs; it
is not a separate scheduler or orchestration system. The wrapper sets
`MIN_RECORDS_FOR_TRAINING=30`, overriding the direct Compose default of 3,000
for a small local run. `scripts/stop_all.sh`
executes `docker compose down` (without removing the named Mongo volume).

## Airflow orchestration

The preferred scheduled path is the optional `real_estate_pipeline` Airflow
DAG in `airflow/dags/real_estate_pipeline.py`. It starts paused. When explicitly
enabled, it runs the multi-source crawler hourly, waits for a snapshot of raw
Kafka offsets to be committed, then trains. This barrier does not await pending
AI extractions. Operational failures retry with backoff; access-denial exit20
fails without task retries. Start it for a local
UI demo with:

```bash
docker compose --profile orchestration up -d airflow
```

Airflow's DAG view, Gantt chart and task logs cover orchestration. Grafana's
provisioned **Agent Operations — AI, Stress & Safety** dashboard covers the
agent-specific runtime view: throughput, confidence, validation/failure
reasons, Kafka lag, provider retries and isolated stress activity.

Sources are configured with `ENABLED_SOURCES=alonhadat,homedy` and
`CRAWL_ENABLED=false` by default. Guland is disabled pending review; Batdongsan
is not an accepted source. Stop legacy scraper AND trainer loops before
unpausing Airflow; do not run two schedulers writing the same artifacts.

## Legacy scheduled workers

- `scripts/auto_scrape.py` remains a compatibility loop for deployments that do
  not run Airflow. It uses the same 2-5 second random request policy, isolates
  each source in a subprocess with its own timeout, and logs failures before
  waiting the configured interval. Failed initial runs do not restart the loop.
- `scripts/auto_train.py` checks MongoDB immediately, trains when the candidate
  count reaches `MIN_RECORDS_FOR_TRAINING` (Compose default 3000), then checks
  again every `TRAIN_INTERVAL` seconds (default 1800). A failed or insufficient
  run retries after `TRAIN_RETRY_INTERVAL` (default 60).

Both workers are persistent loops. They do not exit after one scheduled run;
normal shutdown is handled by their process signal/keyboard paths and Compose.

## Health and monitoring

`scripts/health_check.py` checks the frontend, API, Prometheus, Grafana,
Processor 1 metrics, trainer metrics and MongoDB. It does not probe Kafka,
Zookeeper, scraper or the legacy predictor. Prometheus scrapes
`processor:8003`, `processor-2:8004`, `processor-3:8005`, `trainer:8001`,
DNS-discovered `ai-agent:8006`, `stress-agent:8007`, `scraper:8008` and `localhost:9090`
every 15 seconds.

Processor and trainer expose Prometheus metrics through `utils/metrics.py`.
Agents use `agents/metrics.py`, `agents/stress_metrics.py` and worker-specific
metrics. `health_check.py` does not probe Processors 2/3 or agents;
check those separately with the commands in `RUNBOOK.md`.
There is no ELK, Jaeger, Alertmanager, PagerDuty or Slack integration in this
repository.

## Tests

```bash
pytest -q
cd frontend && npm.cmd run build
```

The frontend has no `test` script and no ESLint configuration; `npm run lint`
is therefore not a configured validation gate.

The checked-in `.env.example` sets `MIN_RECORDS_FOR_TRAINING=30` for small
local experiments. Copying it to `.env` overrides the Compose fallback of
3,000.

## Effective environment defaults

| Variable | Compose default | Used by |
| --- | --- | --- |
| `SCRAPE_INTERVAL` | `1800` seconds | periodic scraper |
| `SCRAPE_INITIAL_LIMIT` | `10` per source | first scraper run |
| `SCRAPE_INITIAL_MAX_PAGES` | `1` per source | first scraper run |
| `SCRAPE_FRESH_START` / `SCRAPE_INITIAL_FRESH_START` | `false` | preserve acknowledged URLs across runs |
| `SCRAPE_REVISIT_SECONDS` | `86400` | refresh previously acknowledged URLs |
| `TRAIN_INTERVAL` | `1800` seconds | trainer |
| `TRAIN_RETRY_INTERVAL` | `60` seconds | trainer |
| `MIN_RECORDS_FOR_TRAINING` | `3000` | trainer gate |
| `PROMETHEUS_METRICS_PORT` | `8001`/`8003`/`8004`/`8005`/`8006`/`8007` | trainer/processor workers/AI/stress (agent ports internal only) |

## Agent lifecycle

`ai-agent` starts `python -m agents.worker`. With `AI_ENABLED=false` it stays
healthy and does not connect a consumer or call a provider. When enabled it
consumes `real_estate_ai_input` in `real_estate_ai_extraction`, persists reusable
extraction state, and waits for an acknowledged `real_estate_ai_results`
publication before committing the request. The three existing Processors consume
results and perform the final deterministic validation/write. Transient provider
retries are bounded; terminal failures return as results for review and DLQ.
There is no independent HTTP extraction server or scheduled AI training task.

`stress-agent` starts `python -m agents.stress`. Generation is opt-in and finite;
after the time/record budget or a previously claimed run ID, it stays idle with
health/metrics available. Run state is bind-mounted under `runtime/stress`.
The same three Processors consume stress input into the isolated stress database.
All primary trainer entrypoints filter synthetic data; no stress trainer exists.
See [optional configuration](../DEPLOYMENT.md#optional-agent-configuration).
