# Kiểm chứng vòng phản hồi thực tế ngày 07/10/2026

**Đã chạy được vòng telemetry → LLM → policy → Kafka**, và đã chuyển sang model
dự phòng khi model chính trả HTTP 503. Đây là kiểm chứng chức năng ở tải nhỏ;
chưa chứng minh tối ưu tốc độ khi quá tải hoặc giới hạn xử lý tối đa của máy.

## Điều kiện thử

- Code nhánh `dev` tại `f02563d`; không đổi thuật toán trong lượt thử.
- RAM host ban đầu trống 4,03 GiB, tương đương 25,65%. Giữ floor 10% cho cả host
  và VM; không hạ floor để ép chạy. RAM host giảm trở lại trong lượt thứ hai.
- Mỗi lượt controller khoảng 240 giây, producer tối đa 5 record/s, 1600 records,
  thời hạn 330 giây, chỉ phát vào `real_estate_stress_raw`.
- Dữ liệu synthetic có cấu trúc, không trùng lặp; không gọi extraction API.
- Trainer có quyền đọc lease nhưng dùng ngưỡng số mẫu cao để không bắt đầu fit
  trong cửa sổ điều tiết. `training_allowed=true` chỉ chứng minh cấp
  quyền, không chứng minh đã huấn luyện thành công.

## Lượt 02: model chính

Run ID: `llm-live-20261007-02`.

Gemini 3.6 Flash trả ba HTTP 503 và hai HTTP 200. Hai quyết định hợp lệ đều chọn
`light`, rate 5/s và cho phép bắt đầu training. Khi model lỗi, guard tạm dừng
rate về 0; sau khi model phục hồi, model chọn tiếp tục 5/s.

- Producer sinh và Kafka xác nhận **245 records**, lỗi giao nhận 0.
- Mongo stress có đúng 245 raw records và 245 feature records của run này;
  Mongo dữ liệu thật không có record mang run ID này.
- Các cửa sổ hoàn toàn sau khi nhận lease 5/s đo ingress **4,805–4,943/s**.
- Trong giai đoạn ổn định, throughput theo kịp ingress, lag 0, p95 khoảng
  **48,75–49,49 ms**. Giai đoạn đầu có lag 24 và một cửa sổ p95 8,6 giây;
  không gộp giai đoạn khởi động vào kết luận độ trễ ổn định.
- Host còn ít nhất 10,05% RAM trong các mẫu; VM còn ít nhất 36,30%.

Rate 5 → 0 do lỗi provider là hành động bảo vệ của code, không phải model tự
giảm tốc theo tải. Hai quyết định model đều chọn 5, nên lượt này chưa chứng minh
model tự chọn nhiều mức tốc độ dương.

## Lượt 03: chuyển model dự phòng

Run ID: `llm-live-20261007-03`.

Trong riêng phiên thử, cấu hình thêm `gemini-3.5-flash-lite` làm fallback, dùng
cùng key và endpoint Gemini hiện có. Không ghi key vào tài liệu/receipt và
không sửa `.env`. Đây là chuyển model trong cùng provider, không phải chuyển
sang tài khoản/provider có quota độc lập.

Chuỗi bằng chứng HTTP và quyết định:

1. Gemini 3.6 Flash trả 200; model chọn `light`, rate 5/s.
2. Lần gọi tiếp theo trả 503; router gọi Gemini 3.5 Flash-Lite và nhận 200.
   Model dự phòng chọn `normal`, giữ 5/s, viện dẫn ingress khoảng 4,8/s,
   p95 khoảng 49 ms và lag 0 từ telemetry thật.
3. Lần sau, model chính tiếp tục trả 503 và model dự phòng lại trả 200;
   quyết định tiếp tục giữ 5/s.

Trong lần chuyển đầu, lease còn hiệu lực và producer tiếp tục chạy; ingress
đo được khoảng 4,81–4,86/s, lag 0. Không có pause do lỗi provider trong lần
chuyển này. Hai lần chuyển đều gặp lỗi quá tải model thật, không chèn lỗi giả.

Sau đó RAM host xuống dưới 10%; guard cấp rate 0 và cấm bắt đầu training.
Ingress giảm về 0 sau cửa sổ chuyển tiếp, hàng đợi vẫn được xử lý hết.
Đây là bằng chứng cơ chế bảo vệ bộ nhớ, không phải quyết định giảm tốc của LLM.

