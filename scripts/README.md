# Automation Scripts

This folder contains scripts to run the pipeline automatically.

## Scripts

- **auto_scrape.py**: Automatically run scraper at interval (default: 1 hour)
- **auto_train.py**: Automatically train model at interval (default: 6 hours)
- **health_check.py**: Check health of all services
- **start_all.sh**: Start all services (docker compose)
- **stop_all.sh**: Stop all services

## Usage

### Start all services (automated)
```bash
./scripts/start_all.sh
# or: bash scripts/start_all.sh
```

Services will run automatically in the background:
- Scraper will run every SCRAPE_INTERVAL seconds (default: 1 hour)
- Trainer will run every TRAIN_INTERVAL seconds (default: 6 hours), if enough data

### Stop all services
```bash
./scripts/stop_all.sh
# or: bash scripts/stop_all.sh
```

### Check health
```bash
python scripts/health_check.py
```

## Configuration

Set environment variables before starting:
```bash
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export MONGO_URI=mongodb://localhost:27017/
export MONGO_DB=real_estate_db
export SCRAPE_INTERVAL=3600        # 1 hour
export TRAIN_INTERVAL=21600        # 6 hours
export MIN_RECORDS_FOR_TRAINING=500
```

## Logs

View logs of specific services:
```bash
docker compose logs -f scraper
docker compose logs -f processor
docker compose logs -f trainer
```
