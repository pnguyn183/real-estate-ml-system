# Báo cáo hệ thống với Stress Agent và Extraction Agent

> Bản báo cáo trên nhánh `main`, chốt phạm vi ngày 03/10/2026: pipeline bất động sản cùng hai agent sinh tải và trích xuất dữ liệu. Phần điều tiết luồng, dự báo traffic và tự chỉnh tài nguyên Docker được giữ cho nhánh `dev`, không thuộc bản này.

## 1. Phạm vi đã triển khai

Hệ thống sử dụng Kafka ba broker, ba Processor cùng consumer group, MongoDB,
trainer mô hình giá, FastAPI, frontend và Prometheus/Grafana. Hai agent là:

| Thành phần | Chức năng | Mã nguồn |
|---|---|---|
| Stress Agent | Sinh dữ liệu tổng hợp theo seed, scenario, tốc độ và thời lượng được cấu hình; ghi nhận Kafka ACK, lỗi và kết quả chạy | [stress.py](../agents/stress.py), [generator.py](../agents/generator.py) |
| Extraction Agent (`ai-agent`) | Gọi model bên ngoài để trích xuất trường thiếu từ văn bản, kiểm tra schema/evidence, cache và gửi kết quả cho Processor | [worker.py](../agents/worker.py), [extraction.py](../agents/extraction.py), [providers.py](../agents/providers.py) |

Luồng nghiệp vụ:

```text
Crawler -> real_estate_raw -> Processor -> MongoDB -> Trainer -> API / Frontend
                                 |
                                 +-> real_estate_ai_input -> Extraction Agent
                                       -> real_estate_ai_results -> Processor

Stress Agent -> real_estate_stress_raw -> Processor -> real_estate_stress_db
```

### Stress Agent

- Hỗ trợ kịch bản dữ liệu và burst theo cấu hình, giới hạn thời lượng/số bản ghi.
- Bản ghi có dấu synthetic, run ID và scenario; dữ liệu thử nghiệm được lưu ở
  database riêng và bị loại khỏi các đường huấn luyện chính.
- Tốc độ sinh tải được cấu hình trước; các mức `normal/medium/high` là profile
  sinh dữ liệu, không phải trạng thái máy do agent học được.
- Stress vẫn dùng chung broker/Processor với dữ liệu thật. Tách database không
  đồng nghĩa tách tài nguyên xử lý.

### Extraction Agent

- Bản ghi có đủ trường được xử lý bằng parser/validator hiện có.
- Khi bật fallback, Processor gửi yêu cầu bất đồng bộ cho agent đối với bản ghi
  đủ điều kiện; kết quả quay lại Processor để kiểm tra và lưu trữ.
- Có timeout, retry có giới hạn, giới hạn số lời gọi, circuit breaker và cache.
  Đây là bảo vệ lời gọi API của extraction, không phải bộ điều khiển tài nguyên.
- Hai agent mặc định tắt trong Compose; cấu hình model và API key ở môi trường
  cục bộ khi cần chạy extraction thật. Xem [hướng dẫn triển khai](../DEPLOYMENT.md).

Các biểu đồ trong [Agent Operations](../monitoring/grafana/dashboards/agent_operations.json)
phục vụ quan sát hoạt động AI/stress. Không dùng R² của mô hình giá để đánh giá
chất lượng extraction hoặc hiệu quả sinh tải.

## 2. Kiểm chứng dữ liệu sau Gemini

**Đã xác minh thành công một record qua Kafka → ai-agent → Google Gemini →
parsed extraction → MongoDB ngày 24/09/2026.** Model thực sự trả kết quả là
`gemini-3.6-flash`. Đây là một input tổng hợp không chứa thông tin cá nhân,
được gửi tới provider thật; kết quả không được mock.

### Từ lỗi model cũ đến lần gọi thành công

Lượt trước với `gemini-2.5-flash`, event
`aaec37e79debbcdece5b940ba080fb888aab4e1977b28f6fd0e71c652f97d637`,
trả HTTP `404 NOT_FOUND`: provider thông báo model không còn khả dụng cho
người dùng mới và đề cập `models/gemini-3.6-flash`. Bằng chứng lỗi được giữ
riêng tại [gemini-clean-audit-model-404.json](../runtime/research/gemini-clean-audit-model-404.json);
lượt đó không có parsed extraction hay cleaning thành công. Lần thăm dò đầu
`67660e15dc29203e2412906657c5c094d1e77f350dbed4f8915c985cfea1405d`
cũng trả 404, lưu tại `runtime/research/gemini-clean-audit-attempt1.json`.

