# Dashboard và alert

## Truy cập

Chạy stack:

```bash
docker compose up -d --build prometheus grafana processor trainer
```

Mở Grafana tại http://localhost:3001 bằng `admin/admin`. Datasource Prometheus và dashboard `Real Estate Pipeline` được provision tự động từ thư mục `monitoring/grafana`.

## Panel chính

- `Processor Throughput`: tốc độ consume, xử lý thành công và lỗi từ Kafka.
- `Kafka Consumer Lag`: số message còn lại theo watermark Kafka.
- `Processing and Training Duration`: thời gian xử lý trung bình và thời gian train trung bình.
- `Model Error`: MAE và RMSE của model hiện tại.
- `Training Samples`: số bản ghi dùng để train.
- `Model R2`: điểm R2 của lần train mới nhất.
- `Trainer Freshness`: thời gian từ lần train thành công gần nhất.

## Cách đọc nhanh

Pipeline ổn định khi:

- `failed` gần 0 so với `consumed`.
- `Kafka Consumer Lag` không tăng liên tục.
- `avg processing time` không tăng đột biến.
- `Trainer Freshness` nhỏ hơn chu kỳ train kỳ vọng.
- `Model Error` không tăng mạnh sau một lần train mới.

Nếu `Trainer Freshness` tăng nhưng `Training Samples` không đổi, thường là thiếu dữ liệu đạt điều kiện `is_model_candidate=true`. Nếu `Kafka Consumer Lag` tăng đồng thời throughput thấp, kiểm tra log processor và MongoDB.

## Alert rule

Prometheus nạp rule từ `monitoring/alert_rules.yml`.

Alert hiện có:

- `HighProcessingErrorRate`: lỗi processor quá 5% trong 5 phút.
- `HighKafkaConsumerLag`: consumer lag trên 1000 message trong 5 phút.
- `ProcessingDurationAnomaly`: xử lý trung bình quá 5 giây.
- `ModelTrainingFailed`: trainer báo lỗi trong 1 giờ.
- `ModelTrainingStale`: quá 12 giờ chưa có lần train thành công.
- `DatabaseWriteFailures`: có lỗi ghi MongoDB.

## Query PromQL hữu ích

```promql
rate(kafka_messages_processed_total[5m])
rate(kafka_messages_failed_total[5m]) / clamp_min(rate(kafka_messages_consumed_total[5m]), 1)
kafka_consumer_lag
rate(processing_duration_seconds_sum[5m]) / rate(processing_duration_seconds_count[5m])
model_mae_vnd
model_rmse_vnd
time() - trainer_last_success_timestamp_seconds
```

## Tùy chỉnh dashboard

Chỉnh JSON tại:

```text
monitoring/grafana/dashboards/real_estate_pipeline.json
```

Sau khi sửa, restart Grafana:

```bash
docker compose restart grafana
```
