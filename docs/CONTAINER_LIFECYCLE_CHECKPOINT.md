# Container lifecycle checkpoint

Cập nhật: **11/11/2026**. Nhánh `dev`; baseline `main` giữ nguyên.

## Đã hoàn thành

- [x] Triển khai tại `9ae88c9`: model chọn tối đa một `stop/start`, dựa trên telemetry và lịch sử hành động; bảo vệ dịch vụ lõi, kiểm tra dependency/identity/cooldown/RAM, journal quyền khôi phục và trainer drain.
- [x] **337 tests đạt** ở lần kiểm chứng mã trước. Không chạy lại khi chỉ cập nhật tài liệu.
- [x] Khôi phục HTTP metrics từ host; lượt 05/11 dùng collector chuẩn, trainer có drain và producer/trainer cùng policy. Đã đối chiếu deployment.
- [x] Lượt `lifecycle-live-20261105-02` kết thúc: **Gemini chọn dừng Grafana và mongo-express**, Docker xác nhận cả hai. Có 23 mẫu / 205,24 giây.
- [x] Khôi phục baseline hoàn tất ngày 11/11. Lỗi còn lại do `compose create --no-deps` dùng cờ không hỗ trợ; sửa bằng `compose up --no-start --no-deps --no-build --pull never stress-agent`. Stress trở lại `Created`, image gốc, `CONTROL_ENABLED=false`, `STRESS_ENABLED=false`; policy khớp bản trước thử. Lệnh sửa không đổi ID hoặc khởi động lại container khác.
- [x] Lưu bằng chứng, cập nhật README, hướng dẫn và Project Report. Giữ nguyên artifact lỗi lịch sử, receipt khôi phục lưu riêng.

Bằng chứng: [live 05/11 và khôi phục 11/11](evidence/container-lifecycle-live-20261105.json); [kiểm thử/chẩn đoán 04/11](evidence/container-lifecycle-20261104.json).

## Giới hạn còn lại

- **0 record được phát/xử lý** trong lượt lifecycle; chưa kiểm chứng giữ throughput khi có tải. RAM host thấp nên ingress giữ 0.
- Chưa có LLM chọn `start`; script khôi phục Grafana/mongo-express không được tính là model tự phục hồi.
- Footprint trước dừng là 406,30 và 46,06 MiB, không phải lượng RAM host thu hồi. Sau stop Grafana, VM available tăng 62,21 → 64,26% nhưng host giảm 6,74 → 5,53%; chưa chứng minh lợi ích nhân quả.
- Có 3 `provider_unavailable`, 2 `provider_cooldown`; một mẫu Kafka `NOT_COORDINATOR` rồi phục hồi. Không sửa timeout/guard chỉ để ép lượt thử thành công.
- Preflight 11/11: host còn **0,94 GiB / 5,96%**, ba worker HTTP health 200. Chưa đủ điều kiện xác minh mở lại tải/bật dịch vụ. Không lặp lại phép thử stop đã có bằng chứng.
- Chưa triển khai controller chạy thường trực. Trainer drain live, đối chứng overload và lượt telemetry liên tục hai giờ vẫn chưa hoàn tất.

## Task tiếp theo

1. Kiểm tra lại RAM host/VM và telemetry mới; khi guard cho phép, chuẩn bị lượt riêng với run ID mới, producer/trainer cùng policy và đúng image có drain. Không chạy lại `trial-02` (ID/artifact cố định).
2. Xác minh LLM tự chọn bật lại container do chính controller dừng, chờ health đạt và xử lý được dữ liệu. Lưu decision/receipt, footprint, RAM host/VM, lag/latency; phân biệt thao tác model với cleanup. Không hạ ngưỡng RAM, giả telemetry hay ép model chọn hành động.
3. Kiểm chứng trainer drain dưới fit thật; sau đó tiếp tục đối chứng overload và thu hai giờ theo kế hoạch riêng. Lifecycle không trộn vào bài đối chứng cố định allocation/training.

Journal có ý định chưa xác nhận cần đối chiếu thủ công. Không nhận quyền bật lại từ kết quả chưa rõ hoặc journal cũ sau khi người vận hành đã khôi phục container. Lượt thử mới phải có cleanup đã sửa như trên; không build/pull lại nếu image phù hợp đã có.
