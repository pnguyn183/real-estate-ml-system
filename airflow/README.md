# Airflow orchestration

The `real_estate_pipeline` DAG owns scheduling and task retries for the real
data path. Its default source is the bounded Homedy sale category:

`https://homedy.com/ban-nha-dat`

```text
Airflow crawl task -> Kafka real_estate_raw -> parallel Processors -> MongoDB
                   -> wait for committed raw offsets -> train -> model artifact
```

Crawler policy, robots checks, bounded retry/backoff and Kafka delivery remain
implemented in the application code. Airflow does not bypass a 403 response.
The crawler's access-denied exit code `20` makes the task fail **without task
retries**. Other task failures have at most two retries, starting at five
minutes with exponential backoff capped at thirty minutes. If access is denied,
pause the DAG until source access has been resolved; a scheduled future run is
separate from a retry.

The offset task snapshots the raw topic's high watermarks, then waits at most
`AIRFLOW_PROCESSING_WAIT_SECONDS` (default 600 seconds) for the existing
Processor group's committed offsets. It does not subscribe, rebalance workers,
consume records or commit offsets. Newer records do not move its target. This
prevents training from immediately racing ahead of normal Kafka processing.
An acknowledged AI handoff can complete a raw offset, so this barrier **does not
wait for in-flight AI results** and does not guarantee that every raw record was
valid. Training retains the existing real-data/candidate/anomaly filters.

## Local demo

Build/start the optional orchestration profile after the normal stack is
available. Starting the profile does not start a crawl: the DAG is paused on
first creation.

```bash
docker compose --profile orchestration build airflow
docker compose --profile orchestration up -d airflow
docker compose logs airflow
docker compose exec airflow airflow dags list-import-errors
```

Open `http://localhost:8080`. Airflow prints the generated local admin
password in its container logs; do not paste those logs into reports. The DAG
is hourly with one active run and `catchup=False`. The single-container
`airflow standalone` / SQLite / SequentialExecutor setup is for a local demo,
not a production Airflow deployment.

Before deliberately enabling Airflow scheduling, stop the existing automatic
scraper and trainer so two schedulers do not crawl or overwrite model artifacts
concurrently:

```bash
docker compose stop scraper trainer
docker compose exec airflow airflow dags unpause real_estate_pipeline
```

Do not unpause while the source is still blocked. Inspect DAG/task logs and
the task state in the Airflow UI. Too few eligible real training records makes
the training task `SKIPPED`, not a successful model-training claim; a database
connection failure fails the task. To switch back:

```bash
docker compose exec airflow airflow dags pause real_estate_pipeline
# Wait for any active DAG run to finish before restarting the other schedulers.
docker compose start scraper trainer
```

Tasks use the separate application interpreter `/opt/project-venv/bin/python`
and explicit working directory `/opt/airflow/project`. Dependencies are installed
at image build time, not every container start. Source code is mounted read-only;
`runtime/` and `artifacts/` are separately writable so scrape state, the text
cache, metrics files and model artifacts survive. Airflow metadata/logs have
their own persistent volume. The optional service needs no Docker socket.

The operational dashboard remains Grafana at `http://localhost:3001`. Open the
provisioned dashboard **Agent Operations — AI, Stress & Safety** to demonstrate
AI throughput, confidence distribution, low-confidence rejections, validation
failures, provider retries, input lag and stress-topic activity. Airflow is for
orchestration state; Grafana is for runtime agent and pipeline metrics.
The short-lived Airflow training subprocess does not replace the long-running
trainer's Prometheus endpoint. When the trainer service is deliberately stopped
for this handover, that target is down; model task results are visible in
Airflow logs/UI. Do not claim Airflow training metrics are continuously scraped.