Lượt 03 có 31 mẫu telemetry, producer sinh và giao thành công **401 records**,
lỗi giao nhận 0. Mongo stress có đúng 401 raw records và 401 feature records;
Mongo dữ liệu thật không có record mang run ID này. Tổng hai lượt là **646 records**.

## Kết luận có thể báo cáo

| Nội dung | Kết quả |
|---|---|
| Model thật nhận telemetry và quyết định ingress | Đạt |
| Producer thật tuân theo lease, Kafka/Mongo xử lý dữ liệu | Đạt |
| Quyết định tiếp theo dựa trên các số đo khi đã có lưu lượng | Đạt |
| Phân loại `light` → `normal` từ model | Có quan sát trong lượt 03 |
| Chuyển model khi HTTP 503 và giữ luồng khi lease còn hiệu lực | Hai lần thành công |
| Model chọn nhiều mức tốc độ dương theo tải | Chưa quan sát; các quyết định đều chọn 5/s |
| Model tự đổi allocation CPU/RAM trong hai lượt này | Chưa quan sát; `resources={}` |
| Áp dụng allocation và rollback trên Docker thật | Đã kiểm tra trực tiếp công cụ ngày 06/10 |
| Training hoàn tất dưới quyền điều tiết | Chưa thử; chỉ kiểm tra quyền bắt đầu |
| Chuyển model khi cạn quota/token | Chưa thử lỗi quota thật; lượt này gặp HTTP 503 |
| Capacity tối đa hoặc hiệu quả ở tải cao | Chưa đo |

Kết quả chứng minh vòng phản hồi và failover hoạt động, không chứng minh các
quyết định luôn tối ưu. Model giữ tốc độ khi số đo ổn định là một kết quả hợp lệ.

## Bằng chứng và vận hành lại

[Tổng hợp số liệu và hash log](evidence/llm-feedback-20261007.json) được lưu cùng
báo cáo để đối chiếu các quyết định, HTTP status, cửa sổ ingress và kết quả cleanup.

Các log cục bộ nằm tại `runtime/control-validation/20261007-02/` và
`runtime/control-validation/20261007-03/`: `live.jsonl`, `provider.jsonl`,
`settings.json`, `summary.json`, `mongo-isolation.json`, `cleanup.json`.
Log provider đã lọc credentials; thư mục runtime được Git bỏ qua.
Manifest và báo cáo producer nằm tại `runtime/stress/llm-live-<run-id>/`.

Trong hai lượt, model không yêu cầu đổi CPU/RAM. Cleanup trả nguyên byte Compose,
giữ `.env`, trả trainer về cấu hình thông thường, giữ stress-agent dừng và khởi
động lại các dịch vụ phụ đã tạm dừng. Route fallback của lượt 03 chỉ có trong
môi trường tiến trình thử, chưa được lưu làm cấu hình chạy thường xuyên.

Đối chiếu hash model ở lượt 03 trả `false`: trainer ở chế độ thông thường đã
tạo phiên bản `20261006_173448` trong lúc chuẩn bị, trước khi trainer điều tiết
bắt đầu; sau khôi phục, trainer thường tạo tiếp phiên bản `20261006_174018`.
Log trainer trong cửa sổ điều tiết chỉ ghi skip/deferred, không fit. Vì vậy,
không tuyên bố model artifacts giữ nguyên xuyên suốt toàn bộ chuẩn bị/khôi phục.

Muốn dùng lại route đã thử với key Gemini có sẵn, đặt nhóm sau trong `.env`
(khai báo `GEMINI_API_KEY` trước nhóm này):

```dotenv
CONTROL_LLM_FALLBACK_PROVIDER=gemini
CONTROL_LLM_FALLBACK_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
CONTROL_LLM_FALLBACK_MODEL=gemini-3.5-flash-lite
CONTROL_LLM_FALLBACK_API_KEY=${GEMINI_API_KEY}
```

Gemini 3.5 Flash-Lite có free tier theo [bảng giá chính thức](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite);
chi phí thực tế phụ thuộc tier của tài khoản. Không bật hay thay đổi billing
trong lượt thử. Thử Gemini 2.5 Flash-Lite trên tài khoản này nhận 404 do model
không còn dành cho người dùng mới, nên không chọn nó làm fallback.
