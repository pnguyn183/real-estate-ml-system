# Kiểm chứng ngày 14/10/2026

**Kết luận: `inconclusive`, chưa chứng minh LLM ổn định hệ thống khi quá tải.**

Đã sửa và push lỗi đọc/ghi policy đồng thời trên Windows tại commit `e972055`:
chỉ thử lại `PermissionError` trong khoảng thời gian giới hạn; policy sai, mất
hoặc hết hạn vẫn đóng gate. 202 kiểm thử liên quan đạt. Kiểm tra file tạm trên
Windows với 180 lần ghi và 1.801 lần đọc trong 3,012 giây không có lỗi ghi hoặc
đọc lease không hợp lệ. Đây là kiểm tra I/O, không phải bằng chứng LLM điều tiết.

Lượt Docker `llm-proof-20261014-01` đã phát 43 bản ghi trong bước hiệu chuẩn.
Cả 43 được Kafka ACK và được observer xác nhận consumer commit; không có lỗi
enqueue/delivery hoặc bản ghi đang chờ khi kết thúc. RAM host xuống ngưỡng bảo
vệ 10%, nên lượt thử dừng với `emergency_host_memory` trước khi gọi model.
Có **0 quyết định LLM, 0 cặp đối chứng hợp lệ**. Không ghi nhận lỗi file policy
trong lượt này; tải chưa đủ dài để kết luận độ ổn định của bản sửa ở tải cao.

Các container đã tạm dừng được khôi phục đầy đủ (`restore_errors=[]`), Compose,
`.env` và giới hạn Docker giữ nguyên trong phép thử. `main` vẫn ở
`a2c5fbb8459015c1924ed78449de41240e995e13`.

[Số liệu tổng hợp](evidence/llm-overload-20261014.json),
[các lượt trước](LLM_OVERLOAD_VALIDATION_20261013.md),
[hướng dẫn chạy lại](LLM_OVERLOAD_EXPERIMENT.md).
Raw artifacts: `runtime/research/llm-proof-20261014-01/`.
