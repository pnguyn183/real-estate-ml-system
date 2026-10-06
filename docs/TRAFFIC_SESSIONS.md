# Tự điều khiển phiên thu thập lịch sử traffic

Mỗi phiên tạo workload có biến thiên qua Kafka và processor thật, đồng thời lưu
telemetry để nghiên cứu dự báo trước 5/10 phút. Đây là **workload tổng hợp có kiểm
soát**, không phải lịch sử crawl tự nhiên. Target `requested_rate` là tải yêu cầu
trước admission; `incoming_rate` là tốc độ thực tế vào Kafka. Không trộn hai target.

Các phiên có thể dài khác nhau và chạy vào những ngày khác nhau. Không cần để
laptop chạy 24/24. Giữ máy thức và Docker hoạt động trong phiên; khoảng thời gian
máy nghỉ không được biến thành traffic bằng 0.

## Khởi chạy khi rảnh

Mở terminal ở thư mục repository. Docker và các service Kafka/MongoDB/processor
phải hoạt động; lệnh thu thập kiểm tra cluster ba broker, telemetry và backlog
trước khi phát record. Mặc định nó không bật/tắt service hoặc sửa `.env`. Cờ
`--lean`/`-Lean` bên dưới cho phép tạm dừng các service phụ trong thời gian thu.

**Mở Docker không tự khởi chạy phiên này.** Các container có restart policy có
thể tự chạy lại; crawler có thể tiếp tục crawl khi `CRAWL_ENABLED=true` và
Prometheus tiếp tục scrape metrics. Phiên tạo tải biến thiên và xuất JSONL dưới
đây chỉ bắt đầu khi bạn nhập lệnh `start`. Dừng phiên không dừng crawler hoặc
Prometheus; lần sau cần chủ động chạy một phiên mới.

```powershell
# Sau bản sửa 01/10: chạy kiểm tra 20 phút với ít service hơn trước khi thu dài.
python -m research.collect_session start --minutes 20 --lean

# Khi phiên kiểm tra có telemetry khỏe, chạy phiên dài do bạn chủ động chọn.
python -m research.collect_session start --hours 2 --lean

# Chỉ xem kế hoạch, không phát dữ liệu hoặc tạo phiên.
.\scripts\traffic_session.ps1 -Hours 2 -DryRun

# Tương đương Python --lean: sau tải sẽ drain, khôi phục service và lập báo cáo.
.\scripts\traffic_session.ps1 -Hours 2 -Lean

# Phiên khác: 90 phút, tên dễ nhớ.
.\scripts\traffic_session.ps1 -Minutes 90 -Name "toi-thu-tu"

# Có thể dùng giờ lẻ hoặc đặt biên tải/seed để tái lập kịch bản.
.\scripts\traffic_session.ps1 -Hours 1.5 -MinRate 5 -MaxRate 40 -Seed 42
```

Nếu PowerShell chặn script do execution policy, dùng Python trực tiếp:

```powershell
python -m research.collect_session start --hours 2
python -m research.collect_session start --minutes 90 --name "toi-thu-tu"

# Xem trạng thái / dừng ở terminal thứ hai, không cần chạy file .ps1.
python -m research.collect_session status
python -m research.collect_session stop
```

Lỗi `running scripts is disabled on this system` / `PSSecurityException` nghĩa
là PowerShell chưa thực thi lệnh `.ps1`, kể cả `-Action stop`. Phiên đang chạy
không bị dừng bởi lỗi này. Dùng các lệnh Python phía trên; không cần đổi execution
policy. Với `stop`, đợi trạng thái cuối `stopped` hoặc `completed` trước khi tắt
máy; `stop_requested` chỉ xác nhận đã gửi yêu cầu.

Terminal hiển thị thư mục phiên và tiến độ đo thực tế. Giữ terminal mở. Đây là
tiến trình foreground, không phải dịch vụ chạy nền. Helper Windows ngăn idle
sleep trong phiên; không ngăn việc chủ động sleep, đóng nắp hoặc tắt máy.

Mặc định: tải trong khoảng 5–60 record/giây, đo mỗi 5 giây, một policy baseline
giới hạn cố định; không tự tăng CPU/RAM, không chạy cặp baseline/adaptive. Biên tải
không phải cam kết laptop chịu được mức đó. Safety guard dừng khi CPU/RAM/lag vượt
ngưỡng hoặc mất telemetry ngoài ngân sách phục hồi bên dưới. Cấu hình đầy đủ
được ghi vào từng phiên.

## Xem trạng thái và dừng sớm

Mở terminal thứ hai:

