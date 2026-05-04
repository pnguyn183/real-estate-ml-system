# Quick Reference Guide

## 🚀 Getting Started in 5 Minutes

### Option 1: Docker (Recommended)
```bash
docker-compose up -d
# Wait 30 seconds for services to start
# Open http://localhost:3000
```

### Option 2: Local Development
```bash
# Terminal 1: Backend API
python -m uvicorn modeling.api:app --reload

# Terminal 2: Frontend
cd frontend && npm run dev

# Open http://localhost:3000
```

---

## 📍 Service Endpoints

| Service | URL | Purpose |
|---------|-----|---------|
| Frontend | http://localhost:3000 | Web UI |
| API | http://localhost:8000 | REST API |
| API Docs | http://localhost:8000/docs | Swagger UI |
| MongoDB UI | http://localhost:8081 | Database browser |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards |

---

## 🔌 API Quick Reference

### Health Check
```bash
curl http://localhost:8000/health
```

### Predict Single Property
```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "area_m2": 100,
    "bedroom_count": 2,
    "bathroom_count": 1,
    "property_type": "apartment",
    "province_slug": "hanoi",
    "district_slug": "dongda"
  }'
```

### Response Format
```json
{
  "predicted_price_vnd": 2500000000,
  "predicted_price_billion_vnd": 2.5,
  "prediction_date": "2024-01-01T12:00:00Z",
  "latency_ms": 45.2
}
```

---

## 📁 Important Files

| File | Purpose |
|------|---------|
| `modeling/api.py` | REST API service |
| `modeling/price_model.py` | ML model implementation |
| `modeling/train_model.py` | Model training script |
| `processing/kafka_to_mongo.py` | Data processor |
| `scraper/kafka_producer.py` | Web scraper |
| `frontend/src/App.tsx` | React main component |
| `frontend/src/api/client.ts` | API client |
| `.env.example` | Configuration template |
| `DEPLOYMENT.md` | Full deployment guide |
| `README.md` | Project overview |

---

## 🛠️ Common Commands

### Backend
```bash
# Start API server
python -m uvicorn modeling.api:app --reload

# Train model
python modeling/train_model.py --mongo-uri mongodb://localhost:27017/

# Run tests
pytest tests/

# Check code quality
flake8 . --max-line-length=100
```

### Frontend
```bash
# Install dependencies
cd frontend && npm install

# Development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Type checking
npm run type-check

# Linting
npm run lint
```

### Docker
```bash
# Start all services
docker-compose up -d

# Stop all services
docker-compose down

# View logs
docker-compose logs -f [service-name]

# Rebuild services
docker-compose build --no-cache

# Reset everything
docker-compose down -v && docker-compose up -d
```

---

## 🔧 Environment Configuration

### Key Variables in `.env`

**API Configuration**
```bash
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=*,localhost:3000
```

**Database**
```bash
MONGO_URI=mongodb://localhost:27017/
MONGO_DB=real_estate_db
```

**Kafka**
```bash
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_RAW_TOPIC=real_estate_raw
KAFKA_CLEAN_TOPIC=real_estate_features
```

**Model**
```bash
MODEL_PATH=artifacts/models/price_model.joblib
```

---

## 📊 Property Fields

### Required (For Prediction)
- `area_m2` - Property area in square meters
- `property_type` - apartment, house, land, commercial, office
- `province_slug` - hanoi, hcmc, danang, haiphong

### Optional (Improves Accuracy)
- `bedroom_count` - Number of bedrooms
- `bathroom_count` - Number of bathrooms
- `floor_count` - Number of floors
- `front_width_m` - Front width in meters
- `road_width_m` - Road width in meters
- `direction` - north, south, east, west, etc.
- `legal` - redbook, pinkbook, other
- `district_slug` - District code
- `ward_slug` - Ward code

---

## 🐛 Troubleshooting

### API Won't Start
```bash
# Check if port is in use
lsof -i :8000

# Kill process on port 8000
kill -9 $(lsof -t -i:8000)

# Check logs
docker-compose logs api
```

### Frontend Won't Connect
```bash
# Check if API is running
curl http://localhost:8000/health

# Check .env configuration
cat frontend/.env | grep VITE_API_URL

# Check browser console for errors
# Open DevTools: F12 > Console
```

### Docker Issues
```bash
# Rebuild from scratch
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d

# Check service health
docker-compose ps

# View full logs
docker-compose logs
```

### Database Connection
```bash
# Test MongoDB connection
mongosh mongodb://localhost:27017/

# Check if MongoDB is running
docker-compose logs mongodb

# Reset MongoDB
docker-compose down -v mongodb
docker-compose up -d mongodb
```

---

## 📈 Performance Tips

1. **API Response Time**
   - Model caching is automatic
   - Typical latency: <100ms
   - Batch predictions: ~5-10ms per item

2. **Database Optimization**
   - Indexes are created automatically
   - Check MongoDB usage: http://localhost:8081

3. **Frontend Performance**
   - Vite provides fast development
   - Production build is optimized
   - ~50KB gzipped

---

## 🔐 Security Notes

Currently the system is open for development. For production:

1. Add API authentication
   ```python
   from fastapi.security import HTTPBearer
   security = HTTPBearer()
   ```

2. Add rate limiting
   ```bash
   pip install slowapi
   ```

3. Enable HTTPS
   - Use nginx with SSL certificates
   - Update CORS_ORIGINS to your domain

4. Database credentials
   - Set MONGO_URI with authentication
   - Use .env for secrets (never commit)

---

## 📚 Documentation

- **[README.md](../README.md)** - Project overview
- **[DEPLOYMENT.md](../DEPLOYMENT.md)** - Deployment guide
- **[PROJECT_STATUS.md](PROJECT_STATUS.md)** - What was built
- **[docs/](./)** - Business and technical documentation
- **[API Docs](http://localhost:8000/docs)** - Interactive API documentation

---

## 🎯 Next Steps

1. ✅ Start services: `docker-compose up -d`
2. ✅ Access frontend: http://localhost:3000
3. ✅ Try a prediction: Fill form and click "Predict Price"
4. ✅ Check API docs: http://localhost:8000/docs
5. ✅ Review logs: `docker-compose logs -f`

---

## 📞 Support

For detailed troubleshooting, see [DEPLOYMENT.md](../DEPLOYMENT.md#troubleshooting)

**Created**: May 1, 2026  
**Last Updated**: May 1, 2026  
**Version**: 1.0.0