Ngày 24/09, dùng credential đang có trong container để gọi API model listing,
không đoán tên model:

- `GET https://generativelanguage.googleapis.com/v1beta/openai/models`
  trả HTTP 200, có ID `models/gemini-3.6-flash`.
- `GET https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash`
  trả HTTP 200, version `3.6-flash-07-2026`, hỗ trợ `generateContent`.
- Request extraction dùng tên `gemini-3.6-flash`; response thành công cũng ghi
  đúng model này. Việc model có trong listing chưa tự chứng minh gọi được:
  bằng chứng quyết định là HTTP 200 và extraction dưới đây.

Hai kết quả discovery được lưu tại
[model-discovery.json](../runtime/research/gemini-verification-runtime/model-discovery.json)
và [model-selected.json](../runtime/research/gemini-verification-runtime/model-selected.json),
không chứa authentication header hoặc API key.

### Tích hợp và điều kiện fallback đã kiểm tra từ code

```text
real_estate_raw (production) / real_estate_stress_raw (kiểm thử)
  -> processing/kafka_to_mongo.py: process_payload -> _enqueue_ai
  -> real_estate_ai_input
  -> agents/worker.py: run -> handle_message -> handle
  -> agents/extraction.py: ExtractionService.extract (tạo messages)
  -> agents/providers.py: OpenAICompatibleProvider.extract (HTTP POST)
  -> ExtractionOutput.model_validate_json -> merge_extraction
  -> real_estate_ai_results
  -> agents/results.py: handle_ai_result -> processor validation
  -> training_features hoặc invalid_records trong DB tương ứng
```

Worker dùng consumer group `real_estate_ai_extraction`; processor xử lý kết quả
trong group `real_estate_training_pipeline`. Kết quả lỗi được lưu thêm trong
`ai_failures` và gửi tới `real_estate_ai_dlq`.

Record được chuyển sang AI khi `extraction_routing_errors` phát hiện giá/diện
tích không hợp lệ hoặc thiếu loại bất động sản, tỉnh hay quận. Đồng thời phải
có URL và source text, không có lỗi identity/normalization cản trở, giá không
phải thỏa thuận, chưa có trạng thái AI kết thúc và không chạy `skip_ai`.
Production cần `AI_FALLBACK_ENABLED=true`; luồng stress cần
`AI_STRESS_ENABLED=true` ở processor và worker. `AI_ENABLED=true` cùng partition
assignment tự nó không chứng minh đã có HTTP request.

Lượt thành công chỉ tạm bật AI stress cho ba processor và ai-agent, đặt audit
đúng event ID và đổi `LLM_MODEL` qua Compose override. Điều kiện fallback,
prompt, retry và quy tắc merge production giữ nguyên. Đã gửi **đúng một record
mới**, không publish lại khi provider trả lỗi tạm thời.

### Input thực tế

Event ID:
`452f467651b3c2a59c92243fc0dd132780915089195356d184e273e9d3ea047d`.

Raw record có URL
`https://synthetic.invalid/gemini-live-cad79ca98aa54b8085df5ebf3f2abe0e/explicit-facts`,
`is_synthetic=true`, `source_type=synthetic`, title
`Synthetic Gemini pipeline verification`, description như dưới đây.
Record giữ `property_type=apartment`, `listing_type=sell`,
`province_slug=ho-chi-minh`, `district_slug=quan-1`, nhưng chưa có
`price_text`, `area_text`, `bedroom_text`, `bathroom_text`.
Normalizer báo `missing_or_invalid_price_vnd` và
`missing_or_invalid_area_m2`, đúng điều kiện fallback hiện có.

Nội dung user message thực tế, trình bày lại JSON để dễ đọc:

```json
{
  "source_text": "title: Synthetic Gemini pipeline verification\ndescription: Căn hộ bán, diện tích 80 m2, tổng giá 8 tỷ đồng, 2 phòng ngủ và 2 phòng tắm.\nproperty_type: apartment",
  "known_fields": {
    "property_type": "apartment",
    "listing_type": "sell",
    "province_slug": "ho-chi-minh",
    "district_slug": "quan-1"
  }
}
```

