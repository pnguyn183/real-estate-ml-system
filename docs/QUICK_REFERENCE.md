# Quick Reference Guide

## Getting Started in 5 Minutes

### Option 1: Docker

```bash
docker-compose up -d
# Open http://localhost:3000
```

### Option 2: Local Development

```bash
# Terminal 1: Backend API
python -m uvicorn modeling.api:app --reload

# Terminal 2: Frontend
cd frontend
npm run dev
```

## Service Endpoints

| Service | URL | Purpose |
| --- | --- | --- |
| Frontend | http://localhost:3000 | Web UI |
| API | http://localhost:8000 | REST API |
| API Docs | http://localhost:8000/docs | Swagger UI |
| MongoDB UI | http://localhost:8081 | Database browser |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards |

## Authentication Quick Reference

The API is protected with bearer-token authentication. `/health` is public; prediction and model endpoints require a signed-in account.

Register the first admin:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass1","full_name":"Admin"}'
```

Sign in:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass1"}'
```

Store the token for later examples:

```bash
TOKEN="<access_token>"
```

Role access:

| Role | Access |
| --- | --- |
| `user` | `POST /predict` |
| `manager` | User access, `GET /model/info`, `POST /predict/batch` |
| `admin` | Full access, including user role and account status management |

## API Quick Reference

### Health Check

```bash
curl http://localhost:8000/health
```

### Current User

```bash
curl http://localhost:8000/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

### Model Info

Requires `manager` or `admin`.

```bash
curl http://localhost:8000/model/info \
  -H "Authorization: Bearer $TOKEN"
```

### Predict Single Property

Requires `user`, `manager`, or `admin`.

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "area_m2": 100,
    "bedroom_count": 2,
    "bathroom_count": 1,
    "property_type": "apartment",
    "province_slug": "hanoi",
    "district_slug": "dongda"
  }'
```

### Batch Prediction

Requires `manager` or `admin`.

```bash
curl -X POST http://localhost:8000/predict/batch \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "properties": [
      {
        "area_m2": 100,
        "bedroom_count": 2,
        "property_type": "apartment",
        "province_slug": "hanoi"
      }
    ]
  }'
```

## Important Files

| File | Purpose |
| --- | --- |
| `modeling/api.py` | REST API service and endpoint policies |
| `modeling/auth.py` | Authentication, password hashing, token handling, RBAC |
| `modeling/price_model.py` | ML model implementation |
| `modeling/train_model.py` | Model training script |
| `processing/kafka_to_mongo.py` | Data processor |
| `scraper/kafka_producer.py` | Web scraper |
| `frontend/src/App.tsx` | React main component |
| `frontend/src/api/client.ts` | API client and auth token handling |
| `DEPLOYMENT.md` | Full deployment guide |
| `README.md` | Project overview |

## Common Commands

### Backend

```bash
python -m uvicorn modeling.api:app --reload
python modeling/train_model.py
pytest utils/tests/
```

### Frontend

```bash
cd frontend
npm install
npm run dev
npm run build
npm run type-check
```

`npm run lint` requires an ESLint config file before it can run.

### Docker

```bash
docker-compose up -d
docker-compose down
docker-compose logs -f api
docker-compose build --no-cache
```

## Environment Configuration

### API

```bash
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
AUTH_SECRET_KEY=replace-with-a-long-random-secret
AUTH_USERS_PATH=artifacts/auth/users.json
AUTH_TOKEN_EXPIRE_MINUTES=60
```

### Database

```bash
MONGO_URI=mongodb://localhost:27017/
MONGO_DB=real_estate_db
```

### Kafka

```bash
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_RAW_TOPIC=real_estate_raw
KAFKA_CLEAN_TOPIC=real_estate_features
```

### Model

```bash
MODEL_PATH=artifacts/models/price_model.joblib
```

## Property Fields

Useful prediction fields:

- `area_m2`
- `bedroom_count`
- `bathroom_count`
- `floor_count`
- `front_width_m`
- `road_width_m`
- `property_type`
- `listing_type`
- `province_slug`
- `district_slug`
- `ward_slug`
- `direction`
- `legal`
- `description`

## Troubleshooting

### API Will Not Start

```bash
docker-compose logs api
lsof -i :8000
```

On Windows, use:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

### Frontend Will Not Connect

```bash
curl http://localhost:8000/health
cat frontend/.env
```

### API Returns 401 or 403

- `401`: token is missing, invalid, or expired. Sign in again.
- `403`: account is active but does not have the required role.

### Docker Issues

```bash
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
docker-compose ps
```

## Documentation

- [README.md](../README.md) - Project overview
- [DEPLOYMENT.md](../DEPLOYMENT.md) - Deployment guide
- [PROJECT_STATUS.md](PROJECT_STATUS.md) - Current implementation status
- [API Docs](http://localhost:8000/docs) - Interactive API documentation

**Last Updated**: May 31, 2026  
**Version**: 1.0.0
