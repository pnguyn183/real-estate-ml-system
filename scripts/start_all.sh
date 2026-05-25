#!/bin/bash
set -e

echo "========================================="
echo "Starting Real Estate Pipeline (Automated)"
echo "========================================="

# Set environment variables
export KAFKA_BOOTSTRAP_SERVERS=${KAFKA_BOOTSTRAP_SERVERS:-"localhost:9092"}
export MONGO_URI=${MONGO_URI:-"mongodb://localhost:27017/"}
export MONGO_DB=${MONGO_DB:-"real_estate_db"}
export SCRAPE_LIMIT=${SCRAPE_LIMIT:-300}
export SCRAPE_MAX_PAGES=${SCRAPE_MAX_PAGES:-5}
export SCRAPE_INTERVAL=${SCRAPE_INTERVAL:-1800}
export SCRAPE_TIMEOUT=${SCRAPE_TIMEOUT:-300}
export SCRAPE_FRESH_START=${SCRAPE_FRESH_START:-true}
export TRAIN_INTERVAL=${TRAIN_INTERVAL:-1800}
export TRAIN_RETRY_INTERVAL=${TRAIN_RETRY_INTERVAL:-60}
export MIN_RECORDS_FOR_TRAINING=${MIN_RECORDS_FOR_TRAINING:-30}

# Start all services
echo "Starting docker services..."
docker compose up -d
docker compose restart scraper

echo ""
echo "Waiting for services to be healthy..."
sleep 10

# Check if services are running
docker compose ps

echo ""
echo "========================================="
echo "Pipeline started successfully!"
echo "========================================="
echo ""
echo "Service endpoints:"
echo "  - Kafka: localhost:9092"
echo "  - MongoDB: localhost:27017"
echo "  - Mongo Express: http://localhost:8081"
echo "  - Prometheus: http://localhost:9090"
echo "  - Grafana: http://localhost:3001 (admin/admin)"
echo "  - Frontend: http://localhost:3000"
echo "  - API docs: http://localhost:8000/docs"
echo "  - Processor metrics: http://localhost:8003/metrics"
echo "  - Trainer metrics: http://localhost:8001/metrics"
echo ""
echo "To view logs:"
echo "  docker compose logs -f processor"
echo "  docker compose logs -f trainer"
echo "  docker compose logs -f scraper"
echo ""
echo "To stop all services:"
echo "  docker compose down"
