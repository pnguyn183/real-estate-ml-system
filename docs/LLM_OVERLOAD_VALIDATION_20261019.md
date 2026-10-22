# Kiểm chứng ngày 19/10/2026

**Kết quả: `inconclusive`; chưa có bằng chứng LLM điều tiết thành công trong lượt này.**
Lượt `llm-proof-20261019-01` dừng ngay ở pha tải nhẹ của baseline vì
`emergency_host_memory`. Chưa chạy nhánh LLM: **0 quyết định LLM, 0 cặp đối chứng
hợp lệ**, chưa tới pha quá tải 500 bản ghi/giây.

Mã nguồn lúc chạy là `778fe1e479f995265b8bb3cea8d8c8f0bef3a914`, đã bao gồm sửa
observer Kafka tại `e5c5664`: timeout đọc commit tăng từ 2 lên 5 giây, thời gian
chờ đóng observer có giới hạn 11 giây và bổ sung chẩn đoán lỗi Kafka đã lọc.
Lỗi quan sát vẫn làm phép thử không hợp lệ. Phiên làm việc trước ghi nhận 47 kiểm
thử liên quan đạt; đây là kết quả lịch sử, không phải lượt chạy lại khi tổng hợp
tài liệu ngày 23/10.

| Chỉ số của lượt 19/10 | Kết quả |
| --- | --- |
| Dữ liệu đã phát / cho qua gate / Kafka ACK / quan sát commit | 68 / 68 / 68 / 68 |
| Từ chối / đang chờ / lỗi enqueue hoặc delivery | 0 / 0 / 0 |
| Dự kiến phát nhưng chưa đến thời điểm khi dừng | 50.732 |
| Lỗi observer / lỗi đọc policy | 0 / 0 |
| Khoảng cách poll commit lớn nhất | 0,281 giây |
| Trạng thái run / session | `guard_stopped` / `incomplete` |
| Khôi phục dịch vụ | 7/7, `restore_errors=[]` |

Chỉ 68 bản ghi đã phát được tính là dữ liệu đã xử lý; 50.732 bản ghi tương lai
không được tính là thành công hoặc thất lạc. Commit được quan sát qua offset của
consumer group, không phải kiểm tra nội dung MongoDB từng bản ghi. p95 từ thời
điểm dự kiến phát đến lần đầu quan sát commit là 7,795 giây, là cận trên do
polling và chỉ áp dụng cho các bản ghi đã quan sát commit.

RAM host khả dụng trước lượt thử là 16,13% (2,54 GiB). Bốn mẫu telemetry cách
nhau khoảng 5 giây ghi nhận thấp nhất 11,29%; watchdog RAM chạy nhanh hơn đã
dừng tải theo ngưỡng 10%. Artifact ghi lý do guard nhưng không lưu giá trị RAM
của chính lần kiểm tra nhanh kích hoạt guard, nên không suy ra mức thấp nhất
thực tế từ bốn mẫu này.

Các mẫu ghi nhận RAM tăng đồng thời ở cả processor và MongoDB:

| Thời điểm UTC ngày 19/10 | Processor 1 (MiB) | Processor 2 (MiB) | Processor 3 (MiB) | MongoDB (MiB) |
| --- | ---: | ---: | ---: | ---: |
| 10:33:34,602 | 155,3 | 162,6 | 150,4 | 942,0 |
| 10:33:39,449 | 659,4 | 665,7 | 655,1 | 1.793,0 |
| 10:33:44,908 | 183,8 | 190,0 | 182,6 | 1.911,8 |
| 10:33:49,411 | 183,7 | 189,8 | 182,4 | 1.911,8 |

Trong cùng khoảng tăng RAM, bộ đếm dữ liệu nhận qua mạng của mỗi processor tăng
từ khoảng 2 MB lên 107–108 MB. Vì vậy, giả thuyết ban đầu chỉ quy về cache MongoDB
chưa đủ giải thích số liệu. Đây là tương quan đo được; riêng telemetry không xác
định được đoạn mã gây tăng RAM hoặc chứng minh quan hệ nhân quả. Cần đối chiếu
đường đọc dữ liệu trong processor trước khi kết luận nguyên nhân.

Training được dừng trong phép thử; giới hạn mỗi processor giữ ở 1 CPU, 768 MiB
RAM. Manifest ghi nhận bảy dịch vụ được khôi phục: frontend, mongo-express, API,
scraper, predictor, Grafana và trainer. Không có lỗi cleanup được ghi nhận.

Phần chưa hoàn thành vẫn là đối chứng ít nhất ba cặp AB/BA với quyết định model
thật và sự phục hồi khi nhu cầu đầu vào còn cao. Theo phạm vi người dùng đặt
ngày 23/10, lượt thử tải cần thêm RAM được để lại; tài liệu này chỉ hoàn tất
tổng hợp bằng chứng đã có. Thu thập telemetry thụ động là công việc riêng và
không tự thay thế đối chứng LLM.

[Số liệu và nguồn artifact](evidence/llm-overload-20261019.json),
[các lượt ngày 14/10](LLM_OVERLOAD_VALIDATION_20261014.md),
[quy trình đối chứng](LLM_OVERLOAD_EXPERIMENT.md).
Raw artifacts: `runtime/research/llm-proof-20261019-01/`.
