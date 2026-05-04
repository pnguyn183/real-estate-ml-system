# Project Index & Navigation Guide

Welcome to the Real Estate Price Prediction System! This guide helps you navigate the project and find what you need.

---

## 📚 Documentation by Role

### 👨‍💼 Product Manager / Business Analyst
Start here:
1. **[README.md](../README.md)** - Project overview and features
2. **[PROJECT_STATUS.md](PROJECT_STATUS.md)** - What was built
3. **[docs/REQUIREMENTS.md](REQUIREMENTS.md)** - Business requirements
4. **[docs/METRICS_AND_SLA.md](METRICS_AND_SLA.md)** - KPIs and targets

### 👨‍💻 Software Developer / Engineer
Start here:
1. **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Quick commands and endpoints
2. **[DEPLOYMENT.md](../DEPLOYMENT.md)** - Setup and deployment
3. **[README.md](../README.md)** - Architecture overview
4. **[docs/DATA_SCHEMA.md](DATA_SCHEMA.md)** - Data models
5. **[modeling/api.py](../modeling/api.py)** - API implementation
6. **[frontend/README.md](../frontend/README.md)** - Frontend setup

### 🏗️ DevOps / Infrastructure
Start here:
1. **[DEPLOYMENT.md](../DEPLOYMENT.md)** - Complete deployment guide
2. **[docker-compose.yml](../docker-compose.yml)** - Service configuration
3. **[monitoring/](../monitoring/)** - Prometheus & Grafana setup
4. **[.env.example](../.env.example)** - Environment configuration

### 🔬 Data Scientist / ML Engineer
Start here:
1. **[modeling/price_model.py](../modeling/price_model.py)** - Model implementation
2. **[modeling/train_model.py](../modeling/train_model.py)** - Training script
3. **[docs/DATA_SCHEMA.md](DATA_SCHEMA.md)** - Feature definitions
4. **[docs/METRICS_AND_SLA.md](METRICS_AND_SLA.md)** - Model metrics

### 🎨 Frontend Developer
Start here:
1. **[frontend/README.md](../frontend/README.md)** - Frontend guide
2. **[frontend/src/App.tsx](../frontend/src/App.tsx)** - Main component
3. **[frontend/src/api/client.ts](../frontend/src/api/client.ts)** - API integration
4. **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - API endpoints

---

## 🗂️ Project Structure

