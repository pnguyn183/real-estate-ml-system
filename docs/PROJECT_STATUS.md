# Project Status - Real Estate Price Prediction System

**Last Updated**: May 31, 2026  
**Status**: Production-oriented local system with authentication and RBAC

## Summary

The project contains a complete real estate price prediction workflow:

- Kafka-based scraping and ingestion
- MongoDB-backed processing and training data storage
- Model training and versioned artifacts
- FastAPI prediction API
- React frontend for authenticated predictions and admin access control
- Prometheus and Grafana monitoring
- Docker Compose deployment

## Current Components

### Backend API

Location: `modeling/api.py`  
Auth helpers: `modeling/auth.py`  
Port: `8000`

Core capabilities:

- Health check
- Single and batch predictions
- Model metadata and metrics
- Bearer-token authentication
- Role-based access control
- Admin-only user role and account status management
- Security headers and restricted CORS configuration

### Frontend

Location: `frontend/`  
Port: `3000`

Core components:

- `AuthPanel.tsx` - Register and sign in
- `Header.tsx` - Branding, current role, and sign out
- `PredictionForm.tsx` - Single property prediction input
- `ResultsDisplay.tsx` - Prediction result view
- `ModelInfo.tsx` - Model metrics for `manager` and `admin`
- `UserAdminPanel.tsx` - Admin-only account management
- `StatsCard.tsx` - Status summary cards

## API Endpoints

| Method | Path | Access |
| --- | --- | --- |
| `POST` | `/auth/register` | Public; first account becomes `admin`, later accounts become `user` |
| `POST` | `/auth/login` | Public |
| `GET` | `/auth/me` | Authenticated users |
| `GET` | `/auth/roles` | Authenticated users |
| `GET` | `/auth/users` | `admin` |
| `PATCH` | `/auth/users/{id}/role` | `admin` |
| `PATCH` | `/auth/users/{id}/status` | `admin` |
| `GET` | `/health` | Public |
| `GET` | `/model/info` | `manager`, `admin` |
| `POST` | `/predict` | `user`, `manager`, `admin` |
| `POST` | `/predict/batch` | `manager`, `admin` |

## Role Model

| Role | Permissions |
| --- | --- |
| `user` | Submit single property predictions |
| `manager` | User permissions plus model info and batch prediction |
| `admin` | Full access plus user role and account status management |

Security rules currently implemented:

- Passwords are stored as salted PBKDF2 hashes
- Tokens are signed with `AUTH_SECRET_KEY`
- Login attempts are rate limited in memory
- Disabled accounts cannot authenticate
- The system prevents removing or disabling the last active admin
- API-side role checks enforce access even if the frontend is bypassed

## Quick Start

```bash
docker-compose up -d
```

Access:

- Frontend: http://localhost:3000
- API Docs: http://localhost:8000/docs
- API Health: http://localhost:8000/health
- MongoDB UI: http://localhost:8081
- Grafana: http://localhost:3001
- Prometheus: http://localhost:9090

## API Usage

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

Use the returned token:

```bash
TOKEN="<access_token>"
```

Single prediction:

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

Model info:

```bash
curl http://localhost:8000/model/info \
  -H "Authorization: Bearer $TOKEN"
```

## Testing Status

Verified locally:

```bash
pytest utils/tests/test_auth.py utils/tests/test_model.py utils/tests/test_utils.py
python -m py_compile modeling/auth.py modeling/api.py
cd frontend && npm run build
```

Notes:

- `npm run lint` exists, but the frontend currently has no ESLint config file.
- `/health` can return `initializing` until `artifacts/models/price_model.joblib` exists.

## Production Checklist

- Backend validation and error handling
- API documentation and validation
- Bearer-token authentication and role-based access control
- Frontend authentication flow and admin panel
- Docker containerization
- Monitoring setup
- Deployment guide
- Configure a strong `AUTH_SECRET_KEY`
- Configure HTTPS and production CORS origins
- Configure database authentication and backups
- Move the JSON user store to a managed database for multi-instance deployments
- Add persistent audit logs for admin role and status changes

## Next Enhancements

- Refresh-token rotation for long sessions
- Database-backed user store
- Admin audit log UI
- Batch prediction UI
- Saved prediction history per user
- Usage analytics and model monitoring by account/role
