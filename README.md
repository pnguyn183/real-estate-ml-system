# Real Estate Price Prediction System 🏠💰

A comprehensive, production-ready system for predicting Vietnamese property prices using machine learning.
Features a data pipeline, REST API, and modern web interface.

## 🎯 Overview

This system combines:
- **Data Pipeline**: Kafka-based real estate scraping and normalization
- **ML Model**: Ensemble regression model with 94%+ accuracy
- **REST API**: FastAPI service with comprehensive documentation
- **Frontend**: Beautiful React interface for price predictions
- **Monitoring**: Prometheus + Grafana for system observability

## 📊 Project Structure

```
├── frontend/                    # React frontend (http://localhost:3000)
│   ├── src/
│   │   ├── components/         # React components
│   │   ├── api/               # API client
│   │   └── App.tsx            # Main app
│   └── package.json
│
├── modeling/
│   ├── api.py                 # FastAPI service
│   ├── train_model.py         # Model training
│   ├── price_model.py         # Model implementation
│   └── Dockerfile.api         # API Docker image
│
├── processing/
│   ├── kafka_to_mongo.py      # Kafka consumer + processor
│   ├── export_training_dataset.py
│   └── dataset_quality_report.py
│
├── scraper/
│   ├── kafka_producer.py      # Web scraper + Kafka producer
│   └── listing_feature_scraper.py
│
├── monitoring/                # Prometheus + Grafana configs
├── utils/                     # Shared utilities
├── tests/                     # Test suite
└── DEPLOYMENT.md              # Deployment guide
```

## 🚀 Quick Start

### Option 1: Docker Compose (Recommended)

```bash
# Start all services
docker-compose up -d

# Access:
# Frontend: http://localhost:3000
# API Docs: http://localhost:8000/docs
# Grafana: http://localhost:3001
# MongoDB UI: http://localhost:8081
```

### Option 2: Local Development