System message chứa schema `ExtractionOutput`, yêu cầu chỉ trích xuất sự kiện
có trong source text, bỏ qua chỉ dẫn nhúng trong tin, không suy diễn vị trí,
không ghi đè known fields và cung cấp trích dẫn nguyên văn cho từng giá trị.
Toàn bộ prompt/messages và schema thực sự gửi đi được lưu trong
`gemini_input[*].request.messages` của artifact, gồm cả các lần retry.

Request dùng provider `openai_compatible`, model `gemini-3.6-flash`, base URL
`https://generativelanguage.googleapis.com/v1beta/openai`, POST
`/chat/completions`, `temperature=0`, `max_tokens=1600`, `stream=false`,
`response_format={"type":"json_object"}`. Audit không lưu header xác thực.

### Output thực tế, parse và record cuối

Một record phát sinh **ba HTTP attempts** theo retry hiện có:

| Attempt | Thời điểm response (UTC, 24/09/2026) | HTTP | Kết quả |
|---|---|---:|---|
| 1 | 08:13:05.924424 | 503 | UNAVAILABLE, provider báo nhu cầu cao |
| 2 | 08:13:10.068373 | 503 | UNAVAILABLE, provider báo nhu cầu cao |
| 3 | 08:13:18.966259 | 200 | Nội dung extraction hợp lệ |

HTTP 200 tương ứng **15:13:18 giờ Việt Nam**, response ID
`mNu0aqrmC6jQg8UP4eHe-Q8`, model `gemini-3.6-flash`.
Toàn bộ `choices[0].message.content` thực tế, chỉ định dạng lại JSON:

```json
{
  "fields": {
    "price_vnd": 8000000000,
    "area_m2": 80.0,
    "bedroom_count": 2,
    "bathroom_count": 2,
    "floor_count": null,
    "front_width_m": null,
    "road_width_m": null,
    "property_type": null,
    "listing_type": null,
    "direction": null,
    "legal": null,
    "furniture": null,
    "province": null,
    "district": null,
    "address": null
  },
  "confidence": 1.0,
  "evidence": {
    "price_vnd": "tổng giá 8 tỷ đồng",
    "area_m2": "diện tích 80 m2",
    "bedroom_count": "2 phòng ngủ",
    "bathroom_count": "2 phòng tắm"
  }
}
```

Raw HTTP body đầy đủ, gồm response envelope, được giữ tại
`gemini_output[2].raw_response`. SHA-256 của **audit payload response**
(`http_status` và `raw_response` sau sanitization), không phải riêng chuỗi body:
`a940b791b34e1c5ebf03a88a4705d7239f3590777ac947bc6f187312a0eb00f6`.

Pydantic parse thành công; `parsed_response[0].parsed_response` giữ các giá trị
trên, chuyển `price_vnd` thành float `8000000000.0`; các field tùy chọn
không có giá trị giữ `null`, không bổ sung suy đoán. Confidence 1.0 là giá trị
Gemini tự trả về, **không phải phép đo độ chính xác trên dataset**. Các trích
dẫn evidence qua kiểm tra có trong input. Bước merge chỉ bổ sung field raw
đang thiếu; processor sau đó normalize thành record cuối:

| Field raw được merge | Trước AI | Sau merge | Field chuẩn hóa trong final record |
|---|---|---|---|
| `price_text` | chưa có | `8.0 tỷ` | `price_vnd=8000000000.0` |
| `area_text` | chưa có | `80.0 m2` | `area_m2=80.0` |
| `bedroom_text` | chưa có | `2` | `bedroom_count=2` |
| `bathroom_text` | chưa có | `2` | `bathroom_count=2` |

Đây là đúng bốn field trong `field_changes` và
`ai_cleaning_audit.changed_fields`. Title, description, URL, loại căn hộ,
loại giao dịch và tỉnh/quận giữ nguyên. Các chuỗi `8.0 tỷ`, `80.0 m2` là
định dạng do code merge tạo từ số Gemini trả về. Giá/m² cuối cùng
`100000000.0` do processor tính từ giá/diện tích; metadata và các feature
khác của processor không được gán nhầm là output Gemini.