```powershell
# Phiên mới nhất trong thư mục mặc định.
.\scripts\traffic_session.ps1 -Action status
.\scripts\traffic_session.ps1 -Action stop

# Chỉ rõ phiên bằng đường dẫn mà lệnh start đã in ra.
.\scripts\traffic_session.ps1 -Action status -Session "runtime/research/traffic-sessions/<session-id>"
.\scripts\traffic_session.ps1 -Action stop -Session "runtime/research/traffic-sessions/<session-id>"
```

Hoặc nhấn **Ctrl+C** trong terminal đang chạy. Dừng có kiểm soát sẽ ngừng tạo tải,
đợi Kafka/processor xử lý backlog trong ngân sách drain, rồi lưu báo cáo. Nó không
dừng Docker. Dữ liệu đã thu vẫn được giữ. Không dùng đóng cửa sổ/kill process làm
cách dừng thường xuyên: JSONL đã flush có thể còn, nhưng báo cáo cuối có thể thiếu.

Thời lượng `-Hours`/`-Minutes` là thời gian **tạo tải**, chưa gồm preflight, drain
(tối đa 120 giây theo cấu hình phiên) và tạo biểu đồ. Lệnh stop có thể phải chờ
một phép đo/preflight đang thực hiện hoàn tất.

## Giảm bộ nhớ khi thu trên laptop

`--lean` là lựa chọn chủ động, không phải mặc định. Nó tạm **stop** các container
đang chạy của `airflow`, `trainer`, `scraper`, `api`, `predictor`, `frontend`,
`grafana`, `mongo-express`, `ai-agent`, `stress-agent`. Vì vậy giao diện/API, crawl,
training và Gemini sẽ không phục vụ trong phiên lean. Ba broker, ba processor,
MongoDB, ZooKeeper và Prometheus được giữ lại để đo pipeline thật.

Trước khi stop, công cụ ghi ID và trạng thái vào `environment.json`. Khi hoàn tất,
nhận `stop`/Ctrl+C hoặc gặp lỗi, nó khởi động lại đúng các container đã chạy trước
phiên, sau khi kiểm tra ID và cấu hình chưa bị thay thế. Container vốn dừng hoặc
paused không bị bật lên. Không recreate container, xóa volume, sửa `.env` hay tăng
RAM của WSL. Không có `--lean` thì không thay đổi trạng thái service.

Mất điện/kill process không thể chạy bước khôi phục. Khi đó hoặc khi báo
`restore_failed`, xem `environment.json` và xử lý những service chưa khôi phục;
không xóa bằng chứng hay mặc định khởi động toàn bộ stack. Lợi ích giảm RAM của
chế độ này còn cần đo trên phiên thực tế của máy bạn.

## Khi đọc metrics bị timeout

Phiên mới phục hồi có giới hạn cho lỗi transport đã phân loại của HTTP metrics
worker, Docker timeout và Kafka timeout/mất kết nối. Lỗi được xác định theo loại
exception/mã Kafka, không đơn thuần tìm chữ “timeout” trong thông báo.

- Khi phát hiện lỗi, trạng thái chuyển sang `recovering`: tạm khóa gửi record
  mới vào Kafka nhưng tiếp tục đo. Tải theo lịch không được gửi sẽ được đếm trong
  `rejected` và `telemetry_recovery.withheld_total`; không gửi bù thành burst.
- Sau **hai lần đo tốt liên tiếp**, mở lại admission và tiếp tục phiên.
- Dừng `safety_stopped` nếu có **năm lần đo lỗi liên tiếp**, hoặc chưa phục hồi sau
  **60 giây** kể từ khi phát hiện lỗi đầu tiên. Kiểm tra deadline diễn ra giữa các
  phép đo; phép đo đang chạy có timeout riêng. Ngân sách không gồm drain/đóng tài
  nguyên. Đồng hồ thời lượng phiên vẫn chạy trong thời gian phục hồi.
- CPU/RAM/lag vượt ngưỡng vẫn dừng ngay. Chỉ cho phép thiếu số đo core nếu có đúng
  lỗi transport Docker/Kafka tương ứng để tạm khóa admission. Thiếu số đo không rõ
  nguyên nhân, lỗi instrumentation, counter reset hoặc lỗi ngoài nhóm transport
  vẫn dừng; không coi dữ liệu thiếu là số 0 hay tự bỏ qua.
- `stop` và Ctrl+C vẫn dùng được trong lúc `recovering`.

Các giá trị trên được lưu ở `config.json.telemetry_recovery`. Runner paired cũ
không khai báo cấu hình này vẫn giữ mặc định dừng ngay ở lỗi đầu tiên. Preflight
của phiên có ngân sách **60 giây** để thử lại lỗi transport trước tải; vẫn yêu cầu
hai phép đo khỏe, queue đã drain và không có producer khác trên topic thử nghiệm.
Sai topology, backlog, telemetry hỏng không phân loại được hoặc áp lực bộ nhớ không
được retry để vượt qua kiểm tra an toàn. `baseline/report.json.preflight_attempts`
lưu các lần thử. Có thể điều chỉnh bằng `--recovery-seconds`, `--recovery-errors`,
`--preflight-seconds`; không tăng chúng để che lỗi thiếu bộ nhớ.

