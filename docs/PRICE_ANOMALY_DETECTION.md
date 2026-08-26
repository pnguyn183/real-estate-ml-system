# Phát hiện bất thường giá bất động sản (Price Anomaly Detection)

## Mục tiêu và vị trí trong pipeline

Module được tích hợp tại `processing/price_anomaly.py` và được gọi trong `KafkaToMongoPipeline.process_payload()` sau khi record đã được normalize/validate, nhưng **trước** khi ghi vào `training_features`. Luồng thực tế là:

```text
Scraper → Kafka real_estate_raw → normalize price/area → validate
        → historical group-based IQR → anomaly metadata
        → MongoDB training_features → Kafka real_estate_features → ML trainer
```

Raw source vẫn được upsert nguyên trạng vào `listings_raw` (`processing/kafka_to_mongo.py`). Chỉ normalized feature document nhận metadata bất thường; record bị gắn cờ không bị xoá.

## Vì sao dùng giá trên mét vuông

Biến phát hiện là `price_per_m2_vnd = price_vnd / area_m2`, thay vì tổng giá. Tổng giá phụ thuộc mạnh vào diện tích nên một căn lớn có thể đắt nhưng hoàn toàn bình thường. Hàm `safe_price_per_m2()` chỉ trả về số dương hữu hạn; thiếu giá/diện tích, zero, số âm, `NaN` hoặc `Infinity` được giữ là invalid và có trạng thái `UNAVAILABLE`, không bị biến đổi thành giá hợp lệ.

## Group-based IQR

So sánh chỉ có ý nghĩa giữa bất động sản tương đồng. Nhóm chính mặc định là:

`province_slug + district_slug + property_type`

Nếu nhóm chính không đủ tin cậy, module thử nhóm rộng hơn:

`province_slug + property_type`

`ward_slug` không nằm trong cấu hình mặc định vì nhóm thường quá nhỏ; area range cũng không được thêm vì giá/m² đã normalise ảnh hưởng diện tích và thêm range sẽ làm phân mảnh baseline. Các cột đều tồn tại trong schema hiện tại (`processing/kafka_to_mongo.py`).

Với từng nhóm historical, module tính:

- `Q1 = percentile_25(price_per_m2_vnd)`
- `Q3 = percentile_75(price_per_m2_vnd)`
- `IQR = Q3 - Q1`
- `lower_bound = Q1 - multiplier × IQR`
- `upper_bound = Q3 + multiplier × IQR`

Record có giá/m² nhỏ hơn lower bound được gắn `LOW`; lớn hơn upper bound được gắn `HIGH`. Default multiplier là 1.5.

## Historical baseline và cách tránh contamination

Trước khi upsert record đang consume, processor gọi `refresh_if_needed()` để đọc historical `training_features`, loại URL hiện tại, rồi xây cache threshold. Vì vậy record mới không định nghĩa threshold cho chính nó. Cache refresh mặc định 300 giây để tránh scan MongoDB ở mỗi message. Threshold được lưu audit vào collection `price_anomaly_thresholds` với `config_id`, group, sample size, quartile và timestamp.

Hệ thống hiện chưa có materialized baseline job hoặc distributed Data Lake; lần refresh đầu sau startup cần scan projection MongoDB relevant. Đây là hạn chế cần thay bằng scheduled aggregation/materialized thresholds khi dữ liệu lớn.

## Small group và zero IQR

- Group có ít hơn `PRICE_ANOMALY_MIN_GROUP_SIZE` (default 30) không có bounds và record nhận `UNAVAILABLE` / `insufficient_baseline_group` nếu fallback cũng không đủ.
- Nếu `IQR = 0`, module **không** coi mọi giá khác median là anomaly. Nhóm mang trạng thái `zero_iqr`; detector sẽ thử fallback rộng hơn, nếu fallback không usable thì mark `UNAVAILABLE` / `zero_iqr_baseline`.
- Thiếu group value: `UNAVAILABLE` / `missing_group_values`.
- Group mới chưa có historical data: `UNAVAILABLE` / `insufficient_baseline_group`.

Lựa chọn này ưu tiên tránh false positive: luxury, waterfront, commercial, prime-location hoặc rare property là outlier thống kê nhưng không tự động là bad data.