Record cuối nằm tại **`real_estate_stress_db.training_features`** với
`ai_status=success`, `ai_model=gemini-3.6-flash`, `ai_attempts=3`,
`processing_method=ai_extraction`, `is_synthetic=true`,
`is_model_candidate=false`. Receipt có `status=completed, outcome=success`,
`origin=stress`, hoàn tất lúc `2026-09-24T08:13:36.720959+00:00`.
Tên collection `training_features` không có nghĩa fixture được dùng train:
cờ synthetic và `is_model_candidate=false` loại nó khỏi dữ liệu huấn luyện.

### Bằng chứng đối chiếu, khôi phục cấu hình và giới hạn

| Hop | Topic | Partition | Offset |
|---|---|---:|---:|
| Raw input | real_estate_stress_raw | 2 | 55686 |
| AI input | real_estate_ai_input | 2 | 7 |
| AI result thành công | real_estate_ai_results | 2 | 6 |

- Artifact [gemini-clean-audit.json](../runtime/research/gemini-clean-audit.json)
  có `status=verified`, `expected_model=gemini-3.6-flash` và chuỗi
  `before → gemini_input → gemini_output → parsed_response → after_extraction → final_record`;
  kèm Kafka offsets, receipt và storage. Artifact được giữ local, ngoài Git.
- Mongo `ai_provider_audit` ghi các bước HTTP cho đúng event đã chọn;
  `ai_cleaning_audit` lưu before/after; `ai_extractions` và
  `ai_result_receipts` lưu kết quả tương ứng. Log
  [ai-agent-success.log](../runtime/research/gemini-verification-runtime/ai-agent-success.log)
  có event ID, audit stage và SHA-256.
- `raw_record_preserved=true`: bản gốc được giữ nguyên trong `listings_raw`.
  Đối chiếu DB chính theo URL/event ID cho `listings_raw`,
  `training_features`, `invalid_records`, `ai_cleaning_audit`,
  `ai_provider_audit` đều **0 document**.
- Sau chạy đã khôi phục `AI_STRESS_ENABLED=false`, bỏ audit event filter,
  giữ production `AI_FALLBACK_ENABLED=false` và `AI_ENABLED=true`.
  `LLM_MODEL` runtime trở lại giá trị ban đầu **`gemini-2.5-flash`**.
  Do đó đây là kiểm chứng thành công bằng override `gemini-3.6-flash`,
  **chưa phải chuyển production sang model mới**; lỗi model cũ chưa được
  loại bỏ khỏi cấu hình vận hành gốc. Bằng chứng trạng thái trước/sau tại
  `runtime/research/gemini-verification-runtime/state-before-success-attempt.json`
  và `state-after-success-attempt.json`.
- Kiểm tra liên quan: **140 tests pass**, compileall bảy file Python và
  `git diff --check` pass; bốn container liên quan healthy, trạng thái cấu hình
  sau chạy khớp trước chạy. Chi tiết tại
  [validation-success.json](../runtime/research/gemini-verification-runtime/validation-success.json).

Đã xác minh request/response Gemini thật, parse, merge, processor validation
và final storage cho **đúng record có audit ở trên**. Chưa xác minh toàn bộ
dataset crawl đã qua Gemini, cleaning lịch sử, độ chính xác extraction trên
mẫu đại diện hay độ ổn định provider dài hạn. Hai response 503 còn cho thấy
một lần thành công không chứng minh dịch vụ luôn sẵn sàng.

### Luồng tạo file bằng chứng và cách mở xem

File trực tiếp tạo `runtime/research/gemini-clean-audit.json` là
[scripts/verify_gemini_pipeline.py](../scripts/verify_gemini_pipeline.py).
Hàm `run_case()` gửi record kiểm chứng qua Kafka, quan sát các message và
đọc kết quả từ MongoDB; hàm `write_report()` ghi dữ liệu tổng hợp ra JSON
tại đường dẫn `--output`. File audit là bản chụp của lượt chạy verifier,
không tự cập nhật liên tục khi agent xử lý các record mới.

```text
verify_gemini_pipeline.py: run_case() gửi raw record qua Kafka
  -> processor chuyển record đủ điều kiện fallback vào real_estate_ai_input
  -> agents/worker.py: capture() ghi before cho event được chọn
  -> agents/providers.py: emit() ghi request, HTTP status, raw response Gemini
  -> agents/extraction.py: emit() ghi parsed response
  -> agents/worker.py: emit() ghi extraction_result
       -> agents/provider_audit.py: che secret, thêm timestamp và SHA-256
       -> MongoDB real_estate_stress_db.ai_provider_audit (lượt kiểm chứng này)
  -> real_estate_ai_results -> processor validation -> final record + receipt
  -> verify_gemini_pipeline.py: run_case() đối chiếu Kafka và các collection MongoDB
  -> write_report() -> runtime/research/gemini-clean-audit.json
```

