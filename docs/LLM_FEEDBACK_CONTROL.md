# Điều tiết bằng LLM agent trên nhánh dev

`agents/control_agent.py` chạy trên máy chủ Docker. Model nhận số đo và lịch sử
quyết định, tự chọn trạng thái `light`, `normal`, `heavy`, tốc độ đầu vào, quyền
khởi chạy training và CPU/RAM cho các container được phép. Không có bảng luật
75%/85% để chọn trạng thái hoặc tốc độ thay cho model.

```text
Docker/host + Kafka + Processor metrics
    -> LLM chính / LLM dự phòng
    -> JSON actions được kiểm tra schema và ngân sách
    -> policy.json -> producer / trainer
    -> docker update + docker-compose.yml
    -> số đo và kết quả hành động ở vòng tiếp theo
```

## Thành phần và phạm vi

| Thành phần | Thực hiện |
|---|---|
| `agents/control_agent.py` | Thu lịch sử, gửi số đo cho model, kiểm tra quyết định, ghi lease và receipts |
| `agents/model_router.py` | Chuyển provider khi hết quota, rate limit, timeout, lỗi dịch vụ; cooldown riêng từng provider |
| `agents/runtime_policy.py` | Đọc quyết định có thời hạn; giới hạn phát Kafka và quyền bắt đầu training |
| `agents/compose_resources.py` | Kiểm tra ngân sách, cập nhật CPU/RAM thực tế, xác nhận rồi lưu giới hạn vào Compose |
| `research/telemetry.py` | Offset/lag/throughput theo topic, CPU/RAM Docker, RAM/swap VM, latency/error từ Processor |

Model chỉ nhận số đo, cấu hình giới hạn và lịch sử hành động; không nhận API key,
nội dung tin bất động sản hay quyền chạy shell. Ba trường hành động là
`rate_per_second`, `training_allowed`, `resources`. Chúng được chuyển thành lời
gọi công cụ có kiểu dữ liệu xác định, không phải thực thi mã do model sinh.

Mặc định điều tiết topic `real_estate_stress_raw`; có thể chọn `real_estate_raw`.
Mỗi lần chỉ điều tiết một topic. Publisher của topic khác tiếp tục theo cấu hình
cũ. Chỉ dùng một publisher/scheduler cho topic được điều tiết; không chạy crawler
thủ công đồng thời với scheduler. Processor tiếp tục drain Kafka khi đầu vào bị
tạm dừng. Tốc độ agent cấp cho stress bị chặn thêm bởi trần/profile sinh tải của
stress-agent; agent không tự bật crawler hoặc sinh thêm workload.

Agent phân biệt CPU/RAM của các container được đo so với Docker VM với CPU/RAM
của máy host. Số liệu throughput khi còn backlog chỉ là bằng chứng khả năng xử lý
đã quan sát; không đủ để tuyên bố giới hạn tuyệt đối hoặc forecast. Lưu lượng
`incoming_rate` đã qua cửa kiểm soát Kafka, không phải nhu cầu trước khi bị chặn.
Muốn đo vùng chịu tải, chạy các workload có giới hạn, đủ dài và đối chiếu lag,
latency, lỗi cùng các hành động; không diễn giải một lần thử ngắn thành capacity.

## Cấu hình model và model dự phòng

Cài dependency trên host:

```powershell
python -m pip install -r requirements-dev.txt -r research/requirements.txt
```

Giữ credentials trong `.env` đã được Git bỏ qua. CLI nạp file này; biến môi trường
đã export được ưu tiên. Các biến `CONTROL_LLM_*` để trống sẽ kế thừa nguyên nhóm
`LLM_*` và `LLM_FALLBACK_*`. Muốn tách model điều tiết khỏi extraction, khai báo đủ
provider/base URL/model/key trong nhóm `CONTROL_LLM_*`. Không trộn key của một
endpoint với endpoint khác. `GEMINI_API_KEY` chỉ thay thế key chính ở endpoint
Gemini đã biết. Model cần hỗ trợ chat completions trả JSON.

Ví dụ bật provider dự phòng qua Groq (thay các giá trị trong ngoặc):

```dotenv
LLM_FALLBACK_PROVIDER=groq
LLM_FALLBACK_BASE_URL=https://api.groq.com/openai/v1
LLM_FALLBACK_MODEL=<model được tài khoản cho phép>
LLM_FALLBACK_API_KEY=<key dự phòng>
```