```
real-estate-ml-system/
│
├── 📄 Documentation Root
│   ├── README.md                          ← Start here for overview
│   ├── QUICK_REFERENCE.md                 ← Quick commands & APIs
│   ├── DEPLOYMENT.md                      ← Complete deployment guide
│   ├── PROJECT_STATUS.md      ← What was built
│   ├── PROJECT_GUIDE.md                   ← This file
│   ├── .env.example                       ← Configuration template
│   └── docker-compose.yml                 ← Docker services
│
├── 📁 frontend/                           ← React Web Interface
│   ├── src/
│   │   ├── App.tsx                       ← Main app component
│   │   ├── main.tsx                      ← Entry point
│   │   ├── index.css                     ← Tailwind styles
│   │   ├── components/
│   │   │   ├── Header.tsx                ← Navigation bar
│   │   │   ├── PredictionForm.tsx        ← Input form
│   │   │   ├── ResultsDisplay.tsx        ← Results view
│   │   │   ├── StatsCard.tsx             ← Stats cards
│   │   │   └── ModelInfo.tsx             ← Model dashboard
│   │   └── api/
│   │       └── client.ts                 ← API communication
│   ├── Dockerfile                        ← Frontend container
│   ├── package.json                      ← Dependencies
│   ├── vite.config.ts                    ← Build config
│   ├── tailwind.config.js                ← Styling config
│   ├── README.md                         ← Frontend guide
│   └── .env.example                      ← Frontend config
│
├── 📁 modeling/                           ← Machine Learning
│   ├── api.py                            ← FastAPI REST service
│   ├── price_model.py                    ← ML model implementation
│   ├── train_model.py                    ← Training script
│   ├── predict_service.py                ← Legacy prediction service
│   ├── Dockerfile.api                    ← API container
│   ├── Dockerfile.trainer                ← Training container
│   └── Dockerfile.predictor              ← Predictor container
│
├── 📁 processing/                         ← Data Processing
│   ├── kafka_to_mongo.py                 ← Main processor
│   ├── export_training_dataset.py        ← Dataset export
│   ├── dataset_quality_report.py         ← Quality checks
│   └── Dockerfile                        ← Processor container
│
├── 📁 scraper/                            ← Web Scraping
│   ├── kafka_producer.py                 ← Scraper + Publisher
│   ├── listing_feature_scraper.py        ← Scraping logic
│   ├── export_sample_dataset.py          ← Sample export
│   └── Dockerfile                        ← Scraper container
│
├── 📁 monitoring/                         ← Observability
│   ├── prometheus.yml                    ← Prometheus config
│   ├── alert_rules.yml                   ← Alert rules
│   ├── grafana/
│   │   ├── dashboards/
│   │   │   └── real_estate_pipeline.json ← Dashboard config
│   │   └── provisioning/                 ← Grafana provisioning
│   └── README.md                         ← Monitoring guide
│
├── 📁 utils/                              ← Shared Utilities
│   ├── logging_utils.py                  ← Logging setup
│   ├── metrics.py                        ← Metrics setup
│   └── validation.py                     ← Validation helpers
│
├── 📁 tests/                              ← Test Suite
│   ├── test_model.py                     ← Model tests
│   └── test_utils.py                     ← Utility tests
│
├── 📁 docs/                               ← Technical Documentation
│   ├── README.md                         ← Docs index
│   ├── REQUIREMENTS.md                   ← Business requirements
│   ├── DATA_SCHEMA.md                    ← Data models
│   ├── METRICS_AND_SLA.md                ← KPIs & SLAs
│   ├── ACCEPTANCE_CRITERIA.md            ← Quality gates
│   ├── ERROR_HANDLING_STRATEGY.md        ← Error handling
│   ├── MONITORING_AND_ALERTING.md        ← Monitoring setup
│   ├── FLOW_DIAGRAMS.md                  ← Architecture diagrams
│   ├── FLOW_ANALYSIS.md                  ← System analysis
│   ├── IMPLEMENTATION_SUMMARY.md         ← Implementation details
│   ├── RUNBOOK.md                        ← Operations guide
│   └── DASHBOARD.md                      ← Dashboard guide
│
├── 📁 scripts/                            ← Automation Scripts
│   ├── auto_scrape.py                    ← Auto scraper
│   ├── auto_train.py                     ← Auto trainer
│   ├── health_check.py                   ← Health checker
│   ├── start_all.sh                      ← Start all services
│   └── stop_all.sh                       ← Stop all services
│
└── 📁 artifacts/                          ← Generated Files
    ├── models/
    │   └── price_model_*.joblib          ← Model versions
    ├── price_model.joblib                ← Stable model path
    ├── price_model_metrics.json          ← Metrics
    └── ...
```

---

## 🚀 Getting Started

### 5-Minute Quick Start
```bash
docker-compose up -d
# Open http://localhost:3000
```

