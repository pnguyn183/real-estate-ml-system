# Documentation index

The implementation and current runtime are documented in
[`ARCHITECTURE.md`](ARCHITECTURE.md). It maps source/configuration for
the Compose topology, data flow, Kafka topics, storage, API, ML serving and
monitoring. Other documents are either operational guides, detailed references,
or product targets; target values are not reported as achieved runtime results.

## Start here

- [`../flow_diagram.md`](../flow_diagram.md) - normal, AI fallback and synthetic-isolation visual flows.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) - implementation/configuration map and technical details.
- [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md) - repository map and component entry points.
- [`QUICK_REFERENCE.md`](QUICK_REFERENCE.md) - ports, endpoints, roles and common commands.
- [`RUNBOOK.md`](RUNBOOK.md) - local Compose startup, checks and recovery.
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) - current operational status and limitations.

## Implementation references

- [`SOURCE_MIGRATION.md`](SOURCE_MIGRATION.md) - three-source audit, current access, canonical v2 contract and migration tests.

- [`DATA_SCHEMA.md`](DATA_SCHEMA.md) - raw/normalized payloads, validation and MongoDB collections.
- [`AUTOMATION.md`](AUTOMATION.md) - scraper/trainer schedules and Compose automation.
- [`ERROR_HANDLING_STRATEGY.md`](ERROR_HANDLING_STRATEGY.md) - implemented failure handling and recovery notes.
- [`MONITORING_AND_ALERTING.md`](MONITORING_AND_ALERTING.md) - Prometheus scrape targets and Grafana alert rules.
- [`DASHBOARD.md`](DASHBOARD.md) - provisioned Grafana panels and queries.
- [`ML_PIPELINE_AUDIT.md`](ML_PIPELINE_AUDIT.md) - model features, training and leakage review.
- [`PRICE_ANOMALY_DETECTION.md`](PRICE_ANOMALY_DETECTION.md) - historical IQR anomaly detector and review metadata.

## Product and quality targets

- [`REQUIREMENTS.md`](REQUIREMENTS.md) - product requirements mapped to their current implementation status.
- [`ACCEPTANCE_CRITERIA.md`](ACCEPTANCE_CRITERIA.md) - executable quality checks and explicit target gates.
- [`METRICS_AND_SLA.md`](METRICS_AND_SLA.md) - exported metrics, alert thresholds and non-binding target values.

These three files preserve the intended product targets, but do not claim that
the current model or deployment has met them. See `PROJECT_STATUS.md` for the
latest observed values.

## Deployment and frontend

- [`../DEPLOYMENT.md`](../DEPLOYMENT.md) - supported single-host Docker Compose deployment.
- [`../frontend/README.md`](../frontend/README.md) - React application development and API integration.
- [`../scripts/README.md`](../scripts/README.md) - helper scripts and their actual defaults.
- [`../monitoring/README.md`](../monitoring/README.md) - Prometheus/Grafana configuration.

## Current verification

The checked-in configuration runs a local, three-broker ZooKeeper-mode Compose
stack with three same-group Processor workers plus optional AI/stress agents
(19 Compose services). AI requests go through an external API and an AI-result
Kafka topic back to the Processors. Synthetic data is stored separately and
excluded again by every primary training path. The
repository does not contain a Spark cluster, physical lake layers, PostgreSQL,
Redis, Elasticsearch/ELK, Jaeger, Alertmanager, Kubernetes manifests or a CI/CD
pipeline. Do not infer those components from older planning text.

Use `PROJECT_STATUS.md` for the dated verification evidence and environment
limitations; configuration alone does not prove a successful container build,
healthy Kafka cluster or a working external provider. Re-run the commands in
`RUNBOOK.md` after changing runtime code or configuration.