Mặc định `LLM_FALLBACK_PROVIDER=disabled`. Chưa có key dự phòng thì chỉ dùng model
chính. Hệ thống không tự tạo key, mua quota hoặc bật billing. Chọn model thuộc
gói miễn phí của tài khoản nếu muốn tránh phí API; xem [quota Groq](https://console.groq.com/docs/rate-limits).
Quota vẫn có giới hạn. Đổi API key cùng project Gemini không tạo thêm quota độc
lập; xem [quota Gemini](https://ai.google.dev/gemini-api/docs/rate-limits).

Router chia ngân sách timeout giữa những provider khả dụng, thử mỗi provider tối
đa một lần trong một lượt và ghi đúng model thực sự trả kết quả. Quota hết dùng
cooldown dài hơn lỗi tạm thời. Tất cả provider lỗi hoặc JSON sai schema thì agent
tạm dừng đầu vào và không cho bắt đầu training; không tự rơi về policy 75%/85%.
Đây là ngân sách timeout của transport, không phải bảo đảm hủy tức thì mọi hoạt
động DNS/socket. Lease hết hạn bảo vệ publisher khi controller bị treo hoặc dừng.

## Quan sát và xem đề xuất

Docker Desktop phải chạy Linux containers; Kafka, ba Processor và trainer cần
đang chạy. Các port Kafka/metrics là port host được publish trong Compose.

```powershell
# Chỉ đọc số đo; không gọi LLM, không chỉnh policy/Compose/container.
python -m agents.control_agent --observe-only --once

# Gọi LLM để xem một đề xuất; không áp dụng vào publisher/container.
python -m agents.control_agent --once
```

`--once` của lượt đề xuất lấy tối đa hai mẫu để tính delta ban đầu. Sample đầu
có thể thiếu throughput/latency. Kafka topic mới hoàn toàn rỗng có lag bằng 0;
topic còn dữ liệu nhưng không biết committed offset vẫn được coi là chưa đủ
bằng chứng. Khi có xử lý, latency được đo từ `pipeline_sent_at` lúc publish đến
sau commit; timestamp này không làm thay đổi AI source-version identity.

Mode đề xuất/áp dụng lưu quan sát và quyết định ở `runtime/control/`.
`--observe-only` chỉ in số đo ra terminal. Mode mặc định có thể gọi LLM;
chỉ `--observe-only` bảo đảm không gọi provider. Exit code 2 trong `--once` nghĩa
là thiếu bằng chứng hoặc quyết định không thể áp dụng an toàn; không phải thành công.

## Bật vòng điều tiết thật

Đặt các biến sau trong `.env` khi đã có model hoạt động:

```dotenv
CONTROL_ENABLED=true
CONTROL_RESOURCE_ENABLED=true
CONTROL_SOURCE_TOPIC=real_estate_stress_raw
CONTROL_MAX_RATE=100
CONTROL_CPU_BUDGET_FRACTION=0.75
CONTROL_MEMORY_BUDGET_FRACTION=0.70
STRESS_ENABLED=true
STRESS_RUN_ID=llm-control-20261006-01
STRESS_LOAD_PROFILE=normal
STRESS_RATE_PER_SECOND=10
STRESS_MULTIPLIER=10
STRESS_MAX_RATE=100
STRESS_DURATION_SECONDS=300
STRESS_MAX_RECORDS=30000
```

Các số là ngân sách vận hành ban đầu, không phải ngưỡng phân loại tải của LLM.
Mỗi lượt stress chủ động cần một `STRESS_RUN_ID` mới; ID đã hoàn tất không chạy lại.
Review tổng RAM/CPU phù hợp máy trước khi chạy. Compose trên `dev` khai báo mỗi
Processor 1 CPU/768 MiB và trainer 1 CPU/1024 MiB, có tổng RAM+swap rõ ràng. Các
biến `PROCESSOR_*_LIMIT`, `TRAINER_*_LIMIT` cho phép đổi phân bổ ban đầu. Giới hạn
chỉ được áp dụng lên container khi Compose được chạy lại; agent từ chối đổi live
từ quota CPU unlimited vì không thể rollback chính xác bằng `docker update`.

```powershell
docker compose config --quiet
docker compose build processor processor-2 processor-3 trainer stress-agent scraper
docker compose up -d processor processor-2 processor-3 trainer stress-agent
python -m agents.control_agent --once
python -m agents.control_agent --apply
```

Đây là các lệnh vận hành, không được tự chạy trong quá trình kiểm thử code.
Recreate để đặt quota ban đầu có thể gián đoạn dịch vụ. Khi đã bật control, producer
và trainer đợi lease hợp lệ, nên cần chạy controller trước khi hết thời lượng của
lượt stress. Đổi topic sang `real_estate_raw` cần bật crawler và triển khai `scraper`
với cùng cấu hình; kiểm soát này không bỏ qua pacing/robots của nguồn crawl.

Agent kiểm tra producer/trainer có cùng topic và bind mount policy trên host.
Nếu Airflow đang chạy, nó cũng phải bật control; trong mode này task training
Airflow được skip, container trainer riêng chịu trách nhiệm fit. Với raw-topic,
chỉ chạy một trong hai scheduler Airflow và scraper. Một fit đang chạy được phép
hoàn tất; quyền mới chỉ điều khiển việc bắt đầu fit tiếp theo. Shared OS lock tránh
hai tiến trình fit đồng thời; khóa được hệ điều hành nhả khi process kết thúc.
`training.json` là trạng thái cuối đã ghi, không phải heartbeat chứng minh process
còn sống sau một crash.

Ctrl+C dừng controller và ghi lease tạm dừng đầu vào/training trước khi nhả khóa.
Khi process bị kill hoặc mất điện, lease tự hết hạn sau `CONTROL_LEASE_SECONDS`.
Model không được nới mức rate tối đa, floor RAM khẩn cấp hoặc ngân sách tài nguyên.
`CONTROL_FAILSAFE_RATE=0` là mặc định để ngừng phát khi lease thiếu/sai/hết hạn.

## Agent sửa và áp dụng giới hạn Docker

Chỉ bốn service `processor`, `processor-2`, `processor-3`, `trainer` được chỉnh.
Broker, replicas và service khác nằm ngoài phạm vi công cụ. Trình tự:

1. Đọc capacity Docker, allocation/ID/project label và memory usage của cgroup.
2. Kiểm tra giới hạn từng service và tổng tất cả service được quản lý; RAM còn
   headroom, không thấp hơn memory reservation. Các quota gốc phải rollback được.
3. Tạo YAML giữ comments, chỉ đổi `deploy.resources.limits.cpus/memory` và
   `memswap_limit`, chạy Compose validation, lưu backup và kiểm tra file chưa bị sửa.
4. Tạm dừng ingress; nhả tài nguyên trước khi cấp lại, gọi `docker update` bằng
   argv cố định, xác minh runtime rồi lưu Compose bằng thay thế file atomic.
5. Khi xác nhận thành công mới cấp lease theo quyết định. Thất bại giữ ingress
   tạm dừng, thử rollback và lưu phần chưa khôi phục được; không ghi đè thay đổi
   đồng thời của người vận hành.

`memswap_limit` là RAM cộng swap; công cụ giữ lượng swap bổ sung hiện có. Docker
hỗ trợ cập nhật tài nguyên Linux containers khi đang chạy; xem
[docker update](https://docs.docker.com/reference/cli/docker/container/update/).
Agent không gọi `compose up/down` và không tự recreate container. Phân bổ được
lưu trực tiếp trong Compose thành giá trị cụ thể; biến `_LIMIT` không ghi đè giá
trị đã được agent lưu. Review diff/receipt khi muốn đổi lại cấu hình.

Backup `.resource-agent-*` và lock `*.resource-agent.lock` không được commit.
Sau sự cố abrupt exit trong resource transaction, kiểm tra receipts/runtime và
backup trước khi xóa lock còn lại; không tự đoán rằng rollback đã xong. Resource
limits đã áp dụng không tự reset khi controller dừng.

## Kiểm chứng và giới hạn hiện tại

Các tests dùng model/Docker giả kiểm tra quyết định LLM, producer/trainer lease,
fallback, deadline, quota, timestamp latency, cold start Kafka, giới hạn tài
nguyên, xác nhận runtime, file CAS, rollback và các thay đổi đồng thời.

```powershell
python -m pytest -q utils/tests/test_control_agent.py utils/tests/test_runtime_policy.py utils/tests/test_model_router.py utils/tests/test_compose_resources.py
python -m pytest -q -ra
```

Sau khi Docker được bật ngày 06/10/2026, đã kiểm chứng kết nối Gemini, áp dụng
CPU/RAM vào Docker/Compose, rollback và cơ chế tạm dừng khi thiếu RAM/provider lỗi.
Lượt thử chưa áp dụng được quyết định LLM để phát tải, nên chưa chứng minh hiệu quả
điều tiết hay capacity. Có sửa lỗi parser dung lượng Docker dạng ký hiệu khoa học.
Chi tiết và bằng chứng: [kiểm chứng thực tế 06/10](LLM_FEEDBACK_VALIDATION_20261006.md).
Model dự phòng cần credentials/route được cấu hình trước khi thử switch thật.
Lượt chạy lại ngày 07/10 đã nhận quyết định LLM, phát và xử lý dữ liệu thật,
đồng thời chuyển model thành công khi HTTP 503; xem
[kiểm chứng vòng phản hồi và fallback](LLM_FEEDBACK_VALIDATION_20261007.md).
Các kết quả adaptive trong nghiên cứu cũ thuộc policy cũ, không chứng minh hiệu quả
của LLM agent này.
