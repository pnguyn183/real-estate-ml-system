# Real Estate ML System - Quick Demo Guide

## Overview

The demo setup allows you to quickly run and test the entire Real Estate Price Prediction system in approximately **5-10 minutes** with Docker.

This includes:
- ✅ Real-time data scraping (optimized for speed)
- ✅ Kafka data streaming
- ✅ Data processing and storage
- ✅ Model training
- ✅ API server
- ✅ Interactive frontend
- ✅ Monitoring dashboards

## Quick Start

### Option 1: Automatic Setup (Recommended)

```bash
# Make script executable (Unix/Linux/macOS)
chmod +x scripts/demo_run.sh

# Run the demo
./scripts/demo_run.sh
```

On Windows PowerShell:
```powershell
# Run with bash if WSL is available
bash scripts/demo_run.sh

# Or using Git Bash directly
```

### Option 2: Manual Setup with Docker Compose

```bash
# Start the demo with docker-compose
docker-compose -f docker-compose.demo.yml up

# In a separate terminal, follow logs
docker-compose -f docker-compose.demo.yml logs -f
```

## What Happens During Demo

### 1. Infrastructure (1-2 minutes)
- Zookeeper and Kafka start
- MongoDB is initialized
- Prometheus and Grafana become available

### 2. Scraping (2-3 minutes)
- Scraper runs with **reduced scope**:
  - Max pages: **8** (instead of 50)
  - Max listings: **400** (instead of 5000)
  - Faster request delays
- Data is published to Kafka topic

### 3. Processing (1-2 minutes)
- Processor consumes from Kafka
- Stores data in MongoDB
- Calculates features

### 4. Training (1-2 minutes)
- Model trains on scraped data
- Metrics are generated
- Model is saved

### 5. API & Frontend (30 seconds)
- API server starts
- Frontend becomes available

## Service URLs

Once the demo is running, access:

| Service | URL | Credentials |
|---------|-----|-------------|
| **Frontend** | http://localhost:3000 | - |
| **API** | http://localhost:8000 | - |
| **API Docs** | http://localhost:8000/docs | - |
| **MongoDB UI** | http://localhost:8081 | - |
| **Prometheus** | http://localhost:9090 | - |
| **Grafana** | http://localhost:3001 | admin/admin |

## Key Files

### Demo Configuration
- `docker-compose.demo.yml` - Main composition file
- `scraper/Dockerfile.demo` - Optimized scraper image
- `modeling/Dockerfile.trainer.demo` - Optimized trainer image

### Demo Scripts
- `scripts/demo_run.sh` - Orchestration script
- `scripts/demo_scraper.sh` - Scraper entrypoint
- `scripts/demo_trainer.sh` - Trainer entrypoint

## Monitoring the Demo

### Watch Progress
```bash
# Watch scraper
docker-compose -f docker-compose.demo.yml logs -f scraper

# Watch processor
docker-compose -f docker-compose.demo.yml logs -f processor

# Watch trainer
docker-compose -f docker-compose.demo.yml logs -f trainer

# Watch API
docker-compose -f docker-compose.demo.yml logs -f api

# Watch all
docker-compose -f docker-compose.demo.yml logs -f
```

### Check Container Status
```bash
docker-compose -f docker-compose.demo.yml ps
```

## Stopping the Demo

### Stop All Services
```bash
docker-compose -f docker-compose.demo.yml down
```

### Clean Up Everything (including data)
```bash
docker-compose -f docker-compose.demo.yml down -v
```

## Demo Parameters

You can customize the demo by setting environment variables:

```bash
# Create .env.demo file
DEMO_MAX_PAGES=10
DEMO_LIMIT=500
DEMO_WAIT_SECONDS=40

# Then run
docker-compose -f docker-compose.demo.yml --env-file .env.demo up
```

### Available Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DEMO_MAX_PAGES` | 8 | Number of listing pages to scrape |
| `DEMO_LIMIT` | 400 | Maximum number of listings |
| `DEMO_WAIT_SECONDS` | 30 | Seconds to wait for data processing |

## Troubleshooting

### Services Won't Start
```bash
# Check Docker is running
docker ps

# Clean up containers
docker-compose -f docker-compose.demo.yml down --remove-orphans
docker system prune -f

# Try again
docker-compose -f docker-compose.demo.yml up
```

### Out of Memory
If you get OOM errors, increase Docker's memory:
- Docker Desktop: Preferences > Resources > Memory (set to 4GB+)
- WSL: Modify `%USERPROFILE%\.wslconfig`

### Port Conflicts
If ports are already in use, stop other services:
```bash
# Find what's using port 3000
lsof -i :3000

# Or change ports in docker-compose.demo.yml
```

### Scraper Timeout
If scraper doesn't complete:
```bash
# Increase timeout or reduce scope
docker-compose -f docker-compose.demo.yml exec scraper tail -f /app/scraper.log
```

## Performance Tips

### Faster Demo
- Reduce `DEMO_MAX_PAGES` to 5
- Reduce `DEMO_LIMIT` to 200
- Reduce `DEMO_WAIT_SECONDS` to 20

### More Data for Testing
- Increase `DEMO_MAX_PAGES` to 15
- Increase `DEMO_LIMIT` to 700
- Allow more processing time

## Demo vs Production

| Aspect | Demo | Production |
|--------|------|-----------|
| Scrape Pages | 8 | 50+ |
| Listings | 400 | 5000+ |
| Train Data | Mini | Full |
| Execution | ~5-10 min | ~hours |
| Purpose | Testing/Demo | Real deployment |
| Auto-restart | No | Yes |
| Data Persistence | Temporary | Persistent |

## Next Steps After Demo

1. **Review Results**: Check frontend at http://localhost:3000
2. **Explore Data**: Use MongoDB UI at http://localhost:8081
3. **Check Metrics**: Visit Grafana at http://localhost:3001
4. **View API Docs**: Open http://localhost:8000/docs
5. **Scale Up**: Use `docker-compose.yml` for larger datasets
6. **Production Deploy**: Follow [DEPLOYMENT.md](../DEPLOYMENT.md)

## Demo Issues & Solutions

### Model not trained
- Check MongoDB has data: `docker-compose -f docker-compose.demo.yml logs processor`
- Increase `DEMO_WAIT_SECONDS`
- Check trainer logs: `docker-compose -f docker-compose.demo.yml logs trainer`

### No scraped data
- Check scraper logs: `docker-compose -f docker-compose.demo.yml logs scraper`
- Verify Kafka is healthy: `docker-compose -f docker-compose.demo.yml logs kafka`

### API returns empty predictions
- Verify model is trained: Check `artifacts/models/price_model.joblib` exists
- Restart API: `docker-compose -f docker-compose.demo.yml restart api`

## Contact & Support

For issues or questions about the demo, check:
- [PROJECT_GUIDE.md](../docs/PROJECT_GUIDE.md)
- [TROUBLESHOOTING](../docs/TROUBLESHOOTING.md)
- Project GitHub issues