Ngoài RAM của nhóm container, công cụ đọc `/proc/meminfo` qua processor để quan sát
RAM/swap toàn VM Docker/WSL. Guard dừng khi **MemAvailable ≤10%**, hoặc khi **swap
đã dùng ≥95% đồng thời MemFree ≤5%**. Ngưỡng được ghi trong `vm_memory_safety` của
config từng phiên. Các chỉ số `vm_memory_available_percent`,
`vm_memory_free_percent`, `vm_swap_used_percent` nằm trong observations và trạng
thái phiên; chúng không phải phần trăm RAM của Windows.

Lỗi gốc giữ nguyên trong `observations.jsonl.errors` và được phân loại thêm qua
`collection_transport_errors` (`worker_transport_errors` giữ để tương thích).
`telemetry_usable=false` đánh dấu các mẫu lỗi/chờ phục
hồi; benchmark, inference và history audit không nối cửa sổ dự báo qua các mẫu
này. Sự kiện pause/resume/stop lưu trong `baseline/report.json.telemetry_recovery`.
`session.json` cũng có trạng thái và lý do dừng; lỗi lúc drain không ghi đè nguyên
nhân dừng ban đầu nữa. Không sửa ngược các báo cáo phiên cũ.
Nếu lỗi ở bước chuẩn bị/thu thập/khôi phục/lập audit, `failure_phase` và `error_hint`
chỉ rõ bước cần kiểm tra. Xem `report_path` hoặc `environment_path` tương ứng;
file có thể chưa được tạo nếu lỗi xảy ra ngay lúc chuẩn bị. Lỗi khôi phục không
làm mất kết quả workload đã hoàn tất.

## Nguyên nhân đã tìm thấy và giới hạn kiểm chứng

Phiên `session-20260928T095003.698081Z-421b046d` từng dừng sau khoảng 330 giây vì
HTTP metrics timeout; RAM 57,74% khi đó chỉ đo các container được chọn. Những phiên
sau còn lỗi Docker stats và Kafka, nên không thể kết luận chỉ là endpoint metrics.

Đối chiếu ngày 01/10 với log kernel Docker/WSL ngày 28/09 tìm thấy lỗi cấp phát
**512 KiB** trên kết nối Hyper-V, **RAM trống khoảng 61,6 MiB**, **2 GiB swap đã dùng
hết**. Các mốc lỗi cấp phát trùng cửa sổ lỗi của ba phiên sau. Bằng chứng đã trích
và nguồn/dòng log nằm ở
`runtime/research/telemetry-diagnosis-20261001.json` (local, được gitignore).
Đây là bằng chứng áp lực bộ nhớ trong VM; RAM khoảng 58% của nhóm container bỏ sót
phần bộ nhớ khác. Chưa xác định riêng service nào dùng phần còn lại, và đây không
phải chẩn đoán hỏng phần cứng laptop.

Bản sửa bổ sung guard bộ nhớ VM, chế độ lean và retry có khóa admission. Tại lần
cập nhật này Docker Engine chưa hoạt động nên chưa chạy lại phiên Kafka thật với
bản sửa. Các test kiểm tra nhánh phục hồi bằng lỗi có chủ đích không thay thế thử
nghiệm dài thực tế. Bật Docker, chạy phiên lean 20 phút, xem `history_audit.json`,
`baseline/report.json` và RAM/swap VM trước khi tăng lên 1–3 giờ. Nếu VM đã cạn bộ
nhớ, retry sẽ không sửa được điều đó; guard phải dừng để giữ an toàn.

Validation bản sửa ngày 01/10: 392 test liên quan session/recovery/preflight/lean,
runner/telemetry/forecast/history/control/report/resource actuator đều pass;
13 warning của NumPy/joblib. Báo cáo JUnit local:
`runtime/research/telemetry-fix-validation-20261001.xml`. Compileall, Compose
config, Python/PowerShell dry-run có `--lean` và `git diff --check` pass. Dry-run
không gọi Docker hoặc phát record; các kết quả này không chứng minh phiên dài ổn định.

## Chọn thời lượng

Lệnh cho phép từ 1 phút đến 8 giờ; giới hạn này là ngân sách vận hành, không phải
kết luận thống kê. Phiên ngắn dùng kiểm tra công cụ. Nên bắt đầu với khoảng 1–3 giờ
cho nghiên cứu forecast, kiểm tra chất lượng sau mỗi phiên. Một giờ cũng chưa bảo
đảm đủ train/validation/test hoặc đủ biến thiên.

