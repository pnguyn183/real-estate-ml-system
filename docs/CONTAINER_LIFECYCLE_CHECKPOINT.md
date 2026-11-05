# Container lifecycle checkpoint

Cập nhật: **05/11/2026**. Nhánh triển khai: `dev`; baseline `main` giữ nguyên.

## Đã hoàn thành

- [x] Model chọn tối đa một `stop/start` mỗi quyết định, dựa trên telemetry từng dịch vụ, tài nguyên host/VM và lịch sử hành động.
- [x] Công cụ thực thi bảo vệ Kafka, ZooKeeper, MongoDB, processor, producer, extraction và Prometheus. Mặc định chỉ cho phép trainer, Grafana và mongo-express.
- [x] Kiểm tra dependency, ID/cấu hình, lần khởi động container, cooldown và dung lượng dự phòng trước thao tác.
- [x] Trainer xác nhận đã rảnh trước khi dừng; fit đang chạy được hoàn tất. Yêu cầu hết hạn hoặc chưa xác nhận không cho phép dừng.
- [x] Ghi ý định trước lệnh Docker, ghi quyền khôi phục sau khi xác nhận đã dừng. Không kế thừa quyền với container thay thế hoặc container người vận hành dừng sau đó.
- [x] Guard RAM ngừng ingress nhưng vẫn cho model chọn dừng dịch vụ phụ khi telemetry hợp lệ. Chờ số đo mới sau thao tác trước khi mở lại tải.
- [x] **337 kiểm thử đạt**; đã sửa và kiểm tra các race condition về drain, telemetry, thay thế/restart container và quyền khôi phục.
- [x] Cập nhật cấu hình mẫu, hướng dẫn và Project Report.

Mã chính: [controller](../agents/control_agent.py), [công cụ lifecycle](../agents/container_lifecycle.py), [trainer drain](../agents/container_drain.py).
Bằng chứng: [kết quả kiểm chứng 04/11](evidence/container-lifecycle-20261104.json).

## Kiểm chứng runtime đã thực hiện

- Đọc Docker thật thành công: có footprint/trạng thái từng dịch vụ phụ. Trainer chưa dùng policy điều khiển nên được bảo vệ.
- CLI ở chế độ đề xuất dừng với `telemetry_unavailable`: published ports của Docker Desktop trỏ tới IP worker cũ. Worker bên trong Docker vẫn trả metrics và khỏe.
- Phép kiểm tra riêng đọc **metrics thật qua Docker exec**, giữ Kafka/Docker/host telemetry như cũ: telemetry hợp lệ, nhưng model trả `provider_timeout`.
- Chưa thực thi stop/start trên container thật, chưa ghi policy hay bật controller liên tục. Không coi phép kiểm tra nội bộ là đã sửa published ports.

## Task tiếp theo

1. Khôi phục đường HTTP metrics từ host tới ba worker và xác minh telemetry qua CLI chuẩn. Không cần viết lại telemetry hoặc lifecycle.
2. Rebuild/deploy trainer có drain handshake; cấu hình producer/trainer dùng cùng policy. Bật `CONTROL_ENABLED` và `CONTROL_CONTAINER_ENABLED` theo [hướng dẫn](LLM_FEEDBACK_CONTROL.md).
3. Xác minh model trả quyết định trong deadline. Không tăng timeout đơn lẻ vượt giới hạn tuổi telemetry/lease.
4. Chạy phép thử riêng: model chọn dừng dịch vụ phụ → Docker xác nhận → đo tài nguyên và sức khỏe pipeline → model khôi phục khi đủ điều kiện. Ghi cả tác dụng và chi phí gián đoạn dịch vụ.
5. Chỉ đánh dấu kiểm chứng quá tải hoàn tất khi có bằng chứng toàn bộ vòng phản hồi trên. Kết quả unit test hiện tại không thay thế bằng chứng này.

Không chạy lại 337 tests nếu chỉ cập nhật tài liệu. Nếu sửa mã tiếp, chạy các suite liên quan nêu trong hướng dẫn. Journal có ý định chưa xác nhận cần đối chiếu thủ công; controller không tự nhận quyền bật lại từ một kết quả không rõ ràng.
