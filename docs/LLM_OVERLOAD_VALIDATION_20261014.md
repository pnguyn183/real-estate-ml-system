# Kiểm chứng ngày 14/10/2026

**Kết luận của cả ba lượt ngày 14/10: `inconclusive`, chưa chứng minh LLM ổn định hệ thống khi quá tải.**

Đã quan sát được quá tải thực tế ở mức 600 bản ghi/giây. Tuy nhiên, chưa lượt nào
chạy đến nhánh LLM: tổng cộng **0 quyết định LLM, 0 cặp đối chứng hợp lệ**. Các lần
dừng dưới đây do cơ chế bảo vệ hoặc lỗi phép đo, không phải quyết định điều tiết của AI.

Đã sửa và push lỗi đọc/ghi policy đồng thời trên Windows tại commit `e972055`:
chỉ thử lại `PermissionError` trong khoảng thời gian giới hạn; policy sai, mất
hoặc hết hạn vẫn đóng gate. 202 kiểm thử liên quan đạt. Kiểm tra file tạm trên
Windows với 180 lần ghi và 1.801 lần đọc trong 3,012 giây không có lỗi ghi hoặc
đọc lease không hợp lệ. Đây là kiểm tra I/O, không phải bằng chứng LLM điều tiết.

| Lượt Docker | Phần đã chạy | Kết quả và nguyên nhân dừng |
| --- | --- | --- |
| `llm-proof-20261014-01` | Hiệu chuẩn, 43 bản ghi được phát | `guard_stopped`: `emergency_host_memory`, RAM host xuống dưới ngưỡng bảo vệ 10%. Cả 43 bản ghi được ACK và quan sát commit. |
| `llm-proof-20261014-02` | Hiệu chuẩn hoàn tất; baseline ở 600 bản ghi/giây | `guard_stopped`: `hard_lag_budget`, lag đạt 10.411, vượt ngân sách 10.000. Baseline phát 44.996 bản ghi: 44.927 được ACK và quan sát commit, 69 bị từ chối khi dừng. |
| `llm-proof-20261014-03` | Baseline ở 500 bản ghi/giây, khoảng 60 giây tổng cộng | `invalid`: `completion_observation_errors`. Observer gặp một `KafkaException`; cả 10.357 bản ghi đã phát cuối cùng đều được ACK và quan sát commit. |

Trong lượt `02`, hiệu chuẩn đi qua 10, 30, 100, 300 rồi 600 bản ghi/giây. Mức 300
ổn định trong khoảng đã quan sát; mức 600 xuất hiện ba mẫu liên tiếp vượt mục tiêu
latency 2 giây. Đây là giới hạn quan sát được của tải thử với phân bổ Docker hiện
tại, chưa phải công suất tuyệt đối của máy. Hiệu chuẩn phát 24.641 bản ghi,
24.621 được ACK và quan sát commit, 20 bị từ chối khi kết thúc hiệu chuẩn.

Baseline của lượt `02` chạy khoảng 74 giây trong pha tải 600 trước khi chạm ngân
sách lag. RAM khả dụng thấp nhất trong toàn bộ các mẫu, kể cả lúc xử lý nốt hàng
đợi, là **14,61% trên host và 17,82% trong Docker VM**; lượt này không dừng vì RAM.
Đỉnh p95 theo khoảng đo từ histogram của pipeline là 29 giây. p95 đo từng bản ghi
từ thời điểm dự kiến phát đến lần đầu quan sát commit là 19,204 giây; đây là cận
trên do polling, chỉ tính bản ghi có commit được quan sát. Hai chỉ số có phạm vi
và cách ước lượng khác nhau; không xem 29 giây là độ trễ chính xác của từng bản ghi.

Lượt `03` chọn mức 500 trước khi có bất kỳ kết quả LLM nào, dựa trên việc mức 600
chạm ngân sách lag. Ngưỡng bảo vệ RAM 10%, ngân sách lag 10.000 và giới hạn Docker
được giữ nguyên. Trong khoảng tải 500 thực tế, ba mẫu có ingress xấp xỉ 500 ghi
nhận lag 170 → 199 → 396; p95 histogram của mẫu cuối là khoảng 2,087 giây. Lượt này
dừng sau khoảng 20 giây của pha tải vì observer gặp lỗi đọc commit. Khoảng cách
poll lớn nhất là 2,516 giây; artifact chỉ ghi loại `KafkaException`, chưa ghi mã
lỗi, nên chưa xác định chắc nguyên nhân. Việc đọc commit thành công về sau không
làm phép thử đã lỗi trở thành hợp lệ.

Cả ba lượt đều kết thúc với `pending=0`, không có lỗi enqueue/delivery hoặc lỗi
đọc policy được ghi nhận. Các lượt `02` và `03` đã đi qua tải cao mà không lặp lại
lỗi tranh chấp file policy. Những kết quả này kiểm tra đường phát, xử lý và quan
sát dữ liệu; chưa chứng minh hiệu quả điều tiết của LLM. Số bản ghi chưa đến thời
điểm phát khi dừng được ghi riêng là `cancelled_future_offers`, không tính là dữ
liệu đã được phục vụ hoặc dữ liệu thất lạc.

Trong cả ba lượt, các container đã tạm dừng được khôi phục đầy đủ
(`restore_errors=[]`), Compose, `.env` và giới hạn Docker giữ nguyên trong phép thử.
Training được dừng ở mỗi lượt để tách tác động của điều tiết tốc độ. `main` tại
thời điểm ghi nhận các lượt này vẫn ở
`a2c5fbb8459015c1924ed78449de41240e995e13`.

Phần còn thiếu là hoàn tất các lượt đối chứng có phép đo hợp lệ, chạy nhánh LLM
thật, rồi đối chiếu ít nhất ba cặp theo cả thứ tự AB và BA. Cần chỉ ra quyết định
giảm tốc của model và sự phục hồi khi nhu cầu đầu vào vẫn cao, đồng thời báo cáo
đầy đủ dữ liệu chờ hoặc bị từ chối. Không dùng việc backlog giảm sau khi guard
dừng tải làm bằng chứng LLM điều tiết thành công.

[Số liệu tổng hợp](evidence/llm-overload-20261014.json),
[các lượt trước](LLM_OVERLOAD_VALIDATION_20261013.md),
[hướng dẫn chạy lại](LLM_OVERLOAD_EXPERIMENT.md).
Raw artifacts: `runtime/research/llm-proof-20261014-01/`,
`runtime/research/llm-proof-20261014-02/`, `runtime/research/llm-proof-20261014-03/`.