### Full Development Setup
See [DEPLOYMENT.md](../DEPLOYMENT.md#local-development)

### API Testing
See [QUICK_REFERENCE.md](QUICK_REFERENCE.md#-api-quick-reference)

---

## 📊 Key Features

### Backend
- ✅ Production-ready Kafka pipeline
- ✅ MongoDB data storage
- ✅ ML model training & versioning
- ✅ FastAPI REST service
- ✅ Prometheus metrics
- ✅ Comprehensive error handling

### Frontend
- ✅ Beautiful React interface
- ✅ Real-time price predictions
- ✅ Model performance dashboard
- ✅ Responsive design
- ✅ TypeScript type safety

### DevOps
- ✅ Docker containerization
- ✅ Docker Compose orchestration
- ✅ Prometheus + Grafana monitoring
- ✅ Deployment guides
- ✅ Production-ready

---

## 🔌 Service Endpoints

| Component | Port | URL | Purpose |
|-----------|------|-----|---------|
| Frontend | 3000 | http://localhost:3000 | Web UI |
| API | 8000 | http://localhost:8000 | REST API |
| API Docs | 8000 | http://localhost:8000/docs | Swagger UI |
| Prometheus | 9090 | http://localhost:9090 | Metrics |
| Grafana | 3001 | http://localhost:3001 | Dashboards |
| MongoDB UI | 8081 | http://localhost:8081 | Database UI |
| Kafka | 9092 | localhost:9092 | Message queue |
| MongoDB | 27017 | localhost:27017 | Database |

---

## 📖 Common Tasks

### I want to...

**Predict a price**
→ Open http://localhost:3000 and fill the form

**Check API status**
→ Go to http://localhost:8000/health

**View API documentation**
→ Go to http://localhost:8000/docs

**See real-time metrics**
→ Go to http://localhost:9090

**View dashboards**
→ Go to http://localhost:3001 (Grafana)

**Train a new model**
→ Run `python modeling/train_model.py`

**Scrape new data**
→ Run `python scraper/kafka_producer.py`

**Deploy to production**
→ See [DEPLOYMENT.md](../DEPLOYMENT.md)

**Troubleshoot issues**
→ See [DEPLOYMENT.md#troubleshooting](../DEPLOYMENT.md#troubleshooting)

**Understand the data flow**
→ See [docs/FLOW_DIAGRAMS.md](FLOW_DIAGRAMS.md)

**Check requirements**
→ See [docs/REQUIREMENTS.md](REQUIREMENTS.md)

---

## 🔍 Search & Find

**Looking for...**

| What | Where |
|------|-------|
| API endpoints | [QUICK_REFERENCE.md](QUICK_REFERENCE.md#-api-quick-reference) |
| Configuration | [.env.example](../.env.example) |
| Data fields | [docs/DATA_SCHEMA.md](DATA_SCHEMA.md) |
| ML model details | [modeling/price_model.py](../modeling/price_model.py) |
| Frontend code | [frontend/README.md](../frontend/README.md) |
| Deployment steps | [DEPLOYMENT.md](../DEPLOYMENT.md) |
| Database schema | [docs/DATA_SCHEMA.md](DATA_SCHEMA.md) |
| Error handling | [docs/ERROR_HANDLING_STRATEGY.md](ERROR_HANDLING_STRATEGY.md) |
| Monitoring setup | [monitoring/README.md](../monitoring/README.md) |
| Kafka configuration | [docker-compose.yml](../docker-compose.yml) |

---

## 🎓 Learning Path

### New to the Project?
1. Read [README.md](../README.md)
2. Run quick start: `docker-compose up -d`
3. Explore http://localhost:3000
4. Read [QUICK_REFERENCE.md](QUICK_REFERENCE.md)

### Want to Deploy?
1. Read [DEPLOYMENT.md](../DEPLOYMENT.md)
2. Set up environment in `.env`
3. Choose deployment option
4. Follow step-by-step guide

### Want to Develop?
1. Read [frontend/README.md](../frontend/README.md)
2. Or read [docs/](./) for backend
3. Set up local development
4. Start coding!

### Want to Understand Data?
1. Read [docs/DATA_SCHEMA.md](DATA_SCHEMA.md)
2. Check [docs/FLOW_DIAGRAMS.md](FLOW_DIAGRAMS.md)
3. Run data quality report
4. Explore MongoDB UI

---

## 🆘 Quick Help

**System won't start?**
```bash
docker-compose logs
docker-compose down -v
docker-compose up -d
```

**API not responding?**
```bash
curl http://localhost:8000/health
docker-compose logs api
```

**Frontend won't load?**
```bash
cat frontend/.env | grep VITE_API_URL
npm run build --verbose
```

**More help?**
→ See [DEPLOYMENT.md#troubleshooting](../DEPLOYMENT.md#troubleshooting)

---

## 📞 Support Resources

| Issue Type | Resource |
|------------|----------|
| General setup | [QUICK_REFERENCE.md](QUICK_REFERENCE.md) |
| Deployment | [DEPLOYMENT.md](../DEPLOYMENT.md) |
| API usage | http://localhost:8000/docs |
| Architecture | [docs/FLOW_DIAGRAMS.md](FLOW_DIAGRAMS.md) |
| Troubleshooting | [DEPLOYMENT.md#troubleshooting](../DEPLOYMENT.md#troubleshooting) |
| Data model | [docs/DATA_SCHEMA.md](DATA_SCHEMA.md) |

---

## 📝 Version Info

- **Project**: Real Estate Price Prediction System
- **Version**: 1.0.0
- **Status**: Production Ready ✅
- **Created**: May 1, 2026
- **Last Updated**: May 1, 2026

---

## 🎯 Navigation

- **Back to Top**: [Go to top](#project-index--navigation-guide)
- **Main README**: [README.md](../README.md)
- **Quick Reference**: [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- **Deployment**: [DEPLOYMENT.md](../DEPLOYMENT.md)
- **Summary**: [PROJECT_STATUS.md](PROJECT_STATUS.md)

---

**Happy coding! 🚀**
