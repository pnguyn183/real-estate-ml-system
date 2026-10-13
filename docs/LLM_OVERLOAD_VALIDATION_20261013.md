# Trạng thái kiểm chứng quá tải ngày 13/10/2026

**Chưa chứng minh được LLM ổn định hệ thống khi quá tải.** Bộ công cụ đối chứng
đã hoàn thành, nhưng các lượt thực tế dưới đây dừng ở bước hiệu chuẩn vì RAM
trống trên máy host xuống ngưỡng bảo vệ 10%, trước khi gọi model.

| Phiên | Offered | Kafka ACK | Consumer commit | Chưa xử lý | Quyết định LLM | Kết quả |
|---|---:|---:|---:|---:|---:|---|
| `llm-proof-20261012-01` | 20 | 20 | 20 | 0 | 0 | `emergency_host_memory` |
| `llm-proof-20261013-01` | 17 | 17 | 17 | 0 | 0 | `emergency_host_memory` |

Cả hai lượt không có enqueue/delivery failure; đây là số đếm Kafka, không phải
xác nhận riêng từng bản ghi MongoDB. Các yêu cầu còn lại của lịch hiệu chuẩn
được ghi là hủy do phiên dừng, không được coi là tải đã hoàn thành. Có **0 cặp
đối chứng hợp lệ**, kết luận `inconclusive`. Việc guard dừng gửi không phải hành
động của LLM và không được nhận là bằng chứng LLM điều tiết.

Mỗi phiên đã khôi phục đủ dịch vụ đã tạm dừng với `restore_errors=[]`. Không đổi
Compose, `.env` hoặc giới hạn Docker trong phép thử. Nhánh `main` giữ commit
`a2c5fbb8459015c1924ed78449de41240e995e13`.

[Tổng hợp có thể kiểm tra bằng máy](evidence/llm-overload-20261013.json) lưu số
đếm và commit của từng phiên. Raw telemetry, event ledger, action receipts và
manifest khôi phục nằm trong hai thư mục tương ứng dưới `runtime/research/`.
Phiên ngày 12 dùng mã đang phát triển; phiên ngày 13 dùng commit `c2ecbcc`.

Kiểm thử mã: 160 tests tập trung cho runner, accounting, report, isolation và
FeedbackAgent đạt sau các sửa cuối. Lượt kiểm tra toàn bộ trước các sửa nhỏ cuối
có 1.209 tests đạt. Fixture giả lập dùng kiểm tra logic không được tính vào bằng
chứng chạy Docker/LLM thực tế.

Xem [hướng dẫn chạy và điều kiện kết luận](LLM_OVERLOAD_EXPERIMENT.md). Cần có
RAM trống ổn định khi **Docker đang chạy**, rồi chạy lại hiệu chuẩn và đủ ba cặp
baseline/LLM. Không hạ ngưỡng RAM hoặc loại bỏ lượt không đạt để đổi kết luận.