## Metadata lưu trong `training_features`

| Field | Ý nghĩa |
|---|---|
| `price_per_m2_vnd` | Giá/m² dương hữu hạn, hoặc `null` nếu invalid |
| `is_price_anomaly` | `true` chỉ khi vượt IQR bound |
| `price_anomaly_status` | `NORMAL`, `FLAGGED`, hoặc `UNAVAILABLE` |
| `price_anomaly_type` | `HIGH` / `LOW` / `null` |
| `price_anomaly_score` | Khoảng cách vượt boundary chia IQR; không phải probability |
| `price_anomaly_group`, `_group_columns` | Nhóm baseline thực tế (primary hoặc fallback) |
| `price_anomaly_baseline_size` | Số quan sát baseline |
| `price_anomaly_q1`, `_q3`, `_iqr`, `_lower_bound`, `_upper_bound` | Bằng chứng giải thích flag |
| `price_anomaly_reason` | Lý do flag/unavailable |

Record `FLAGGED` vẫn được publish vào `real_estate_features` và lưu Mongo để review/audit. Repository chưa có listing-analytics endpoint, nên anomaly metadata không được đưa vào FastAPI prediction response; không tạo API riêng không cần thiết.

## Unified review status and optional LLM review

Sau bước validation/IQR, processor ghi schema audit chung: `listing_review_status` (`NORMAL`, `SUSPICIOUS`, `INVALID`), `is_anomaly`, `anomaly_type`/`anomaly_types`, `anomaly_score`, `anomaly_reason`, `detection_method`, và `detected_at`. Validation error là `DATA_QUALITY`/`INVALID`; fingerprint lặp lại là `DUPLICATE`/`SUSPICIOUS`; IQR flag là `PRICE`/`SUSPICIOUS`. Không trạng thái nào tự xóa record hoặc thay đổi `price_vnd`.

`processing/llm_review.py` chỉ được gọi sau khi record đã là `SUSPICIOUS`. Mặc định `LLM_REVIEW_ENABLED=false`, nên không có network/API call. Một provider tương lai phải trả đúng JSON schema đã validate; response malformed/timeout trở thành `llm_review_status=UNAVAILABLE` trong khi kết quả deterministic vẫn giữ nguyên. LLM không được phép thay đổi ground-truth price.

## Configuration

| Environment variable | Default | Mô tả |
|---|---:|---|
| `PRICE_ANOMALY_IQR_MULTIPLIER` | `1.5` | IQR multiplier dương |
| `PRICE_ANOMALY_MIN_GROUP_SIZE` | `30` | Số historical observations tối thiểu/group |
| `PRICE_ANOMALY_GROUP_COLUMNS` | `province_slug,district_slug,property_type` | Group chính, comma-separated |
| `PRICE_ANOMALY_FALLBACK_GROUP_COLUMNS` | `province_slug,property_type` | Group fallback, comma-separated |
| `PRICE_ANOMALY_REFRESH_SECONDS` | `300` | TTL cache baseline MongoDB |
| `PRICE_ANOMALY_TRAINING_POLICY` | `FLAG` | `KEEP`, `FLAG`, hoặc `EXCLUDE` |

`KEEP` và `FLAG` đều giữ record flagged trong training; `FLAG` là default để downstream/audit nhìn thấy metadata. `EXCLUDE` thêm `is_price_anomaly != true` vào Mongo training query và filter lại trong `RealEstatePriceModel.train`. Không có anomaly field nào được thêm vào ML feature list, nên price-derived data-quality metadata không thành target leakage feature.

## Logging, monitoring và giới hạn

Refresh baseline log: số record processed/valid, số group, usable group, small/zero-IQR group. Processor log anomaly result khi refresh hoặc có flag; khi đóng process log tổng record valid, anomaly count/percentage và skipped. Chưa có Prometheus anomaly metric/dashboard; đây là improvement tiếp theo hợp lý.

IQR là rule thống kê, không chứng minh fraud hay sai dữ liệu. Nó không thay thế domain review, temporal/geospatial features, provenance checks, dedup semantic hoặc model validation. Với dữ liệu lớn, xây scheduled threshold materialization theo snapshot date, indexes phù hợp và segment/time-aware baseline; không dùng test/future target data để evaluate ML.
