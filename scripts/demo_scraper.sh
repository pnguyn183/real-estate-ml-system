#!/bin/bash
set -e

echo "========================================="
echo "DEMO SCRAPER - Optimized for Quick Execution"
echo "========================================="

# Get demo parameters from environment
MAX_PAGES=${DEMO_MAX_PAGES:-8}
LIMIT=${DEMO_LIMIT:-400}
BOOTSTRAP_SERVERS=${KAFKA_BOOTSTRAP_SERVERS:-kafka:29092}

echo "Demo Parameters:"
echo "  MAX_PAGES: $MAX_PAGES"
echo "  LIMIT: $LIMIT"
echo "  Bootstrap Servers: $BOOTSTRAP_SERVERS"

# Run scraper with demo parameters
echo ""
echo "Starting scraper with demo parameters..."
python kafka_producer.py \
  --bootstrap-servers "$BOOTSTRAP_SERVERS" \
  --max-pages "$MAX_PAGES" \
  --limit "$LIMIT" \
  --request-delay 0.15 \
  --detail-delay 0.05

# Signal completion
touch /app/scrape_done.flag

echo ""
echo "========================================="
echo "Scraper completed successfully!"
echo "========================================="
echo "Flag file created: /app/scrape_done.flag"
