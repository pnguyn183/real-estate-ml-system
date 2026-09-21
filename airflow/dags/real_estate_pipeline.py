"""Airflow orchestration for the real-data crawl and model-training path.

The DAG is intentionally thin: crawler policy, Kafka delivery and data
validation remain owned by the application modules. Airflow owns scheduling,
retry/backoff and task-level observability.
"""

from datetime import datetime, timedelta
import os
import subprocess

from airflow import DAG
from airflow.exceptions import AirflowException, AirflowFailException, AirflowSkipException
from airflow.operators.python import PythonOperator


def run_application(arguments):
    """Do not retry access denials; propagate other task errors to Airflow."""
    result = subprocess.run(
        [os.environ.get("APPLICATION_PYTHON", "/opt/project-venv/bin/python"), *arguments],
        cwd=os.environ.get("APPLICATION_ROOT", "/opt/airflow/project"),
        check=False,
    )
    if result.returncode == 20:
        raise AirflowFailException("Source access denied; task retries are disabled for this failure")
    if result.returncode == 99:
        raise AirflowSkipException("Insufficient eligible real training data")
    if result.returncode:
        raise AirflowException(f"Application task exited with code {result.returncode}")


def run_crawler():
    if os.environ.get("CRAWL_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise AirflowSkipException("Real crawling disabled; no source requests or downstream training")
    run_application([
        "scraper/kafka_producer.py",
        "--sources", os.environ.get("ENABLED_SOURCES", "alonhadat,homedy"),
        "--limit", os.environ.get("SCRAPE_LIMIT", "10"),
        "--max-pages", os.environ.get("SCRAPE_MAX_PAGES", "1"),
        "--request-delay", os.environ.get("SCRAPE_REQUEST_DELAY", "2"),
        "--detail-delay", os.environ.get("SCRAPE_DETAIL_DELAY", "2"),
        "--delay-min", os.environ.get("SCRAPE_DELAY_MIN", "2"),
        "--delay-max", os.environ.get("SCRAPE_DELAY_MAX", "5"),
        "--state-file", os.environ.get("SCRAPE_STATE_FILE", "runtime/scrape_state/airflow_state.json"),
    ])


with DAG(
    dag_id="real_estate_pipeline",
    description="Polite real-data crawl, Kafka ingestion and price-model training",
    start_date=datetime(2026, 1, 1),
    schedule="@hourly",
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "real-estate-pipeline",
        "depends_on_past": False,
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=30),
    },
    tags=["real-data", "kafka", "training"],
) as dag:
    crawl_real_data = PythonOperator(
        task_id="crawl_real_data",
        python_callable=run_crawler,
        execution_timeout=timedelta(hours=2),
    )

    wait_for_processors = PythonOperator(
        task_id="wait_for_processors",
        python_callable=run_application,
        op_args=[["airflow/runtime_checks.py", "wait-for-processing"]],
        execution_timeout=timedelta(minutes=15),
    )

    train_price_model = PythonOperator(
        task_id="train_price_model",
        python_callable=run_application,
        op_args=[["airflow/runtime_checks.py", "train"]],
        execution_timeout=timedelta(hours=1),
    )

    crawl_real_data >> wait_for_processors >> train_price_model
