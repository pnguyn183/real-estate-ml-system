#!/bin/bash
set -e

echo "========================================="
echo "Stopping Real Estate Pipeline"
echo "========================================="

docker compose down

echo ""
echo "All services stopped."
echo ""
echo "To remove volumes (database data):"
echo "  docker compose down -v"