Mỗi phiên bị giới hạn tối đa một triệu record dự kiến. Nếu thời lượng và biên tải
khiến kế hoạch vượt ngân sách, lệnh từ chối trước khi phát tải; giảm biên tải hoặc
chia phiên. Không hạ ngưỡng đánh giá mô hình chỉ để có điểm số.

## File bằng chứng và ghép nhiều phiên

Mặc định dữ liệu nằm dưới `runtime/research/traffic-sessions/` (được gitignore):

- `config.json`: profile và seed đã chọn trước khi chạy, các ngưỡng an toàn.
- `session.json`: thời lượng yêu cầu, trạng thái và thông tin phiên.
- `environment.json`: nếu chọn lean, kế hoạch stop/restore, ID container và kết quả
  khôi phục service; không lưu biến môi trường hay secret.
- `baseline/observations.jsonl`: timestamp, run_id, topic, requested/input rate,
  throughput, CPU/RAM container, RAM/swap VM, Kafka lag, latency/error và tải từng broker.
- `baseline/report.json`, `baseline/manifest.json`, `baseline/actions.jsonl`:
  kết quả runner, phiên bản code/config và quyết định của policy.
- `history_audit.json` và `history.png` được tạo sau khi phiên kết thúc; kiểm tra lỗi,
  khoảng trống, thời lượng liên tục và phân bố target trước khi train.

Khi có nhiều phiên đã kết thúc, dùng đường dẫn **thư mục phiên**:

```powershell
python -m research.collect_session export --sessions `
  runtime/research/traffic-sessions/<session-1> `
  runtime/research/traffic-sessions/<session-2> `
  --output runtime/research/traffic-sessions/training-history.jsonl

python -m research.history_audit `
  --input runtime/research/traffic-sessions/training-history.jsonl `
  --target requested_rate --output runtime/research/traffic-sessions/merged-audit

python -m research.benchmark traffic `
  --input runtime/research/traffic-sessions/training-history.jsonl `
  --target requested_rate --horizons 300 600 --lag-seconds 60 120 `
  --output runtime/research/traffic-sessions/forecast-evaluation
```

Export giữ timestamp/run_id/topic, lỗi đo và khoảng trống; không nội suy, không
biến downtime thành 0, không ghi đè file đích. Manifest export ghi nguồn và hash
để đối chiếu. Export thành công không có nghĩa mọi dòng đều dùng được để train.
Benchmark tự ngắt cửa sổ tại ranh giới phiên/gap/lỗi và chia theo thời gian, có
embargo để nhãn tương lai không lấn vào test. Giữ lại các phiên/profile khác để
đánh giá khả năng tổng quát; không dùng kết quả test để sửa kịch bản cho đẹp điểm.

Thu thập nhiều phiên không chứng minh seasonality ngày/tuần, không hoàn tất kết
nối forecast vào controller, và không bảo đảm cải thiện so với persistence.
Script cũ `collect_traffic_history.ps1` vẫn dành cho quan sát traffic đang có,
không tự tạo workload biến thiên.

## Kiểm chứng công cụ ngày 28/09/2026

Đây là bằng chứng của phiên bản trước guard VM/preflight retry/lean ngày 01/10,
không phải xác minh runtime của những thay đổi mới này.

Đã chạy hai phiên nhỏ qua Kafka thật, bằng PowerShell wrapper, biên tải 1–3
record/giây. Đây là kiểm tra công cụ, chưa phải dataset đủ dài để train:

| Phiên | Kết quả | Kafka acknowledged | Mẫu telemetry | Lỗi đo | Lag cuối |
| --- | --- | ---: | ---: | ---: | ---: |
| Đặt 2 phút, seed 42 | `completed`, tự hết thời gian | 126 | 30 | 0 | 0 |
| Đặt 2 phút, seed 43 rồi gửi `stop` | `stopped`, giữ dữ liệu và drain | 17 | 9 | 0 | 0 |

Bằng chứng local: `runtime/research/traffic-session-validation/`, các thư mục
`session-20260928T071024.155927Z-98c7bc2d` và
`session-20260928T071302.864955Z-2a28e7a8`. Mỗi thư mục có `session.json`,
`baseline/report.json`, `baseline/observations.jsonl`, `history_audit.json` và
`history.png`. Export hai phiên tạo `combined.jsonl` với 39 dòng và manifest hash;
benchmark ở `forecast-check/metrics.json` báo `insufficient_data` cho cả 300/600
giây đúng như dự kiến. Không có điểm forecast giả hoặc model được export.

Validation: 150 tests thuộc session/runner/telemetry/forecast/history/control/report
pass; compileall cho các Python sửa/thêm, Docker Compose config và
`git diff --check` pass. Không thay đổi `.env`, restart policy hoặc trạng thái
service Docker. Hai phiên thử đều đã kết thúc; không cài lịch tự chạy.