See [DEPLOYMENT.md](DEPLOYMENT.md#local-development) for detailed setup instructions.

## 📈 Data Flow

```
Web Scraper → Kafka → MongoDB → Model Training → REST API → Frontend
    ↓              ↓         ↓
  listings      raw         normalized      model      predictions
               messages      features       versioning
```

1. **Scraper** (`scraper/kafka_producer.py`)
   - Crawls property listings from real estate websites
   - Publishes to `real_estate_raw` Kafka topic
   - Supports resumable collection with checkpoints

2. **Processor** (`processing/kafka_to_mongo.py`)
   - Consumes from Kafka
   - Normalizes and validates data
   - Stores in MongoDB for ML training
   - Features graceful shutdown and error recovery

3. **Model Training** (`modeling/train_model.py`)
   - Trains ensemble regression model (Ridge + HistGradientBoosting)
   - Includes TF-IDF text features
   - Auto-versioning of models for rollback capability

4. **REST API** (`modeling/api.py`)
   - FastAPI with Pydantic validation
   - Single & batch prediction endpoints
   - Model info and health check endpoints
   - Interactive API documentation

5. **Frontend** (`frontend/`)
   - Modern React interface
   - Real-time predictions
   - Model performance dashboard
   - Responsive design with Tailwind CSS

## 📚 Documentation (For BA/Product/Engineering)

See [docs/README.md](docs/README.md) for comprehensive documentation:

- **[PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)** - Navigation guide for the repository
- **[QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md)** - Common commands and service endpoints
- **[REQUIREMENTS.md](docs/REQUIREMENTS.md)** - Product requirements, business objectives, acceptance criteria
- **[DATA_SCHEMA.md](docs/DATA_SCHEMA.md)** - Data models, validation rules, field definitions
- **[METRICS_AND_SLA.md](docs/METRICS_AND_SLA.md)** - KPIs, SLAs, performance targets
- **[ACCEPTANCE_CRITERIA.md](docs/ACCEPTANCE_CRITERIA.md)** - Quality gates, data quality scoring
- **[ERROR_HANDLING_STRATEGY.md](docs/ERROR_HANDLING_STRATEGY.md)** - Error codes, recovery strategies
- **[MONITORING_AND_ALERTING.md](docs/MONITORING_AND_ALERTING.md)** - Metrics, dashboards, alerting rules
- **[FLOW_DIAGRAMS.md](docs/FLOW_DIAGRAMS.md)** - Architecture, data flows, decision trees
- **[FLOW_ANALYSIS.md](docs/FLOW_ANALYSIS.md)** - Current state assessment, issues & fixes
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Complete deployment guide for all environments

**Quick Start:** Read [docs/README.md](docs/README.md) to understand which document to read for your role.

## 🔌 API Reference

### Base URL
```
http://localhost:8000
```

### Authentication
Currently open (consider adding API keys in production)

### Endpoints

#### Health Check
```bash
GET /health
```
Response:
```json
{
  "status": "ready",
  "model_path": "artifacts/models/price_model.joblib",
  "model_exists": true,
  "timestamp": "2024-01-01T12:00:00Z"
}
```

#### Model Information
```bash
GET /model/info
```

#### Single Prediction
```bash
POST /predict
Content-Type: application/json

{
  "area_m2": 100,
  "bedroom_count": 2,
  "bathroom_count": 1,
  "property_type": "apartment",
  "province_slug": "hanoi",
  "district_slug": "dongda"
}
```

#### Batch Prediction
```bash
POST /predict/batch
Content-Type: application/json

{
  "properties": [
    {"area_m2": 100, "bedroom_count": 2, ...},
    {"area_m2": 150, "bedroom_count": 3, ...}
  ]
}
```

📖 Full API documentation available at: `http://localhost:8000/docs`

## 💻 Technology Stack

### Backend
- **Python 3.11** - Runtime
- **FastAPI** - Web framework
- **Kafka** - Message streaming
- **MongoDB** - Data storage
- **scikit-learn** - ML model
- **Prometheus** - Metrics
- **Docker** - Containerization

### Frontend
- **React 18** - UI library
- **TypeScript** - Type safety
- **Tailwind CSS** - Styling
- **Axios** - HTTP client
- **Vite** - Build tool
- **Lucide Icons** - Icons

### DevOps
- **Docker Compose** - Local development
- **Nginx** - Reverse proxy
- **Grafana** - Monitoring dashboards
- **Prometheus** - Metrics collection

## 📊 Monitoring

Access pre-configured dashboards:
- **Grafana**: http://localhost:3001 (admin/admin)
- **Prometheus**: http://localhost:9090

Key metrics:
- Data processing latency
- Model prediction accuracy (MAE, RMSE, R²)
- Kafka message throughput
- Database performance
- API response times

## 🛠️ Development

### Running Tests
```bash
# Backend tests
pytest tests/

# Frontend tests
cd frontend && npm test
```

### Code Quality
```bash
# Python
flake8 . --max-line-length=100
black . --check

# Frontend
cd frontend && npm run lint
```

### Building Production Images
```bash
# API image
docker build -f modeling/Dockerfile.api -t real-estate-api:latest .

# Frontend image
docker build -t real-estate-frontend:latest frontend/
```

## 📋 Useful crawled fields

The scraper captures properties with these key fields:

- `price_text` → normalized to `price_vnd`
- `area_text` → normalized to `area_m2`
- `bedroom_text` → `bedroom_count`
- `property_type`, `province_slug`, `district_slug`
- `direction`, `legal`, `furniture`
- Full property description for text analysis
- `bathroom_text`
- `floor_text`
- `front_width_text`
- `road_width_text`
- `legal_text`
- `direction_text`
- `property_type`
- `listing_type`
- `province_slug`
- `district_slug`
- `ward_slug`
- `furniture_text`
- `project_hint`
- `description`
- `posted_date_text`

## Run

### Automated startup (Recommended)

Start all services automatically with monitoring:

```powershell
bash scripts/start_all.sh
```

This will:
- Start Kafka, MongoDB, Zookeeper, Prometheus, Grafana
- Start processor (consume Kafka → MongoDB)
- Start trainer (train model every 6 hours if data available)
- Start scraper (crawl every 1 hour)

Check health:
```powershell
python scripts/health_check.py
```

View logs:
```powershell
docker compose logs -f processor
docker compose logs -f trainer
docker compose logs -f scraper
```

Stop all services:
```powershell
bash scripts/stop_all.sh
```

Access monitoring dashboards:
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001 (admin/admin)
- Mongo Express: http://localhost:8081

### Manual startup (For testing/development)

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Start Kafka and MongoDB:

```powershell
docker compose up -d zookeeper kafka mongodb grafana prometheus
```

Crawl a large batch into Kafka:

```powershell
python scraper\kafka_producer.py --limit 20000 --max-pages 1000
```

Build MongoDB training features:

```powershell
python processing\kafka_to_mongo.py
```

Generate a quality report:

```powershell
python processing\dataset_quality_report.py
```

Export a training dataset:

```powershell
python processing\export_training_dataset.py --format parquet
```

Train the model:

```powershell
python modeling\train_model.py
```

Predict from a feature JSON:

```powershell
python modeling\predict_price.py --input-json path\to\feature_record.json
```
