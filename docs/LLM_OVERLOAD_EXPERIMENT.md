# Kiểm chứng LLM điều tiết khi quá tải

`research.llm_experiment` chạy **FeedbackAgent thật** trên `dev`, dùng model/API
đã cấu hình. `research.run_experiment` vẫn là đối chứng bằng TrafficController
theo luật; kết quả của lệnh cũ không phải bằng chứng cho LLM.

## Chạy thử

Docker Desktop, Kafka, MongoDB và ba Processor có instrumentation phải đang
chạy. Cài dependencies trong `requirements-dev.txt` và `research/requirements.txt`.
API key giữ trong `.env` như cấu hình agent hiện tại.

```powershell
# Hiệu chuẩn, rồi chạy 3 cặp theo thứ tự baseline/LLM, LLM/baseline, baseline/LLM.
python -m research.llm_experiment --output runtime/research/llm-proof

# Model Gemini dự phòng dùng cùng key, chỉ cấu hình trong tiến trình này.
python -m research.llm_experiment --output runtime/research/llm-proof-fallback --fallback-model gemini-3.5-flash-lite

# Chỉ tìm mức tải quá tải, không gọi model.
python -m research.llm_experiment --calibrate-only --output runtime/research/llm-calibration
```

Mỗi lần cần một thư mục output mới. Cấu hình ở
[`research/llm_experiment.json`](../research/llm_experiment.json). Dự kiến khoảng
25 phút cho hiệu chuẩn và sáu lượt; thời gian thực tế phụ thuộc queue và API.
Lượt bị lỗi hoặc guard sẽ dừng chuỗi thử, lưu bằng chứng và khôi phục dịch vụ.

RAM trống phải **trên 10% ở cả host và Docker VM**; cần thêm khoảng trống vì RAM
có thể giảm trong lúc chạy. Không hạ ngưỡng để bỏ qua điều kiện thử. Model dự phòng
cùng project không tạo quota độc lập; tài khoản cần quyền dùng model tương ứng.

## Thiết kế đối chứng

- Giữ nguyên CPU/RAM Docker, kiểm tra allocation thực tế và container ID trước,
  trong, sau mỗi lượt. Không sửa Compose hay `.env`.
- Dừng các publisher khác, trainer, Airflow/AI worker và dịch vụ giao diện/API
  phụ trợ đang chạy. Cuối phiên khôi phục đúng ID đã chạy trước phiên.
- Cùng seed, nội dung, khóa partition, lịch nhu cầu và giới hạn queue trong mỗi
  cặp. URL/run ID khác nhau để không ghi đè bản ghi giữa các lượt.
- Baseline dùng trần admission cố định; LLM chọn trần từ telemetry. Cùng trần
  khởi đầu và cùng điều kiện dừng bảo vệ. Huấn luyện dừng ở cả hai lượt;
  `resources` phải rỗng vì phép thử này chỉ đo tác động của điều tiết tốc độ.

Không xóa dữ liệu hoặc reset offset. Dữ liệu mô phỏng vào topic/DB stress.
Kiểm tra model không đổi chỉ áp dụng trong phiên đã cô lập; trainer có thể cập
nhật model theo lịch bình thường sau khi được khôi phục.

Hiệu chuẩn tăng nhu cầu theo các mức cấu hình. Cần ít nhất ba mẫu liên tiếp trong
cùng giai đoạn có p95 vượt mục tiêu hoặc Kafka lag tăng liên tiếp ít nhất 20 bản
ghi từ mẫu đầu đến mẫu cuối, đồng thời ingress
đạt ít nhất 80% nhu cầu. Nguồn sinh tải chậm không được coi là quá tải pipeline.
Mức tìm được chỉ là giới hạn quan sát với workload và allocation này, không phải
giới hạn tuyệt đối của máy. Không tìm được thì kết luận chưa đủ bằng chứng.

