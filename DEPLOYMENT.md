# Deployment Guide - Real Estate Price Prediction

This guide covers deployment of the complete Real Estate Price Prediction system with backend, frontend, and all supporting services.

## Table of Contents

- [Local Development](#local-development)
- [Docker Deployment](#docker-deployment)
- [Production Deployment](#production-deployment)
- [Monitoring & Logging](#monitoring--logging)
- [Troubleshooting](#troubleshooting)

## Local Development

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker & Docker Compose
- Git

### Setup

1. **Clone the repository**
```bash
git clone <repository-url>
cd real-estate-ml-system
```

2. **Create environment file**
```bash
cp .env.example .env
```

3. **Start supporting services (Kafka, MongoDB, Prometheus)**
```bash
docker-compose up -d zookeeper kafka mongodb mongo-express prometheus grafana
```

4. **Backend Setup**

```bash
# Create Python virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

5. **Frontend Setup**

```bash
cd frontend

# Install dependencies
npm install

# Create environment file
cp .env.example .env

cd ..
```

### Running Services

**Terminal 1 - Kafka Producer (Scraper)**
```bash
python scraper/kafka_producer.py \
  --bootstrap-servers localhost:9092 \
  --max-pages 5 \
  --limit 100
```

**Terminal 2 - Kafka to MongoDB (Processor)**
```bash
python processing/kafka_to_mongo.py \
  --bootstrap-servers localhost:9092 \
  --mongo-uri mongodb://localhost:27017/
```

**Terminal 3 - Model API**
```bash
python -m uvicorn modeling.api:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
```

**Terminal 4 - Frontend Dev Server**
```bash
cd frontend
npm run dev
```

### Accessing Services

- **Frontend**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **MongoDB Express**: http://localhost:8081
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3001

## Docker Deployment

### Build Images

```bash
# Build all services
docker-compose build

# Or build individual services
docker build -f modeling/Dockerfile.api -t real-estate-api:latest .
docker build -f frontend/Dockerfile -t real-estate-frontend:latest frontend/
```

### Run with Docker Compose

```bash
# Start all services
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Verify Services

```bash
# Check API health
curl http://localhost:8000/health

# Check model info
curl http://localhost:8000/model/info

# Check frontend
curl http://localhost:3000
```

## Production Deployment

### Pre-deployment Checklist

- [ ] Environment variables configured (`.env`)
- [ ] Models trained and validated
- [ ] Database backups configured
- [ ] CORS origins configured
- [ ] SSL/TLS certificates prepared
- [ ] Monitoring configured
- [ ] Log rotation configured

### Deployment Options

#### Option 1: Kubernetes

```bash
# Create namespace
kubectl create namespace real-estate

# Apply configurations
kubectl apply -f k8s/

# Check deployments
kubectl get deployments -n real-estate
```

#### Option 2: VM/Server Deployment

1. **Install dependencies**
```bash
sudo apt-get update
sudo apt-get install -y python3.11 nodejs npm docker.io docker-compose postgresql
```

2. **Clone and setup**
```bash
git clone <repository-url> /opt/real-estate-predictor
cd /opt/real-estate-predictor

# Create .env from template
cp .env.example .env
# Edit .env for production
nano .env
```

3. **Start services with systemd**

Create `/etc/systemd/system/real-estate-api.service`:
```ini
[Unit]
Description=Real Estate Price Prediction API
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=/opt/real-estate-predictor
ExecStart=/usr/bin/docker-compose up
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable real-estate-api
sudo systemctl start real-estate-api
```

### SSL/TLS Configuration

With Nginx reverse proxy:

```nginx
upstream api {
    server localhost:8000;
}

upstream frontend {
    server localhost:3000;
}

server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;

    location / {
        proxy_pass http://api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}

server {
    listen 443 ssl http2;
    server_name example.com;

    ssl_certificate /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;

    location / {
        proxy_pass http://frontend;
    }

    location /api/ {
        proxy_pass http://api/;
    }
}
```

## Monitoring & Logging

### Prometheus

Access dashboard: http://localhost:9090

Key metrics:
- `kafka_messages_consumed_total`
- `kafka_messages_processed_total`
- `db_writes_success_total`
- `processing_duration_seconds`
- `model_mae_vnd`
- `model_rmse_vnd`
- `model_r2`

### Grafana

Access dashboard: http://localhost:3000 (default: admin/admin)

Pre-configured dashboards in `monitoring/grafana/dashboards/`

### Logs

**Docker Compose logs:**
```bash
docker-compose logs -f [service-name]
```

**Application logs:**
```bash
# API logs
tail -f logs/api.log

# Processing logs
tail -f logs/processor.log
```

## Scaling Considerations

### Horizontal Scaling

1. **Multiple Scrapers**
   - Run multiple instances with different page ranges
   - Distribute load via round-robin

2. **Multiple Processors**
   - Use consumer group for parallel processing
   - Set `num.partitions` for Kafka topic

3. **API Load Balancing**
   - Deploy multiple API instances behind load balancer
   - Use Nginx or HAProxy

### Vertical Scaling

- Increase CPU/Memory for prediction service
- Configure model serving cache

### Database

- Add MongoDB replica set for high availability
- Implement sharding for large datasets
- Regular backups and point-in-time recovery

## Backup & Recovery

### Database Backup

```bash
# Backup MongoDB
mongodump --uri="mongodb://localhost:27017" --out=/backup/mongodb

# Restore MongoDB
mongorestore --uri="mongodb://localhost:27017" /backup/mongodb
```

### Model Versioning

Models are automatically versioned:
```
artifacts/models/
├── price_model_v20240501_120000.joblib
├── price_model_v20240501_110000.joblib
└── price_model_current.joblib (symlink to latest)
```

Rollback:
```bash
# Update symlink
ln -sf price_model_v20240501_110000.joblib artifacts/models/price_model.joblib
```

## Troubleshooting

### Common Issues

#### API won't start
```bash
# Check model file exists
ls -la artifacts/models/price_model.joblib

# Check Python dependencies
pip list | grep fastapi

# Check port not in use
lsof -i :8000
```

#### MongoDB connection issues
```bash
# Test connection
mongosh mongodb://localhost:27017

# Check logs
docker-compose logs mongodb
```

#### Frontend can't reach API
```bash
# Check CORS configuration
curl -H "Origin: http://localhost:3000" \
     -H "Access-Control-Request-Method: POST" \
     http://localhost:8000/health

# Verify API_URL in frontend .env
cat frontend/.env
```

#### High latency
```bash
# Check server load
top

# Check Prometheus metrics
curl http://localhost:9090/api/v1/instant?query=processing_duration_seconds

# Check network connectivity
ping mongodb
ping kafka
```

### Performance Optimization

1. **API Response Time**
   - Cache model in memory ✓ (already implemented)
   - Use async request processing
   - Add response compression

2. **Database Performance**
   - Create appropriate indexes ✓ (already implemented)
   - Implement connection pooling
   - Regular maintenance tasks

3. **Frontend Performance**
   - Code splitting
   - Lazy loading
   - Image optimization

## Support & Contact

For deployment issues:
1. Check logs: `docker-compose logs`
2. Review monitoring: http://localhost:9090
3. Check error details in Grafana: http://localhost:3001

## Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Kafka Documentation](https://kafka.apache.org/documentation/)
- [MongoDB Documentation](https://docs.mongodb.com/)
- [Docker Documentation](https://docs.docker.com/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
