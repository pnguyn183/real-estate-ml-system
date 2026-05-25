# Automation Scripts

This folder contains scripts to run the pipeline automatically.

## Scripts

- **auto_scrape.py**: Run scraper immediately, then repeat at interval
- **auto_train.py**: Check training data immediately, retry quickly until enough data, then train at interval
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
- Scraper runs immediately on container start, then every `SCRAPE_INTERVAL` seconds
- Trainer checks immediately on container start; if data is not ready, it retries every `TRAIN_RETRY_INTERVAL` seconds

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
export SCRAPE_LIMIT=300            # listings per run for Docker testing
export SCRAPE_MAX_PAGES=5          # pages per run for Docker testing
export SCRAPE_INTERVAL=1800        # 30 minutes
export SCRAPE_TIMEOUT=300          # 5 minutes
export TRAIN_INTERVAL=1800         # 30 minutes after successful training
export TRAIN_RETRY_INTERVAL=60     # retry while waiting for data
export MIN_RECORDS_FOR_TRAINING=200       # minimum candidate records before training
```

## Logs

View logs of specific services:
```bash
docker compose logs -f scraper
docker compose logs -f processor
docker compose logs -f trainer
```