Các lệnh `emit()` trong luồng trên cùng sử dụng sink của
[agents/provider_audit.py](../agents/provider_audit.py), ghi các stage vào
document có `_id=event_id` trong `ai_provider_audit`. Chỉ event nằm trong
`AI_AUDIT_EVENT_IDS` mới được ghi; audit mặc định tắt. Collector còn đọc
`ai_cleaning_audit`, `ai_extractions`, `ai_result_receipts`, `listings_raw`,
`training_features` và `invalid_records` để đối chiếu quá trình xử lý.
Vì vậy, file xuất chứa cả bằng chứng provider và kết quả xử lý cuối của pipeline.

Để tự xem, mở
[runtime/research/gemini-clean-audit.json](../runtime/research/gemini-clean-audit.json)
trong IDE, rồi tìm các key sau:

| Key trong artifact | Nội dung đối chiếu |
|---|---|
| `before` | Raw record trước khi xử lý AI |
| `gemini_input[*].request.messages` | Toàn bộ system/user messages thực sự gửi Gemini, gồm các lần retry |
| `gemini_output[*]` | HTTP status và raw response của từng attempt |
| `parsed_response` | JSON extraction đã qua kiểm tra schema |
| `after_extraction` | Record sau khi merge các field được trích xuất |
| `field_changes` | Giá trị trước/sau của từng field thay đổi khi merge |
| `final_record` | Bản ghi cuối sau processor validation và chuẩn hóa |
| `receipt`, `final_storage_collection` | Trạng thái xử lý và collection lưu kết quả cuối |

Raw response được giữ trong artifact; ví dụ INPUT/OUTPUT trình bày ở trên
được đối chiếu với artifact này. Đường dẫn `runtime/` nằm ngoài Git nên cần
mở file tại máy đã chạy verifier. Nếu lượt chạy thất bại, verifier vẫn lưu
bằng chứng thu được với `status=blocked`; có file JSON không đồng nghĩa
Gemini đã trích xuất thành công.

Phân biệt các script có tên gần nhau:

| Script | Vai trò |
|---|---|
| `verify_gemini_pipeline.py` | Kiểm chứng cuộc gọi Gemini thật và xuất bằng chứng INPUT/OUTPUT đầy đủ |
| `audit_gemini_clean.py` | Tổng hợp thống kê và hash từ `ai_cleaning_audit`, không thu raw request/response Gemini; lưu sang file riêng để giữ nguyên artifact đầy đủ |
| `verify_agent_pipeline.py` | Kiểm thử pipeline với transport LLM mô phỏng, không phải bằng chứng gọi Gemini thật |

### Code audit và cách kiểm chứng lại

[provider_audit.py](../agents/provider_audit.py),
[providers.py](../agents/providers.py), [extraction.py](../agents/extraction.py)
và [worker.py](../agents/worker.py) bổ sung audit có cấu trúc: mặc định tắt,
chỉ bật cho event được chọn, che secret, không thu thập Authorization header.
Body lỗi đọc có giới hạn; lỗi ghi audit không thay đổi kết quả nghiệp vụ.
Export tổng hợp cũ `audit_gemini_clean.py` không thay thế bằng chứng HTTP.

[verify_gemini_pipeline.py](../scripts/verify_gemini_pipeline.py) hỗ trợ
`--prepare --model`, lưu model kỳ vọng và kiểm tra model ở request/kết quả.
Cần kiểm tra listing với credential hiện tại trước mỗi lần chọn model;
lệnh dưới dùng model đã được kiểm chứng trong lần báo cáo này.
Images phải chứa code audit hiện tại; không bật producer stress khác.
Ví dụ chạy lại từ cấu hình gốc đã tắt AI stress/audit; chọn đường dẫn case mới
nếu tên dưới đây đã tồn tại:

