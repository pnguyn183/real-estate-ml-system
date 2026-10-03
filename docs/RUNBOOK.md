# Runtime runbook

This runbook covers the Docker Compose topology in `docker-compose.yml`.
See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full data flow and
[`QUICK_REFERENCE.md`](QUICK_REFERENCE.md) for API payloads.

## Start and inspect

```bash
docker compose config --quiet
docker compose build --no-cache
docker compose up -d
docker compose ps
docker compose logs --tail=200
python scripts/health_check.py
```

`up` starts ZooKeeper, brokers `kafka`/`kafka2`/`kafka3`, waits for all three
healthchecks, runs the one-shot `kafka-init`, and then starts the scraper and
three Processor workers and the disabled-by-default AI/stress services. There
are 19 services including the initializer, which should show `Exited (0)`.
`health_check.py` is a partial helper: it does not probe Kafka, workers 2/3 or
either agent, so its success is not a full-stack verification.

Published endpoints:

- Frontend: `http://localhost:3000`
- FastAPI/OpenAPI: `http://localhost:8000` and `/docs`
- Legacy predictor: `http://localhost:8002/health` (host-published,
  unauthenticated; restrict network access)
- Trainer metrics: `http://localhost:8001/metrics`
- Processor metrics: `http://localhost:8003/metrics`, `:8004/metrics`, `:8005/metrics`
- Kafka brokers: `localhost:9092`, `localhost:9093`, `localhost:9094`
- Mongo Express: `http://localhost:8081`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001`

## Scraper and trainer

The scraper performs an initial crawl and repeats every `SCRAPE_INTERVAL`
(default 1800 seconds). Its resume state is under `runtime/scrape_state/`.
The trainer checks MongoDB immediately, trains when
`MIN_RECORDS_FOR_TRAINING` is met, and retries on `TRAIN_RETRY_INTERVAL`.

```bash
docker compose logs -f scraper
docker compose logs -f trainer
docker compose exec -T trainer python -c "from scripts.auto_train import check_training_data; print(check_training_data())"
```

The helper logs the candidate count using the trainer's actual query/anomaly
policy and prints whether the threshold is met; inspect its log for connection
errors too. It does not train or replace the current model. Every primary
Mongo/model/JSON training entrypoint rejects
synthetic records. The isolated `real_estate_stress_db` is never the primary
trainer source. HTTP 403 from the website is a live-scraping limitation; a
successful replay of stored real records proves only the stored-record path.

## Kafka and Mongo recovery

The three workers use the same `real_estate_training_pipeline` group and manual
synchronous commits. Inspect topic replicas, lag and ownership with:

```bash
docker compose exec -T kafka kafka-topics \
  --bootstrap-server kafka:29092 --describe --topic real_estate_raw
docker compose exec -T kafka kafka-topics \
  --bootstrap-server kafka:29092 --describe --topic 'real_estate_.*'
docker compose exec -T kafka kafka-consumer-groups \
  --bootstrap-server kafka:29092 --describe --group real_estate_training_pipeline --members --verbose
docker compose exec -T kafka kafka-consumer-groups \
  --bootstrap-server kafka:29092 --describe --group real_estate_ai_extraction
docker compose logs --tail=200 processor processor-2 processor-3
```

After persistence, workers commit Kafka offsets and write Mongo
`offset_checkpoint` documents. Invalid records are in `invalid_records`; raw or
database failures may be in `dlq_raw`. Replay is a manual Kafka-tooling
operation; no automated replay script is included. Restarting one worker causes
Kafka to rebalance its partitions to the remaining group members.

Seven application topics are provisioned, each with three partitions, RF3 and
minimum ISR2; [QUICK_REFERENCE.md](QUICK_REFERENCE.md#kafka-operations) lists
their producer/consumer ownership. Processor members share nine subscribed
partitions across raw, stress raw and AI results. The AI group has no active
members while disabled. Kafka broker storage is not volume-mounted; container
replacement can lose logs/offsets despite the three replicas.

## Optional AI and stress verification

The real-record fallback requires `AI_FALLBACK_ENABLED=true`; the external
worker independently requires `AI_ENABLED=true` plus valid `LLM_*` settings.
Only the worker receives the API key. Routing with an inactive worker leaves
requests queued. `AI_STRESS_ENABLED` is a separate opt-in for synthetic API
usage; do not enable it casually for a high-volume run.

```bash
docker compose config --quiet
docker compose exec -T ai-agent curl -fsS http://localhost:8006/health
docker compose exec -T stress-agent curl -fsS http://localhost:8007/health
docker compose logs --tail=200 ai-agent stress-agent processor processor-2 processor-3
```

Disabled AI returns alive/enabled state without requiring a key or joining
Kafka. This checks idle startup, not external extraction. Configuration and
bounded stress recipes are in [DEPLOYMENT.md](../DEPLOYMENT.md#optional-agent-configuration).

`scripts/verify_agent_pipeline.py` exercises real Kafka/Mongo and the real
extraction/result code with an injected, test-only simulated provider. Before
running it, ensure the AI queue is idle with no backlog, `AI_ENABLED=false`,
`STRESS_ENABLED=false`, and set `AI_STRESS_ENABLED=true` on all three Processors.
It refuses another active AI consumer or pre-existing request backlog.

```bash
docker compose up -d processor processor-2 processor-3 ai-agent stress-agent
docker compose exec -T ai-agent python scripts/verify_agent_pipeline.py --timeout 90
```

The smoke uses a unique tagged synthetic run, keeps its documents in the stress
database for inspection, and neither calls a live LLM nor resets offsets. It
checks all three partitions, exact duplicates, cached difficult input, malformed
JSON, bounded timeout retry and review recovery. Afterward restore
`AI_STRESS_ENABLED=false` and recreate the Processors. Never report this as a
real external-provider test or a live website scrape. Actual dated results
belong in [PROJECT_STATUS.md](PROJECT_STATUS.md).

Requests are acknowledged before source commits; the AI worker persists
extraction state and acknowledges results before request commits. Result
Processors validate and persist to the origin-selected database before result
commits. Failed extraction uses `invalid_records`, `ai_failures` and acknowledged
`real_estate_ai_dlq`; malformed requests go directly to that DLQ. No automatic
DLQ replay exists. Inspect cache/receipts before controlled recovery because
unchanged failed requests reuse saved terminal results.

## Monitoring

Prometheus scrapes `processor:8003`, `processor-2:8004`, `processor-3:8005`,
`trainer:8001`, DNS-discovered `ai-agent:8006`, `stress-agent:8007` and itself
every 15 seconds: seven targets with one AI replica. Agent ports are internal
only and remain available while disabled. Grafana reads the provisioned
Prometheus datasource; existing panels/alerts cover Processor/trainer metrics,
while agent series are available in Explore. There is no Alertmanager or
notification receiver.

## Auth and prediction

Register the first account to bootstrap `admin`, then login for a bearer token.
`/health` is public; `/predict` accepts all authenticated roles;
`/model/info` and `/predict/batch` require manager/admin. Users are stored in
`artifacts/auth/users.json`; set a strong `AUTH_SECRET_KEY` outside local use.

## Stop and data safety

```bash
docker compose down       # stop services, preserve mongo_data
docker compose down -v    # destructive: remove the Mongo named volume
```

Do not use `down -v` unless deleting local Mongo data is intentional.
