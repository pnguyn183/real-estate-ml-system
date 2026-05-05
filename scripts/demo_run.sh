#!/bin/bash
set -e

echo "========================================="
echo "REAL ESTATE ML SYSTEM - DEMO MODE"
echo "========================================="
echo ""
echo "This script will:"
echo "  1. Start all infrastructure (Kafka, MongoDB, Prometheus, Grafana)"
echo "  2. Run optimized scraper (8 pages, 400 listings)"
echo "  3. Process data with Kafka to MongoDB"
echo "  4. Train model with quick parameters"
echo "  5. Start API and Frontend"
echo ""
echo "Estimated time: 5-10 minutes"
echo ""
echo "========================================="

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null && ! command -v docker &> /dev/null; then
    echo "Error: Docker and Docker Compose are required!"
    exit 1
fi

# Determine docker-compose command
if command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
else
    DOCKER_COMPOSE="docker compose"
fi

# Clean up any previous demo containers
echo ""
echo "Cleaning up previous demo containers..."
$DOCKER_COMPOSE -f docker-compose.demo.yml down --remove-orphans 2>/dev/null || true

# Start services
echo ""
echo "Step 1: Starting infrastructure services..."
$DOCKER_COMPOSE -f docker-compose.demo.yml up -d zookeeper kafka mongodb mongo-express prometheus grafana

echo "Waiting for services to be healthy..."
sleep 15

# Start scraper
echo ""
echo "Step 2: Starting scraper..."
$DOCKER_COMPOSE -f docker-compose.demo.yml up -d scraper

echo "Waiting for scraper to complete..."
$DOCKER_COMPOSE -f docker-compose.demo.yml exec -T scraper sh -c 'while [ ! -f /app/scrape_done.flag ]; do echo "Waiting for scraper..."; sleep 5; done'
echo "Scraper completed!"

# Start processor
echo ""
echo "Step 3: Starting processor..."
$DOCKER_COMPOSE -f docker-compose.demo.yml up -d processor

echo "Waiting for data to be processed (30 seconds)..."
sleep 30

# Start trainer
echo ""
echo "Step 4: Starting model trainer..."
$DOCKER_COMPOSE -f docker-compose.demo.yml up -d trainer

echo "Waiting for trainer to complete..."
$DOCKER_COMPOSE -f docker-compose.demo.yml exec -T trainer sh -c 'while [ ! -f /app/train_done.flag ]; do echo "Waiting for trainer..."; sleep 5; done'
echo "Training completed!"

# Start API and Frontend
echo ""
echo "Step 5: Starting API and Frontend..."
$DOCKER_COMPOSE -f docker-compose.demo.yml up -d api frontend

echo "Waiting for services to be ready..."
sleep 10

# Show final status
echo ""
echo "========================================="
echo "DEMO SETUP COMPLETE!"
echo "========================================="
echo ""
echo "Service endpoints:"
echo "  - Frontend: http://localhost:3000"
echo "  - API: http://localhost:8000"
echo "  - API Docs: http://localhost:8000/docs"
echo "  - MongoDB: localhost:27017"
echo "  - Mongo Express: http://localhost:8081"
echo "  - Prometheus: http://localhost:9090"
echo "  - Grafana: http://localhost:3001 (admin/admin)"
echo ""
echo "View logs:"
echo "  docker-compose -f docker-compose.demo.yml logs -f scraper"
echo "  docker-compose -f docker-compose.demo.yml logs -f processor"
echo "  docker-compose -f docker-compose.demo.yml logs -f trainer"
echo "  docker-compose -f docker-compose.demo.yml logs -f api"
echo "  docker-compose -f docker-compose.demo.yml logs -f frontend"
echo ""
echo "Stop all services:"
echo "  docker-compose -f docker-compose.demo.yml down"
echo ""
echo "========================================="