```powershell
$casePath = "runtime/research/gemini-new-case.json"
$overridePath = "runtime/research/gemini-new-compose.json"
python scripts/verify_gemini_pipeline.py --prepare --model gemini-3.6-flash --case $casePath
if ($LASTEXITCODE -ne 0) { throw "Prepare failed" }
$eventId = (Get-Content -Raw $casePath | ConvertFrom-Json).event_id
$testServices = @{}
foreach ($service in @("processor", "processor-2", "processor-3", "ai-agent")) {
    $testServices[$service] = @{ environment = @{ AI_STRESS_ENABLED = "true" } }
}
$testServices["ai-agent"].environment.LLM_MODEL = "gemini-3.6-flash"
$testServices["ai-agent"].environment.AI_AUDIT_EVENT_IDS = $eventId
@{ services = $testServices } | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding UTF8 $overridePath
try {
    docker compose -f docker-compose.yml -f $overridePath up -d --no-deps processor processor-2 processor-3 ai-agent
    if ($LASTEXITCODE -ne 0) { throw "Test containers failed" }
    python scripts/verify_gemini_pipeline.py --run --case $casePath --output runtime/research/gemini-new-audit.json --timeout-seconds 180
    if ($LASTEXITCODE -ne 0) { throw "Verification blocked; inspect artifact" }
} finally {
    docker compose up -d --no-deps processor processor-2 processor-3 ai-agent
}
```

Không dùng lại event đã publish; verifier từ chối để tránh hiểu nhầm cache
là một cuộc gọi mới. Chỉ kết luận thành công khi artifact ghi `verified`
với đủ bằng chứng HTTP 200, parsed extraction và final receipt thành công.

## 3. Pipeline mô hình giá và phạm vi kết luận

Trainer đọc các bản ghi đủ điều kiện, không có dấu synthetic, từ MongoDB.
Mô hình hiện tại dùng preprocessing và VotingRegressor gồm Ridge,
HistGradientBoostingRegressor và SGDRegressor trên target giá biến đổi log.
Model artifact được dùng bởi API phục vụ dự đoán giá. Xem
[price_model.py](../modeling/price_model.py) và [ML_PIPELINE_AUDIT.md](ML_PIPELINE_AUDIT.md).

Bản báo cáo này không công bố benchmark mới cho mô hình giá. Phần kiểm chứng
Gemini ở mục 2 chỉ xác nhận một fixture tổng hợp đi qua provider thật và được
lưu đúng; không suy ra chất lượng trên toàn bộ dữ liệu crawl.

## 4. Kiểm tra bản main ngày 03/10/2026

| Kiểm tra | Kết quả |
|---|---|
| `python -m pytest -q -ra` | 531 tests passed; có cảnh báo deprecation từ thư viện |
| Compileall các package ứng dụng và tests | Thành công |
| `docker compose --env-file .env.example config --quiet` | Thành công; 19 service mặc định, gồm `ai-agent` và `stress-agent` |
| Frontend `npm ci`, `npm run lint`, `npm run build` | Thành công |
| Kiểm tra import | Không còn phụ thuộc package nghiên cứu đã tách khỏi bản main |

Đây là kiểm tra offline và cấu hình của bản chốt. Không có lượt chạy Kafka/Mongo
hoặc provider thật mới trong lần tách nhánh này. Bằng chứng live ngày 24/09/2026
ở mục 2 được giữ nguyên theo lần đo đó; không coi cấu hình model cũ là xác nhận
model vẫn khả dụng hiện nay.

Lệnh kiểm tra lại từ thư mục dự án:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q -ra
docker compose --env-file .env.example config --quiet
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
```

## 5. Tài liệu và bằng chứng

- [Kiến trúc hiện có](ARCHITECTURE.md) và [sơ đồ luồng](../flow_diagram.md).
- [Hướng dẫn chạy và kiểm chứng hai agent](RUNBOOK.md#optional-ai-and-stress-verification).
- [Verifier Kafka/Mongo dùng LLM mô phỏng](../scripts/verify_agent_pipeline.py).
- [Verifier Gemini với request/response thực](../scripts/verify_gemini_pipeline.py).
- [Bằng chứng Gemini đã lưu](../runtime/research/gemini-clean-audit.json).

Các file dưới `runtime/` và `artifacts/` được giữ cục bộ, không đưa vào Git.
Tên thư mục lịch sử `runtime/research/` trong bằng chứng Gemini chỉ là nơi lưu
artifact extraction; không cần package `research` để chạy hai verifier trên.
Checkout mới cần cấu hình môi trường và tạo bằng chứng riêng theo hướng dẫn,
không mặc định có sẵn dữ liệu/model của máy đã báo cáo.