Mỗi lượt sau hiệu chuẩn chạy profile nhẹ → quá tải → nhẹ. Lịch nhu cầu, gửi Kafka,
poll commit, telemetry và gọi model chạy độc lập; rate gate đóng hoặc API chậm
không làm biến mất nhu cầu khỏi số đếm.

## Các số đo

| Chỉ số | Ý nghĩa |
|---|---|
| `offered` | Yêu cầu phát sinh theo lịch, trước rate gate |
| `admitted` | Yêu cầu được phép thử gửi Kafka |
| `deferred` | Yêu cầu phải chờ; có thể sau đó được gửi hoặc bị từ chối |
| `rejected` | Queue đầy, hết hạn chờ hoặc lượt thử dừng |
| `acknowledged` | Kafka xác nhận nhận bản ghi; chưa phải xử lý xong |
| `committed` | Quan sát offset consumer đã đi qua bản ghi |
| `offered_to_commit_p95_upper_seconds` | p95 từ lịch nhu cầu đến lúc quan sát commit, gồm chờ gate |
| `latency_p95_seconds` | p95 histogram Processor từ gửi Kafka đến sau commit |
| `scheduler_lateness_*` | Độ trễ của chính nguồn sinh tải |

Latency từ offered là **cận trên do polling**, chỉ tính các yêu cầu đã quan sát
commit. Luôn đọc cùng tỷ lệ `committed/offered`, số bị từ chối và số chưa xong.
Consumer có thể commit cả nhánh lỗi/DLQ nên còn phải đối chiếu error/outcome
metrics; không coi mọi commit là ghi MongoDB thành công. Không gộp trung bình các
p95 theo khoảng thành p95 toàn lượt.

## Điều kiện kết luận

Chỉ ghi `demonstrated` khi có ít nhất ba cặp hợp lệ với cả hai thứ tự và mỗi cặp:

1. Baseline thực sự quá tải, nguồn tải và telemetry hợp lệ.
2. Có quyết định giảm tốc độ do model trả về, ghi provider/model/thời điểm.
3. Ingress giảm sau hành động và hệ thống phục hồi với throughput dương **khi nhu
   cầu vẫn ở mức quá tải**, trước khi profile tự hạ tải.
4. Lag hoặc thời gian vi phạm latency cải thiện, chỉ số chính còn lại không xấu
   đi, lỗi không tăng. Báo cáo nêu cả chi phí chờ/từ chối yêu cầu.

Pause vì RAM, telemetry, API hoặc lease có nguồn `guard`, không được nhận là
thành công của LLM. Kết quả có thể là `inconclusive` hoặc `not_demonstrated`;
không ép model trả một hành động định sẵn để đạt kiểm chứng. Thử nghiệm này chưa
chứng minh LLM tốt hơn controller theo luật hoặc lợi ích của tự chỉnh Docker
limits; các giả thuyết đó cần đối chứng riêng.

## Bằng chứng và khôi phục

- `comparison.md/json/png/svg`: kết luận, chỉ số từng cặp và đồ thị có mốc LLM/guard.
- `pair-*/run.json`, `telemetry.jsonl`, `events.jsonl`, `control/decisions.jsonl`:
  số đo từng lượt, từng yêu cầu và hành động thực tế.
- `calibration.json`, `selected_profile.json`: lý do chọn mức tải.
- `identity.json`, `config.json`, `isolation.json`, `session.json`: mã nguồn,
  cấu hình, container và kết quả khôi phục.

Artifacts trong `runtime/` được Git bỏ qua. Chỉ đưa tổng hợp đã kiểm tra vào docs.
Nếu tắt cưỡng bức/mất điện, khôi phục từ manifest:

```powershell
python -m research.llm_experiment --restore runtime/research/llm-proof/isolation.json
```

Lệnh chỉ khôi phục thao tác chưa hoàn tất với đúng container ID, không tạo
container hoặc khởi động dịch vụ vốn đã tắt.
