#!/bin/bash
set -e

echo "========================================="
echo "DEMO TRAINER - Optimized for Quick Execution"
echo "========================================="

# Get parameters from environment
MONGO_URI=${MONGO_URI:-mongodb://mongodb:27017/}
MONGO_DB=${MONGO_DB:-real_estate_db}
MODEL_PATH=${MODEL_PATH:-artifacts/models/price_model.joblib}
METRICS_PATH=${METRICS_PATH:-artifacts/price_model_metrics.json}
WAIT_SECONDS=${DEMO_WAIT_SECONDS:-30}

echo "Configuration:"
echo "  MongoDB URI: $MONGO_URI"
echo "  Database: $MONGO_DB"
echo "  Model Path: $MODEL_PATH"
echo "  Wait for data: $WAIT_SECONDS seconds"

# Wait for data to be processed
echo ""
echo "Waiting $WAIT_SECONDS seconds for data processing..."
sleep "$WAIT_SECONDS"

# Check if data exists
echo "Checking for training data..."
RECORD_COUNT=0
for i in {1..5}; do
    echo "Attempt $i to connect to MongoDB..."
    RECORD_COUNT=$(python3 << 'EOF'
import sys
from pymongo import MongoClient

try:
    client = MongoClient("mongodb://mongodb:27017/", serverSelectionTimeoutMS=5000)
    db = client["real_estate_db"]
    collection = db["training_features"]
    count = collection.count_documents({"price_vnd": {"$gt": 0}})
    print(count)
    client.close()
except Exception as e:
    print(0, file=sys.stderr)
    print(0)
EOF
) || true
    
    if [ "$RECORD_COUNT" -gt 0 ]; then
        echo "Found $RECORD_COUNT records in MongoDB"
        break
    fi
    echo "No records found yet, retrying in 5 seconds..."
    sleep 5
done

if [ "$RECORD_COUNT" -eq 0 ]; then
    echo "Warning: No training data found, but proceeding with training..."
fi

# Run trainer
echo ""
echo "Starting model training..."
python train_model.py \
  --mongo-uri "$MONGO_URI" \
  --mongo-db "$MONGO_DB" \
  --collection training_features \
  --model-path "$MODEL_PATH" \
  --metrics-path "$METRICS_PATH"

TRAIN_EXIT_CODE=$?

if [ $TRAIN_EXIT_CODE -eq 0 ]; then
    # Signal completion
    touch /app/train_done.flag
    echo ""
    echo "========================================="
    echo "Training completed successfully!"
    echo "========================================="
    echo "Flag file created: /app/train_done.flag"
    echo "Model saved to: $MODEL_PATH"
    echo "Metrics saved to: $METRICS_PATH"
else
    echo "Training failed with exit code $TRAIN_EXIT_CODE"
    exit $TRAIN_EXIT_CODE
fi
