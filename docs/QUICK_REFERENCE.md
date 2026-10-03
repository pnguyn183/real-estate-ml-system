# Quick reference

For the implementation map, read [ARCHITECTURE.md](ARCHITECTURE.md). The
commands below use the current Compose service names and ports.

## Start

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
```

## Service endpoints

| Service | URL | Notes |
| --- | --- | --- |
| Frontend | `http://localhost:3000` | React/Nginx UI |
| FastAPI | `http://localhost:8000` | Authenticated API |
| OpenAPI docs | `http://localhost:8000/docs` | Swagger UI |
| Legacy predictor | `http://localhost:8002` | Host-published, unauthenticated; restrict network access |
| Processor 1 metrics | `http://localhost:8003/metrics` | Prometheus endpoint |
| Processor 2 metrics | `http://localhost:8004/metrics` | Same consumer group |
| Processor 3 metrics | `http://localhost:8005/metrics` | Same consumer group |
| Trainer metrics | `http://localhost:8001/metrics` | Prometheus endpoint |
| AI health/metrics | `ai-agent:8006/health`, `/metrics` | Compose network only; disabled by default |
| Stress health/metrics | `stress-agent:8007/health`, `/metrics` | Compose network only; disabled by default |
| Mongo Express | `http://localhost:8081` | Basic auth UI |
| Prometheus | `http://localhost:9090` | Targets/alerts/PromQL |
| Grafana | `http://localhost:3001` | Provisioned dashboard |

## Authentication

`GET /health` is public. Register/login returns a bearer token; users are stored
in `artifacts/auth/users.json`.

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass1","full_name":"Admin"}'

curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass1"}'
```

Role access:

| Role | Endpoints |
| --- | --- |
| `user` | `POST /predict` |
| `manager` | user access + `GET /model/info` + `POST /predict/batch` |
| `admin` | all above + `/auth/users*` administration |

## Prediction payload

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "area_m2": 100,
    "bedroom_count": 2,
    "bathroom_count": 1,
    "floor_count": 1,
    "property_type": "apartment",
    "province_slug": "ha-noi",
    "district_slug": "dong-da"
  }'
```

## Environment

| Area | Variables |
| --- | --- |
| API | `API_HOST`, `API_PORT`, `CORS_ORIGINS`, `AUTH_SECRET_KEY`, `AUTH_USERS_PATH`, `AUTH_TOKEN_EXPIRE_MINUTES`, `MODEL_PATH` |
| MongoDB | `MONGO_URI`, `MONGO_DB` |
| Kafka | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_RAW_TOPIC`, `KAFKA_CLEAN_TOPIC`, `KAFKA_GROUP_ID` |
| Scraper | `SCRAPE_*` and `SCRAPE_INITIAL_*` limits, pages, timeout, delays and state files |
| Trainer | `TRAIN_INTERVAL`, `TRAIN_RETRY_INTERVAL`, `MIN_RECORDS_FOR_TRAINING`, `PRICE_ANOMALY_TRAINING_POLICY` |
| AI enablement | `AI_ENABLED`, `AI_FALLBACK_ENABLED`, `AI_STRESS_ENABLED` (all default `false`) |
| External provider | `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` |
| AI budgets | `AI_RATE_LIMIT_PER_MINUTE`, `AI_MAX_CONCURRENT_REQUESTS`, `AI_TOTAL_BUDGET_SECONDS`, `AI_MIN_CONFIDENCE`, `AI_CIRCUIT_*` |
| Agent routing | `KAFKA_AI_TOPIC`, `KAFKA_AI_RESULT_TOPIC`, `KAFKA_AI_DLQ_TOPIC`, `KAFKA_AI_GROUP_ID`, `KAFKA_STRESS_TOPIC`, `MONGO_STRESS_DB` |
| Stress generation | `STRESS_ENABLED`, `STRESS_SCENARIO`, `STRESS_RUN_ID`, `STRESS_RATE_PER_SECOND`, `STRESS_MULTIPLIER`, `STRESS_DURATION_SECONDS`, `STRESS_MAX_RECORDS`, `STRESS_UNSTRUCTURED_RATIO`, `STRESS_DUPLICATE_RATIO` |

Inside Compose, use `kafka:29092,kafka2:29093,kafka3:29094` and
`mongodb:27017`; from the host use `localhost:9092,localhost:9093,localhost:9094`
and `localhost:27017`.

The table names application settings; Compose passes fixed values for some
settings and interpolates others. Changing a fixed topic/database in `.env`
alone does not change its Compose value. Keep credentials only in ignored
`.env`, never publish resolved Compose output with secrets, and use
`docker compose config --quiet` for a secret-safe syntax check. See
[deployment configuration](../DEPLOYMENT.md#optional-agent-configuration).

## Kafka operations

`kafka-init` provisions seven application topics, each with three partitions,
replication factor three and minimum ISR two:

| Topic | Producer | Consumer |
| --- | --- | --- |
| `real_estate_raw` | Website scraper | Three Processors |
| `real_estate_features` | Real Processor branch | None; best-effort audit |
| `real_estate_stress_raw` | Stress generator | Same three Processors |
| `real_estate_stress_features` | Stress Processor branch | None; best-effort audit |
| `real_estate_ai_input` | Processor fallback | AI worker |
| `real_estate_ai_results` | AI worker | Same three Processors/result handler |
| `real_estate_ai_dlq` | AI worker or result handler | No automatic replay consumer |

Inspect the live topology and group assignment with:

```bash
docker compose exec kafka kafka-topics --bootstrap-server kafka:29092 --describe --topic real_estate_raw
docker compose exec kafka kafka-topics --bootstrap-server kafka:29092 --describe --topic real_estate_features
docker compose exec kafka kafka-topics --bootstrap-server kafka:29092 --describe --topic 'real_estate_.*'
docker compose exec kafka kafka-consumer-groups --bootstrap-server kafka:29092 --describe --group real_estate_training_pipeline --members --verbose
docker compose exec kafka kafka-consumer-groups --bootstrap-server kafka:29092 --describe --group real_estate_ai_extraction
```

The three `processor*` services deliberately share `real_estate_training_pipeline`
and subscribe to raw, stress raw and AI results: nine partitions in total.
The typical three-member assignment is one partition per topic per worker;
ownership is dynamic and changes on a rebalance. The separate AI group consumes
only the three AI-input partitions. With AI disabled there are no active AI
members (the group may be absent or retain offsets from an earlier run).

Normal structured records never need the LLM. Stress records remain tagged and
go to `real_estate_stress_db`; primary Mongo queries and model/JSON training
also reject synthetic markers. Read [the runbook](RUNBOOK.md) before enabling
AI routing or running the bounded mock integration test.

## Validation and troubleshooting

```bash
python -m compileall -q .
pytest -q
cd frontend && npm.cmd run build
docker compose logs --tail=200 <service>
```

`401` means missing/invalid/expired token; `403` means insufficient role; `503`
from prediction generally means the model artifact is missing or cannot load.
Use `docker compose ps`, `/health`, `/metrics` and Prometheus `/targets` before
restarting services. Use `docker compose down -v` only when intentionally
deleting the Mongo volume.
