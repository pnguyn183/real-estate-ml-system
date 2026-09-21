# Runtime scripts

The scripts are thin entrypoints around the services described in
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).

| Script | Behavior |
| --- | --- |
| `auto_scrape.py` | Run initial scraper once, sleep, then run periodic scraper forever |
| `auto_train.py` | Check candidates, train when the threshold is met, retry and repeat forever |
| `health_check.py` | Probe frontend/API/Prometheus/Grafana/processor/trainer plus MongoDB |
| `start_all.sh` | Bash wrapper that exports local defaults, runs Compose and restarts scraper |
| `stop_all.sh` | Runs `docker compose down` |
| `init_kafka_cluster.sh` | One-shot Compose provisioner for seven application topics, each 3 partitions / RF3 / minimum ISR2 |
| `verify_agent_pipeline.py` | Bounded Kafka/Mongo smoke with an injected simulated LLM transport; not a live external API test |

## Commands

```bash
bash scripts/start_all.sh
python scripts/health_check.py
bash scripts/stop_all.sh
```

The shell helpers are intended for Bash environments. `start_all.sh` sets
`MIN_RECORDS_FOR_TRAINING=30`, so it deliberately overrides the direct Compose
default of 3,000 for a small local run. On Windows, use `docker compose up -d`
and `docker compose down` directly.

## Compose defaults

The canonical defaults are in `docker-compose.yml`: periodic scraper and train
checks every 1,800 seconds; initial scraper limit 5,000 and max 200 pages; and
trainer minimum 3,000 model candidates. Override with `.env` or exported
variables before `docker compose up`. The checked-in `.env.example` and
`start_all.sh` use 30 candidates for small local runs, which overrides 3,000
when applied.

Common variables:

```bash
KAFKA_BOOTSTRAP_SERVERS=localhost:9092,localhost:9093,localhost:9094
MONGO_URI=mongodb://localhost:27017/
MONGO_DB=real_estate_db
SCRAPE_LIMIT=300
SCRAPE_MAX_PAGES=5
SCRAPE_INTERVAL=1800
SCRAPE_TIMEOUT=300
TRAIN_INTERVAL=1800
TRAIN_RETRY_INTERVAL=60
MIN_RECORDS_FOR_TRAINING=3000
```

Inside Compose, Kafka is `kafka:29092,kafka2:29093,kafka3:29094` and MongoDB is
`mongodb:27017`. `kafka-init` provisions seven application topics with three
partitions, RF3 and minimum ISR2 before `scraper`, agents or any processor worker starts. The
`processor`, `processor-2` and `processor-3` services share the
`real_estate_training_pipeline` group and subscribe to raw, stress raw and AI
results (nine partitions total). The optional AI worker separately consumes
`real_estate_ai_input` in `real_estate_ai_extraction` and publishes results for
the Processors; it does not directly write training features. Both agents are
disabled by default. Synthetic data uses `real_estate_stress_db` and is excluded
again by primary Mongo/model/JSON training and export filters.

See [the runtime runbook](../docs/RUNBOOK.md#optional-ai-and-stress-verification)
for the smoke test's strict idle-queue prerequisites and cleanup/flag restore.
It leaves tagged test documents in the stress database for inspection, never
calls a live LLM and must not be reported as successful live scraping.

## Logs

```bash
docker compose logs -f scraper
docker compose logs -f processor
docker compose logs -f processor-2 processor-3
docker compose logs -f trainer
```
