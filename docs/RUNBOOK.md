# Runbook vận hành

## Mục tiêu

Runbook này dùng để khởi động, giám sát, khôi phục và replay pipeline bất động sản gồm scraper, Kafka, processor, MongoDB, trainer, predictor, Prometheus và Grafana.

## Khởi động

```bash
docker compose up -d --build
```

Endpoint chính:

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001, tài khoản `admin/admin`
- Mongo Express: http://localhost:8081
- Processor metrics: http://localhost:8003/metrics
- Trainer metrics: http://localhost:8001/metrics
- Predictor health: http://localhost:8002/health

## Kiểm tra nhanh

```bash
docker compose ps
docker compose logs -f processor
docker compose logs -f trainer
python scripts/health_check.py
```

Processor phải có metric `kafka_messages_consumed_total`. Trainer phải có metric `trainer_runs_total` và cập nhật `trainer_last_success_timestamp_seconds` sau lần train thành công.

## Predict thử

Sau khi trainer tạo model trong `artifacts/models/price_model.joblib`, gọi:

```bash
curl -X POST http://localhost:8002/predict ^
  -H "Content-Type: application/json" ^
  -d "{\"area_m2\":100,\"bedroom_count\":2,\"bathroom_count\":1,\"floor_count\":1,\"front_width_m\":5,\"road_width_m\":6,\"property_type\":\"apartment\",\"direction\":\"east\",\"legal\":\"redbook\",\"listing_type\":\"sell\",\"province_slug\":\"hanoi\",\"district_slug\":\"dongda\",\"ward_slug\":\"catlinh\",\"project_hint\":\"vinhome\",\"text_features\":\"vinhome apartment\"}"
```

Nếu chưa có model, predictor trả HTTP 503 và trạng thái `/health` là `waiting_for_model`.

## Khôi phục sự cố

Kafka hoặc MongoDB chưa sẵn sàng:

```bash
docker compose ps
docker compose logs kafka mongodb
docker compose restart kafka mongodb
```

Processor lỗi ghi DB:

```bash
docker compose logs processor
```

Kiểm tra collection `dlq_raw` và `invalid_records` trong MongoDB. Các bản ghi lỗi parse hoặc lỗi DB được lưu để điều tra và replay.

Trainer không train:

```bash
docker compose logs trainer
```

Kiểm tra số record đủ điều kiện:

```javascript
db.training_features.countDocuments({is_model_candidate: true, has_target_price: true})
```

Mặc định `MIN_RECORDS_FOR_TRAINING=500`. Có thể giảm biến này trong `docker-compose.yml` cho môi trường demo.

## Offset checkpoint và replay

Processor commit Kafka offset thủ công. Sau mỗi commit thành công, nó ghi checkpoint vào MongoDB collection `offset_checkpoint` với:

- `group_id`
- `topic`
- `partition`
- `last_processed_offset`
- `committed_offset`
- `updated_at`

Để replay từ checkpoint cũ, dừng processor, reset offset consumer group về offset mong muốn bằng Kafka tooling, rồi khởi động lại processor. Mongo checkpoint là nguồn tham chiếu vận hành, còn Kafka consumer group offset vẫn là cơ chế chạy chính.

## Alert chính

- `HighProcessingErrorRate`: tỷ lệ lỗi processor trên 5%.
- `HighKafkaConsumerLag`: lag vượt 1000 message.
- `ProcessingDurationAnomaly`: thời gian xử lý trung bình trên 5 giây.
- `ModelTrainingFailed`: trainer có run lỗi trong 1 giờ gần nhất.
- `ModelTrainingStale`: không có lần train thành công hơn 12 giờ.
- `DatabaseWriteFailures`: phát sinh lỗi ghi MongoDB.

## Dừng hệ thống

```bash
docker compose down
```

Giữ dữ liệu MongoDB bằng volume `mongo_data`. Xóa toàn bộ dữ liệu khi cần reset môi trường:

```bash
docker compose down -v
```
