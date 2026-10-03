# Project guide and navigation

Start with [`ARCHITECTURE.md`](ARCHITECTURE.md) for the implementation-first
topology, data lineage, Compose dependencies and known mismatches. This guide
points to the code and operational references for each role.

## By role

### Product / QA

1. [`PROJECT_STATUS.md`](PROJECT_STATUS.md) - observed runtime state and limits.
2. [`REQUIREMENTS.md`](REQUIREMENTS.md) - product goals mapped to implementation.
3. [`ACCEPTANCE_CRITERIA.md`](ACCEPTANCE_CRITERIA.md) - checks versus targets.
4. [`METRICS_AND_SLA.md`](METRICS_AND_SLA.md) - exported metrics and target values.

### Backend / data / ML

1. [`QUICK_REFERENCE.md`](QUICK_REFERENCE.md) - commands, auth and API payloads.
2. [`../processing/kafka_to_mongo.py`](../processing/kafka_to_mongo.py) - ingestion, normalization and Mongo writes.
3. [`../processing/price_anomaly.py`](../processing/price_anomaly.py) - contextual price review.
4. [`../processing/feature_engineering.py`](../processing/feature_engineering.py) and [`../processing/text_enrichment.py`](../processing/text_enrichment.py) - deterministic enrichment.
5. [`../modeling/price_model.py`](../modeling/price_model.py) - feature frame, training and inference.
6. [`../modeling/train_model.py`](../modeling/train_model.py) / [`../scripts/auto_train.py`](../scripts/auto_train.py) - manual and scheduled training.
7. [`DATA_SCHEMA.md`](DATA_SCHEMA.md) and [`ML_PIPELINE_AUDIT.md`](ML_PIPELINE_AUDIT.md) - detailed contracts.
8. [`../agents/worker.py`](../agents/worker.py), [`../agents/results.py`](../agents/results.py) - AI request consumer and Processor result handler.
9. [`../agents/extraction.py`](../agents/extraction.py), [`../agents/providers.py`](../agents/providers.py) - strict extraction and replaceable external-API transport.
10. [`../agents/stress.py`](../agents/stress.py), [`../agents/generator.py`](../agents/generator.py), [`../agents/safety.py`](../agents/safety.py) - bounded synthetic generation and shared training boundary.

### DevOps / operations

1. [`../docker-compose.yml`](../docker-compose.yml) - canonical 19-service stack (three Kafka brokers, three processor workers, optional AI/stress agents).
2. [`../DEPLOYMENT.md`](../DEPLOYMENT.md) - build, deploy and recovery.
3. [`RUNBOOK.md`](RUNBOOK.md) - operator checks and incident response.
4. [`MONITORING_AND_ALERTING.md`](MONITORING_AND_ALERTING.md) and [`../monitoring/`](../monitoring/) - Prometheus/Grafana.
5. [`AUTOMATION.md`](AUTOMATION.md) - scheduled worker behavior.

### Frontend

1. [`../frontend/README.md`](../frontend/README.md) - local development and API integration.
2. [`../frontend/src/App.tsx`](../frontend/src/App.tsx) - app shell and role-aware rendering.
3. [`../frontend/src/api/client.ts`](../frontend/src/api/client.ts) - Axios client and token storage.
4. [`../frontend/src/components/`](../frontend/src/components/) - auth, prediction, model and admin UI.

## Repository map

```text
real-estate-ml-system/
├── modeling/
│   ├── api.py, auth.py
│   ├── price_model.py
│   ├── predict_price.py, predict_service.py
│   └── train_model.py
├── processing/
│   ├── kafka_to_mongo.py
│   ├── feature_engineering.py, text_enrichment.py
│   ├── price_anomaly.py, llm_review.py
│   └── export_training_dataset.py, dataset_quality_report.py
├── scraper/
│   ├── listing_feature_scraper.py
│   └── kafka_producer.py
├── scripts/
│   ├── auto_scrape.py, auto_train.py, health_check.py
│   └── start_all.sh, stop_all.sh
├── frontend/src/
├── monitoring/
├── utils/tests/
├── docker-compose.yml, Dockerfile*
└── docs/
```

The two export/report scripts are manual utilities; Compose selects the
scheduled workers. The generic `modeling/Dockerfile` is retained as a possible
manual image entrypoint but is not selected by Compose.
`agents/` contains the new worker, result handler, provider/validation code,
stress generator and shared synthetic safety. `scripts/init_kafka_cluster.sh`
provisions all seven application topics. `scripts/verify_agent_pipeline.py` is
a bounded Kafka/Mongo smoke using a test-only simulated LLM transport, not a
production provider or evidence that a live external API works.

## Three data paths

- Normal: website → scraper → `real_estate_raw` → deterministic Processor →
  `real_estate_db.training_features` → trainer/model → serving/frontend.
- AI fallback: missing required parseable fields plus extractable text →
  `real_estate_ai_input` → external-API worker → `real_estate_ai_results` →
  Processor validation → origin-selected MongoDB storage/review.
- Stress: deterministic templates → `real_estate_stress_raw` → the same
  Processor team → `real_estate_stress_db`; explicit tags survive extraction.
  Mongo queries, manual/JSON training, model training and export reject synthetic
  records. The shared workers/brokers still expose real ingestion to test load.

The agents and routing are off by default. See
[deployment configuration](../DEPLOYMENT.md#optional-agent-configuration).

## Public host endpoints

| Component | Port | Purpose |
| --- | ---: | --- |
| Frontend | 3000 | React/Nginx UI |
| FastAPI | 8000 | Authenticated API and `/docs` |
| Legacy predictor | 8002 | Host-published, unauthenticated; restrict network access |
| Trainer metrics | 8001 | Prometheus endpoint |
| Processor 1 metrics | 8003 | Prometheus endpoint |
| Processor 2 metrics | 8004 | Same Kafka consumer group |
| Processor 3 metrics | 8005 | Same Kafka consumer group |
| Prometheus | 9090 | Metrics, targets and alerts |
| Grafana | 3001 | Dashboard |
| Mongo Express | 8081 | Basic-auth database UI |
| MongoDB | 27017 | Database |
| Kafka broker 1 | 9092 | Host listener; internal `kafka:29092` |
| Kafka broker 2 | 9093 | Host listener; internal `kafka2:29093` |
| Kafka broker 3 | 9094 | Host listener; internal `kafka3:29094` |
| ZooKeeper | 2181 | Kafka coordination |

AI health/metrics `ai-agent:8006` and stress health/metrics `stress-agent:8007`
are internal-only; they are not additional public endpoints.

## API access summary

| Endpoint | Access |
| --- | --- |
| `GET /health`, `POST /auth/register`, `POST /auth/login` | Public |
| `GET /auth/me`, `GET /auth/roles` | Authenticated |
| `POST /predict` | `user`, `manager`, `admin` |
| `GET /model/info`, `POST /predict/batch` | `manager`, `admin` |
| `/auth/users*` | `admin` |

## Common commands

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
docker compose logs --tail=200 <service>
curl http://localhost:8000/health
python scripts/health_check.py
```

For Python validation run `python -m compileall -q .` and `pytest -q`; for the
frontend run `npm.cmd run build` from `frontend/` on PowerShell. The frontend
has no test script and no ESLint configuration. The supported deployment is a
single-host Compose stack; no Spark, data lake, Kubernetes, ELK, Jaeger,
Alertmanager or CI/CD system is part of this repository.

Current runtime verification and environment limitations are recorded separately
in [`PROJECT_STATUS.md`](PROJECT_STATUS.md).
