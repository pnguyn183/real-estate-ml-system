# Project Index and Navigation Guide

This guide maps the Real Estate Price Prediction System and points each role to the most useful files.

## Start Here by Role

### Product Manager or Business Analyst

1. [README.md](../README.md) - Project overview
2. [PROJECT_STATUS.md](PROJECT_STATUS.md) - Current implementation status
3. [REQUIREMENTS.md](REQUIREMENTS.md) - Product requirements
4. [METRICS_AND_SLA.md](METRICS_AND_SLA.md) - KPIs and targets

### Software Developer

1. [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Commands, auth flow, and endpoint examples
2. [DEPLOYMENT.md](../DEPLOYMENT.md) - Setup and deployment
3. [modeling/api.py](../modeling/api.py) - FastAPI endpoints and authorization dependencies
4. [modeling/auth.py](../modeling/auth.py) - Authentication, token, password hashing, and RBAC helpers
5. [frontend/README.md](../frontend/README.md) - Frontend setup
6. [frontend/src/api/client.ts](../frontend/src/api/client.ts) - API client and token handling

### DevOps or Infrastructure

1. [DEPLOYMENT.md](../DEPLOYMENT.md) - Complete deployment guide
2. [docker-compose.yml](../docker-compose.yml) - Service configuration
3. [monitoring/](../monitoring/) - Prometheus and Grafana setup
4. [QUICK_REFERENCE.md](QUICK_REFERENCE.md#environment-configuration) - Key environment variables

### Data Scientist or ML Engineer

1. [modeling/price_model.py](../modeling/price_model.py) - Model implementation
2. [modeling/train_model.py](../modeling/train_model.py) - Training script
3. [DATA_SCHEMA.md](DATA_SCHEMA.md) - Feature definitions
4. [METRICS_AND_SLA.md](METRICS_AND_SLA.md) - Model metrics

### Frontend Developer

1. [frontend/README.md](../frontend/README.md) - Frontend guide
2. [frontend/src/App.tsx](../frontend/src/App.tsx) - Main app shell and role-aware rendering
3. [frontend/src/components/AuthPanel.tsx](../frontend/src/components/AuthPanel.tsx) - Login and registration UI
4. [frontend/src/components/UserAdminPanel.tsx](../frontend/src/components/UserAdminPanel.tsx) - Admin user management UI
5. [frontend/src/api/client.ts](../frontend/src/api/client.ts) - API integration

## Project Structure

```text
real-estate-ml-system/
├── README.md
├── DEPLOYMENT.md
├── docker-compose.yml
├── frontend/
│   ├── src/
│   │   ├── api/client.ts
│   │   ├── components/
│   │   │   ├── AuthPanel.tsx
│   │   │   ├── Header.tsx
│   │   │   ├── ModelInfo.tsx
│   │   │   ├── PredictionForm.tsx
│   │   │   ├── ResultsDisplay.tsx
│   │   │   ├── StatsCard.tsx
│   │   │   └── UserAdminPanel.tsx
│   │   ├── App.tsx
│   │   └── main.tsx
│   └── README.md
├── modeling/
│   ├── api.py
│   ├── auth.py
│   ├── price_model.py
│   └── train_model.py
├── processing/
│   ├── kafka_to_mongo.py
│   ├── export_training_dataset.py
│   └── dataset_quality_report.py
├── scraper/
│   ├── kafka_producer.py
│   └── listing_feature_scraper.py
├── monitoring/
├── scripts/
├── utils/
│   ├── tests/test_auth.py
│   ├── tests/test_model.py
│   └── tests/test_utils.py
└── docs/
```

## Key Features

### Backend

- Kafka ingestion pipeline
- MongoDB storage
- ML model training and versioning
- FastAPI REST service
- Bearer-token authentication
- Role-based access control for `admin`, `manager`, and `user`
- Prometheus metrics
- Structured error handling

### Frontend

- React + TypeScript app
- Register and sign in flow
- Single prediction workspace
- Model info panel for `manager` and `admin`
- Admin user and role management panel

### DevOps

- Docker Compose orchestration
- Prometheus and Grafana monitoring
- Deployment and runbook documentation

## Service Endpoints

| Component | Port | URL | Purpose |
| --- | --- | --- | --- |
| Frontend | 3000 | http://localhost:3000 | Web UI |
| API | 8000 | http://localhost:8000 | REST API |
| API Docs | 8000 | http://localhost:8000/docs | Swagger UI |
| Prometheus | 9090 | http://localhost:9090 | Metrics |
| Grafana | 3001 | http://localhost:3001 | Dashboards |
| MongoDB UI | 8081 | http://localhost:8081 | Database UI |
| Kafka | 9092 | localhost:9092 | Message queue |
| MongoDB | 27017 | localhost:27017 | Database |

## Common Tasks

| Task | Where to Go |
| --- | --- |
| Start the system | `docker-compose up -d` |
| Predict a price | http://localhost:3000 |
| Bootstrap the first admin | [QUICK_REFERENCE.md](QUICK_REFERENCE.md#authentication-quick-reference) |
| Check API status | http://localhost:8000/health |
| View API documentation | http://localhost:8000/docs |
| View dashboards | http://localhost:3001 |
| Train a model | `python modeling/train_model.py` |
| Scrape new data | `python scraper/kafka_producer.py` |
| Deploy to production | [DEPLOYMENT.md](../DEPLOYMENT.md) |
| Troubleshoot issues | [DEPLOYMENT.md#troubleshooting](../DEPLOYMENT.md#troubleshooting) |

## API Access Summary

| Endpoint | Access |
| --- | --- |
| `GET /health` | Public |
| `POST /auth/register` | Public |
| `POST /auth/login` | Public |
| `GET /auth/me` | Authenticated |
| `POST /predict` | `user`, `manager`, `admin` |
| `GET /model/info` | `manager`, `admin` |
| `POST /predict/batch` | `manager`, `admin` |
| `/auth/users*` | `admin` |

## Search and Find

| Looking For | File |
| --- | --- |
| API endpoints and auth policies | [modeling/api.py](../modeling/api.py) |
| Auth internals | [modeling/auth.py](../modeling/auth.py) |
| API curl examples | [QUICK_REFERENCE.md](QUICK_REFERENCE.md) |
| Frontend auth flow | [frontend/src/components/AuthPanel.tsx](../frontend/src/components/AuthPanel.tsx) |
| Admin panel | [frontend/src/components/UserAdminPanel.tsx](../frontend/src/components/UserAdminPanel.tsx) |
| Deployment steps | [DEPLOYMENT.md](../DEPLOYMENT.md) |
| Data fields | [DATA_SCHEMA.md](DATA_SCHEMA.md) |
| Monitoring setup | [monitoring/README.md](../monitoring/README.md) |

## Quick Help

```bash
docker-compose logs
docker-compose ps
curl http://localhost:8000/health
```

If the API returns:

- `401`: sign in again or provide `Authorization: Bearer <token>`.
- `403`: the account does not have the required role.
- `503`: the model artifact is not available yet.

## Version Info

- **Project**: Real Estate Price Prediction System
- **Version**: 1.0.0
- **Status**: Production-oriented local system with auth/RBAC
- **Last Updated**: May 31, 2026
