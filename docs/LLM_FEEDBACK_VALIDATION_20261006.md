# Kiểm chứng thực tế ngày 06/10/2026

Đã chạy thử trên Docker thật và gọi Gemini thật. **Chưa chứng minh được vòng
LLM tự tăng/giảm lưu lượng khi có dữ liệu**: phần lớn lượt thử bị chặn vì thiếu
RAM host; lần gọi model trong vòng điều tiết gặp lỗi dịch vụ tạm thời.

## Môi trường và phạm vi

- Nhánh `dev`, nền code `fd70bea`; `main` không thay đổi.
- Docker Engine 29.6.2, Linux containers, 16 CPU, khoảng 7,61 GiB RAM VM.
- Build và triển khai lại ba Processor, trainer, stress-agent với code mới.
- Giới hạn ban đầu: mỗi Processor 1 CPU/768 MiB; trainer 1 CPU/1024 MiB.
- Lượt `llm-live-20261006-01`: tối đa 5 record/s, 300 records, 240 giây.
  Controller được giới hạn khoảng 160 giây rồi dừng và thu hồi lease.
- Tạm dừng sáu dịch vụ phụ để giảm tải; cuối lượt khởi động lại đúng các container đó.
- Trainer dùng ngưỡng số mẫu rất cao trong lượt thử để kiểm tra quyền bắt đầu
  training mà không ghi đè model đang phục vụ. Không chạy fit trong lượt thử.

## Kết quả

| Hạng mục | Bằng chứng | Kết luận |
|---|---|---|
| API model thật | `gemini-3.6-flash` trả JSON hợp lệ trong khoảng 2,63 giây | Kết nối model hoạt động; đây là yêu cầu kiểm tra kết nối |
| Đọc Docker/Kafka/Processor | 23 mẫu trong 161,92 giây; Kafka lag bằng 0 | Thu được telemetry thật; một mẫu phát hiện lỗi parser bên dưới |
| Đổi CPU và RAM | Công cụ actuator đổi Processor từ 1 CPU/768 MiB sang 0,9 CPU/704 MiB | Docker inspect và YAML cùng xác nhận giá trị mới |
| Khôi phục tài nguyên | Áp dụng lại 1 CPU/768 MiB, sau đó trả nguyên byte Compose ban đầu | Khôi phục thành công |
| Rollback tự động | Chủ động chèn lỗi sau khi Docker và Compose đã đổi CPU thành 0,9 | Công cụ tự khôi phục allocation và nguyên byte Compose; không còn service cần khôi phục |
| Bảo vệ khi thiếu RAM | 17 quyết định `emergency_host_memory` | Lease đặt rate=0 và không cho bắt đầu training |
| Provider lỗi | Một quyết định `decision_failed:provider_unavailable` | Giữ đầu vào/training tạm dừng |
| Producer/trainer đọc lease | Producer phát 0 record; trainer ghi `Training deferred until...` | Chặn đầu vào và hoãn training hoạt động thật |
| Tự điều chỉnh lưu lượng theo LLM | Không có quyết định model nào được áp dụng trong lượt này | **Chưa kiểm chứng thành công** |
| Chuyển model dự phòng | Chưa cấu hình credentials/route dự phòng | **Chưa kiểm chứng thực tế** |

Thử đổi tài nguyên và thử rollback gọi trực tiếp công cụ actuator với giá trị
kiểm thử; **không phải bằng chứng model tự chọn allocation đó**. Lỗi rollback
được chèn có chủ đích, không phải sự cố Docker tự phát.

RAM host còn 4,44–15,28% trong lượt thử, phần lớn dưới floor 10%. Trong khoảng
RAM đủ, lần gọi Gemini trả lỗi dịch vụ; sau đó RAM lại xuống dưới floor.
Không hạ floor để ép phát tải. Báo cáo stress ghi `generated=0`, `delivered=0`,
`failed=0`, `undelivered=0`, kết thúc `interrupted` khi dừng lượt thử có giới hạn.
Các số 0 này chứng minh tạm dừng, không chứng minh throughput hay capacity.

## Lỗi phát hiện và bản sửa

Docker CLI thực tế trả một bộ đếm dạng ` 1e+03kB`. Parser dung lượng cũ không
nhận ký hiệu khoa học và đánh dấu toàn mẫu `telemetry_unavailable`.

Đã sửa `research/telemetry.py` để nhận số thập phân/ký hiệu khoa học không âm,
giữ đúng đơn vị SI/IEC và từ chối giá trị sai, vô hạn hoặc tràn số. Kiểm thử hồi
quy bao gồm chính chuỗi gây lỗi cùng NetIO, BlockIO và MemUsage.
**72 tests telemetry/controller đạt** sau bản sửa. Lấy lại ba mẫu telemetry thật:
cả ba không có lỗi thu thập; hai mẫu sau warmup đo ingress/throughput bằng 0 và lag 0.

## Bằng chứng cục bộ và trạng thái sau thử

Bằng chứng nằm tại `runtime/control-validation/20261006-01/` (được Git bỏ qua):

- `provider.json`, `live.jsonl`, `summary.json`.
- `resource.apply.json`, `resource.observed.json`, `resource.restore.json`.
- `rollback.json`, `compose.before.yml`, `compose.applied.yml`.
- `trainer.denied.log`, `telemetry.after-fix.json`, `services.restore.json`.

Manifest, ground truth và báo cáo producer tại
`runtime/stress/llm-live-20261006-01/`. Ground truth rỗng vì lease chặn toàn bộ lượt.

Compose đã trả lại nguyên nội dung ban đầu; `.env` không đổi. Model artifacts
không đổi trong lượt thử. Stress-agent được đưa về cấu hình mặc định và giữ dừng;
trainer được đưa về cấu hình thông thường. Sáu dịch vụ phụ đã được khởi động lại.
Các container Processor/trainer giữ image mới và quota ban đầu khai báo trên `dev`.

Để kiểm chứng phần còn thiếu, cần một khoảng RAM host ổn định trên floor, model
trả quyết định thành công và workload có giới hạn. Dùng run ID mới, lưu cả quyết
định lẫn ingress/throughput/lag/latency trước và sau hành động. Cần route dự phòng
được cấu hình trước khi kiểm chứng chuyển model.
