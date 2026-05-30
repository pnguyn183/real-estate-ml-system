# Runbook Vận Hành

Runbook này dùng để khởi động, kiểm tra, giám sát và khôi phục hệ thống dự đoán giá bất động sản.

## Khởi Động

```bash
docker compose up -d --build
```

Endpoint chính:

- Frontend: http://localhost:3000
- FastAPI: http://localhost:8000
- API docs: http://localhost:8000/docs
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001, tài khoản mặc định `admin/admin`
- Mongo Express: http://localhost:8081
- Processor metrics: http://localhost:8003/metrics
- Trainer metrics: http://localhost:8001/metrics
- Legacy predictor health: http://localhost:8002/health

## Kiểm Tra Nhanh

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f processor
docker compose logs -f trainer
python scripts/health_check.py
```

Kỳ vọng:

- API `/health` trả `ready` khi `artifacts/models/price_model.joblib` tồn tại.
- API `/health` trả `initializing` nếu chưa có model.
- Processor có metric `kafka_messages_consumed_total`.
- Trainer có metric `trainer_runs_total` và cập nhật `trainer_last_success_timestamp_seconds` sau lần train thành công.

## Bootstrap Admin và Token

API chính yêu cầu bearer token cho prediction, model info và quản trị user.

Đăng ký tài khoản đầu tiên:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass1","full_name":"Admin"}'
```

Tài khoản đầu tiên sẽ có role `admin`. Các tài khoản tự đăng ký sau đó mặc định là `user`.

Đăng nhập:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"StrongPass1"}'
```

Lưu token:

```bash
TOKEN="<access_token>"
```

## Predict Qua API Chính

Sau khi trainer tạo `artifacts/models/price_model.joblib`, gọi:

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "area_m2": 100,
    "bedroom_count": 2,
    "bathroom_count": 1,
    "floor_count": 1,
    "front_width_m": 5,
    "road_width_m": 6,
    "property_type": "apartment",
    "direction": "east",
    "legal": "redbook",
    "listing_type": "sell",
    "province_slug": "hanoi",
    "district_slug": "dongda",
    "ward_slug": "catlinh",
    "project_hint": "vinhome",
    "text_features": "vinhome apartment"
  }'
```

Quyền:

- `user`: gọi `/predict`
- `manager`: gọi `/predict`, `/predict/batch`, `/model/info`
- `admin`: toàn quyền và quản lý user

## Predict Qua Legacy Predictor

Service `predictor` ở port `8002` vẫn tồn tại cho luồng legacy và không dùng cùng auth/RBAC với FastAPI chính:

```bash
curl -X POST http://localhost:8002/predict \
  -H "Content-Type: application/json" \
  -d '{"area_m2":100,"bedroom_count":2,"bathroom_count":1,"property_type":"apartment","province_slug":"hanoi","district_slug":"dongda"}'
```

Nếu chưa có model, predictor trả HTTP 503 và trạng thái `/health` là `waiting_for_model`.

## Khôi Phục Sự Cố

### Kafka hoặc MongoDB chưa sẵn sàng

```bash
docker compose ps
docker compose logs kafka mongodb
docker compose restart kafka mongodb
```

### API trả 401 hoặc 403

- `401`: thiếu token, token sai, hoặc token hết hạn. Đăng nhập lại.
- `403`: tài khoản hợp lệ nhưng role không đủ quyền. Dùng admin panel để nâng role nếu cần.

### Processor lỗi ghi DB

```bash
docker compose logs processor
```

Kiểm tra collection `dlq_raw` và `invalid_records` trong MongoDB. Các bản ghi lỗi parse hoặc lỗi DB được lưu để điều tra và replay.

### Trainer không train

```bash
docker compose logs trainer
```

Kiểm tra số record đủ điều kiện:

```javascript
db.training_features.countDocuments({is_model_candidate: true, has_target_price: true})
```

Mặc định `MIN_RECORDS_FOR_TRAINING=30` trong Docker Compose nếu không truyền biến môi trường khác.

## Offset Checkpoint và Replay

Processor commit Kafka offset thủ công. Sau mỗi commit thành công, nó ghi checkpoint vào MongoDB collection `offset_checkpoint` với:

- `group_id`
- `topic`
- `partition`
- `last_processed_offset`
- `committed_offset`
- `updated_at`

Để replay từ checkpoint cũ, dừng processor, reset offset consumer group về offset mong muốn bằng Kafka tooling, rồi khởi động lại processor.

## Alert Chính

- `HighProcessingErrorRate`: tỷ lệ lỗi processor trên 5%
- `HighKafkaConsumerLag`: lag vượt 1000 message
- `ProcessingDurationAnomaly`: thời gian xử lý trung bình trên 5 giây
- `ModelTrainingFailed`: trainer có run lỗi trong 1 giờ gần nhất
- `ModelTrainingStale`: không có lần train thành công hơn 12 giờ
- `DatabaseWriteFailures`: phát sinh lỗi ghi MongoDB

## Dừng Hệ Thống

```bash
docker compose down
```

Giữ dữ liệu MongoDB bằng volume `mongo_data`. Xóa toàn bộ dữ liệu khi cần reset môi trường:

```bash
docker compose down -v
```
