# Project Report

**Cập nhật checklist: 26/10/2026 · Nhánh: `dev` · Mã nguồn đối chiếu: `6bc14df`.**

Báo cáo này thay thế `BAO_CAO_GIANG_VIEN.md`, tổng hợp các yêu cầu trong cuộc
trao đổi và những hạng mục nghiên cứu đã ghi trong báo cáo trước. Các yêu cầu
“tiếp tục”, kiểm tra RAM và chạy lại được gộp vào công việc tương ứng, không
được tính thành tính năng mới. Kết quả thực nghiệm mới nhất được đối chiếu là
ngày **24/10/2026**; cập nhật tài liệu ngày 26/10 không phải một lượt chạy mới.

**Trạng thái tổng thể:** đã có stress agent, extraction agent, vòng phản hồi LLM,
công cụ sửa tài nguyên Docker/Compose và hệ thống đo lường. Đã xác minh vòng LLM
ở tải nhỏ và chuyển model khi lỗi 503. **Chưa chứng minh LLM tự điều tiết để giữ
ổn định khi quá tải; chưa thu xong lượt telemetry bổ sung hai giờ.** Dự báo
5 phút đạt tiêu chí thử nghiệm trên dữ liệu tổng hợp; 10 phút chưa đạt.

Quy ước checklist:

- `[x]`: hoàn thành đúng phạm vi ghi trong mục; có thể chỉ là hoàn thành mã/công cụ nếu được ghi rõ.
- `[ ] Một phần`: đã có triển khai hoặc bằng chứng hạn chế, nhưng yêu cầu tổng thể còn thiếu.
- `[ ] Chưa hoàn thành`: chưa có triển khai hoặc chưa có bằng chứng đạt tiêu chí.
- **Git**: bằng chứng đã lưu trong repository. **Local**: file trong `runtime/` hoặc artifact trên máy chạy, không có khi chỉ clone GitHub. Test mô phỏng không thay thế số đo thực tế.

## 1. Checklist các yêu cầu đã giao

### 1.1. Giữ bản an toàn và phân nhánh

- [x] **R01 — Giữ `main` ở bản hai agent stress/extract, không chứa điều tiết.** Baseline được tạo tại `c51b23a`; `main` hiện ở `a2c5fbb`. Cây `agents/` trên nhánh này không có control agent, model router hay resource actuator; không có thư mục `research/`. [Commit baseline](https://github.com/pnguyn183/real-estate-ml-system/commit/c51b23a).
- [x] **R02 — Bỏ báo cáo giảng viên và file `frontend(admin,user)` khỏi `main`.** Commit `a2c5fbb` xóa cả hai; ứng dụng trong thư mục `frontend/` vẫn thuộc baseline. [Commit dọn nhánh main](https://github.com/pnguyn183/real-estate-ml-system/commit/a2c5fbb).
- [x] **R03 — Phát triển và push phần điều tiết lên `dev`.** Trước lần cập nhật báo cáo này, remote `dev` ở `6bc14df`; remote `main` vẫn ở `a2c5fbb`. Báo cáo này thuộc `dev`. [Lịch sử dev](https://github.com/pnguyn183/real-estate-ml-system/commits/dev).
- [x] **R04 — Có checklist tổng hợp yêu cầu, khả năng, tiến độ và bằng chứng; đổi tên báo cáo thành Project Report.** Chính tài liệu này và các liên kết ở [README](../README.md), [mục lục tài liệu](README.md).

### 1.2. Agent quan sát và tự điều tiết

- [x] **R05 — Có telemetry phục vụ điều tiết.** Đã đo CPU/RAM container, RAM/swap VM, tài nguyên host, Kafka ingress/throughput/lag, latency, lỗi và lịch sử quyết định. Có phát hiện số đo thiếu/cũ. Mã: [telemetry.py](../research/telemetry.py), [control_agent.py](../agents/control_agent.py). Chạy thật: [06/10](LLM_FEEDBACK_VALIDATION_20261006.md), [07/10](LLM_FEEDBACK_VALIDATION_20261007.md).
- [x] **R06 — Dùng agent LLM quyết định trạng thái thay vì bảng ngưỡng 75%/85% cố định.** Model nhận telemetry và chọn `light`, `normal`, `heavy`; đã quan sát `light → normal` khi có dữ liệu thật. Các mức RAM và nghỉ 1–2/5 giây người dùng nêu là ví dụ, không phải quy tắc đã đóng cứng vào LLM. [Thiết kế và phạm vi](LLM_FEEDBACK_CONTROL.md), [bằng chứng 07/10](evidence/llm-feedback-20261007.json).
- [x] **R07 — Có dynamic feedback loop rate limiting chạy được.** Telemetry → LLM → quyết định JSON → policy có thời hạn → producer/Kafka → telemetry vòng sau đã chạy thật với 646 record. Rate gate điều chỉnh nhịp phát theo `rate_per_second`. [runtime_policy.py](../agents/runtime_policy.py), [kiểm chứng 07/10](LLM_FEEDBACK_VALIDATION_20261007.md).
- [ ] **R08 — Một phần: LLM tự tăng/giảm nhiều mức tốc độ theo tải.** Cơ chế đã có nhưng mọi quyết định LLM hợp lệ trong hai lượt 07/10 đều chọn **5 record/s**. Việc giảm về 0 do RAM/provider thuộc guard của code, chưa phải bằng chứng LLM tự giảm tốc để giải quyết nghẽn. [Giới hạn thực nghiệm](LLM_FEEDBACK_VALIDATION_20261007.md).
- [ ] **R09 — Một phần: agent biết giới hạn hệ thống và phân biệt rảnh/bình thường/nặng.** Có phân loại LLM, capacity Docker và throughput đã quan sát; đã tạo được quá tải ở cấu hình cụ thể. Chưa xác định công suất tối đa hoặc mức vận hành tối ưu có tính lặp lại. `capacity_evidence()` không coi vài mẫu rảnh là bằng chứng saturation. [control_agent.py](../agents/control_agent.py), [hiệu chuẩn 14/10](LLM_OVERLOAD_VALIDATION_20261014.md).
- [x] **R10 — Có bảo vệ khi thiếu RAM hoặc telemetry/model bị lỗi.** Policy thiếu/sai/hết hạn đưa producer về rate an toàn; thiếu RAM ngăn phát mới và ngăn bắt đầu training, processor tiếp tục xử lý backlog. Đã quan sát thật các lần `emergency_host_memory`. Cơ chế này không tạo thêm RAM và không bảo đảm cả stack vẫn chạy khi footprint nền vượt bộ nhớ. [06/10](LLM_FEEDBACK_VALIDATION_20261006.md), [runtime_policy.py](../agents/runtime_policy.py).

### 1.3. Docker resource constraints và luồng training

- [x] **R11 — Khai báo giới hạn tài nguyên Docker.** Compose có CPU/RAM/swap cho processor và trainer; cấu hình mẫu mỗi processor 1 CPU/768 MiB, trainer 1 CPU/1024 MiB. Đây là giới hạn khai báo, không phải số đo mức tiêu thụ hiện tại. [docker-compose.yml](../docker-compose.yml), [.env.example](../.env.example).
- [x] **R12 — Có công cụ để agent sửa resource limit trực tiếp trong `docker-compose.yml`.** Công cụ kiểm tra ngân sách, áp dụng `docker update`, xác minh runtime rồi lưu YAML; có backup, kiểm tra thay đổi đồng thời và rollback. Ngày 06/10 đã đổi processor 1 CPU/768 MiB → 0,9 CPU/704 MiB và khôi phục; lỗi được chèn để kiểm tra rollback cũng khôi phục thành công. [compose_resources.py](../agents/compose_resources.py), [bằng chứng actuator](LLM_FEEDBACK_VALIDATION_20261006.md).
- [ ] **R13 — Một phần: LLM tự chọn CPU/RAM và chứng minh thay đổi đó có ích.** Công cụ đã chạy thật, nhưng các allocation của bài thử 06/10 do người thử cung cấp. Quyết định LLM 07/10 có `resources={}`. Chưa có bằng chứng LLM tự chọn allocation, áp dụng và cải thiện quá tải. Không có autoscaling số replica hoặc broker. [Phạm vi công cụ](LLM_FEEDBACK_CONTROL.md).
- [x] **R14 — Tách tiến trình training khỏi xử lý stream và cho agent cấp quyền bắt đầu.** Trainer là service riêng, đọc `training_allowed`, có khóa tránh fit đồng thời; Airflow nhường training cho trainer khi control bật. Một fit đang chạy được phép hoàn tất. [auto_train.py](../scripts/auto_train.py), [train_model.py](../modeling/train_model.py), [runtime_policy.py](../agents/runtime_policy.py), [DAG](../airflow/dags/real_estate_pipeline.py).
- [ ] **R15 — Một phần: kiểm chứng training hoàn tất dưới điều tiết mà stream vẫn ổn định.** Đã thấy hoãn training và cấp quyền thật, nhưng các lượt control cố ý không chạy fit để giữ model phục vụ. Tách service vẫn dùng chung host/VM; chưa phải cô lập tài nguyên tuyệt đối. [06/10](LLM_FEEDBACK_VALIDATION_20261006.md), [07/10](LLM_FEEDBACK_VALIDATION_20261007.md).

### 1.4. Model và chuyển dự phòng

- [x] **R16 — Có cấu hình model/API key và router dự phòng.** Hỗ trợ endpoint/model/key chính và dự phòng, có thể tách cấu hình điều tiết khỏi extraction; cooldown và deadline được xử lý trong router. [model_router.py](../agents/model_router.py), [providers.py](../agents/providers.py), [cấu hình cần cung cấp](#6-cấu-hình-và-điều-kiện-cần-có).
- [x] **R17 — Chuyển model khi model chính lỗi dịch vụ.** Ngày 07/10 có hai lần HTTP 503 thật ở Gemini 3.6 Flash, chuyển sang Gemini 3.5 Flash-Lite và nhận HTTP 200; luồng tiếp tục khi lease còn hiệu lực. Đây là tên model trong bằng chứng lịch sử. [Báo cáo và log tham chiếu](LLM_FEEDBACK_VALIDATION_20261007.md).
- [ ] **R18 — Một phần: tự switch khi hết token/quota và có model miễn phí dự phòng.** Router đã xử lý quota/rate limit và có test mô phỏng, nhưng chưa kiểm chứng cạn quota thật. Fallback đã thử dùng cùng key/provider, không tạo quota độc lập; chưa chứng minh một route miễn phí được cấu hình vận hành thường xuyên. Phải có tài khoản, model và key được cấp quyền; không tự tạo key hay mua quota. [test_model_router.py](../utils/tests/test_model_router.py), [cấu hình fallback](LLM_FEEDBACK_CONTROL.md).

### 1.5. Chạy thực tế, đo hiệu quả và tiếp tục công việc dở

- [x] **R19 — Chạy thực tế sau khi bật Docker.** Đã có dữ liệu Kafka/Mongo thật, gọi model thật và thử actuator thật; kết quả được ghi theo từng lượt, gồm cả thất bại. [06/10](LLM_FEEDBACK_VALIDATION_20261006.md), [07/10](LLM_FEEDBACK_VALIDATION_20261007.md), [13/10](LLM_OVERLOAD_VALIDATION_20261013.md), [14/10](LLM_OVERLOAD_VALIDATION_20261014.md), [19/10](LLM_OVERLOAD_VALIDATION_20261019.md).
- [x] **R20 — Có cách đo và bộ thử chứng minh agent đã adjust.** Đã xây harness baseline/LLM, hiệu chuẩn quá tải, workload cùng seed, tách nhu cầu trước gate khỏi lượng được nhận, lưu quyết định/ingress/commit/lag/latency/lỗi và trạng thái khôi phục. [llm_experiment.py](../research/llm_experiment.py), [llm_report.py](../research/llm_report.py), [tiêu chí đối chứng](LLM_OVERLOAD_EXPERIMENT.md).
- [ ] **R21 — Chưa hoàn thành: chứng minh LLM giữ hệ thống ổn định khi quá tải.** Các lượt đối chứng đến 19/10 chưa hoàn thành cặp hợp lệ; không có quyết định LLM trong các nhánh overload đã lưu. Lượt 14/10 đã tạo quá tải thật, nhưng chưa chạy được nhánh LLM tương ứng. Không dùng kết quả controller theo luật, forecast hoặc guard để đánh dấu yêu cầu này hoàn tất. [14/10](LLM_OVERLOAD_VALIDATION_20261014.md), [19/10](LLM_OVERLOAD_VALIDATION_20261019.md).
- [x] **R22 — Rà lại điểm dừng, hoàn tất các bản sửa không cần thêm RAM và tránh chạy lặp.** Đã sửa tranh chấp policy file Windows, timeout observer, đọc lịch sử anomaly theo luồng và ghi hash dependency thực sự trong worker; đã lưu các lượt lỗi và điểm tiếp tục. Chi tiết commit/tests ở [mục 7](#7-các-bản-sửa-đã-hoàn-thành).
- [ ] **R23 — Một phần: kiểm chứng bản sửa giảm RAM trên Docker.** Mã và test đã hoàn tất; phiên 23/10 vẫn dùng cùng container/image identity như trước bản sửa và thiếu hash dependency cần thiết. Phải xác minh/recreate worker trước khi nhận kết quả mới là kiểm chứng bản sửa. Chưa có số đo giảm RAM thực tế. [Provenance và giới hạn](TELEMETRY_STATUS_20261024.md).
- [x] **R24 — Phân biệt RAM máy, RAM Docker và RAM container khi kiểm tra điều kiện chạy.** Đã bổ sung/đọc riêng các lớp; không suy ra máy còn RAM từ một chỉ số container thấp. Các lần đo được lưu theo ngày; việc dùng RAMMap hoặc giải phóng RAM không tự chứng minh đủ bộ nhớ suốt lượt thử. [Định nghĩa số đo](#5-telemetry-đã-thu-được-gì-và-còn-thiếu-gì).

### 1.6. Thu telemetry và các mục nghiên cứu từ báo cáo trước

- [x] **R25 — Kiểm kê dữ liệu telemetry hiện có và đánh giá có đủ dùng không.** Đã audit lịch sử, loại phiên chưa kết thúc khỏi export và benchmark 1.732 observations của 7 phiên đã kết thúc. Đủ để đánh giá bước đầu trên workload tổng hợp, chưa đủ chứng minh hoạt động dài hạn hoặc điều tiết LLM. [Kiểm kê 23/10](TELEMETRY_STATUS_20261023.md), [kết quả 24/10](TELEMETRY_STATUS_20261024.md).
- [ ] **R26 — Chưa hoàn thành: thu thêm khoảng hai giờ.** Lượt 09/10 dừng sau 58m43s; phiên bắt đầu 23/10 giờ Việt Nam chỉ có khoảng 27 phút quan sát. Kế hoạch hai giờ ngày 24/10 vẫn `prepared_not_started`; không cộng thời lượng kế hoạch thành dữ liệu thật. [Bằng chứng phiên và kế hoạch](evidence/telemetry-status-20261024.json).
- [x] **H01 — Kiểm định dữ liệu và đối chiếu Gemini input/output.** Đã có audit skew/missingness/outlier và một record tổng hợp qua Gemini thật → parse → merge → MongoDB. Chưa xác minh chất lượng extraction toàn dataset. [Audit dữ liệu](LECTURER_RESEARCH_REPORT.md), [phụ lục Gemini](#phụ-lục-a--bằng-chứng-gemini-ngày-24092026).
- [x] **H02 — Benchmark các mô hình giá trên cùng tập chia.** Đã so VotingRegressor, Random Forest, Gradient Boosting và XGBoost; chỉ là chẩn đoán holdout, chưa thay model production và chưa xử lý hết leakage. [Kết quả định lượng](#4-kết-quả-định-lượng-có-thể-báo-cáo), [benchmark.py](../research/benchmark.py).
- [x] **H03 — Đo tải ba broker, thời gian phản ứng, trước/sau và tạo biểu đồ.** Có paired-v4 bằng controller theo luật, log T0–T3 và biểu đồ từ dữ liệu thật. Ingress gần đều không đồng nghĩa CPU cân bằng; recovery đo được xảy ra trong drain. [Báo cáo nghiên cứu cũ](LECTURER_RESEARCH_REPORT.md).
- [x] **H04 — Dự báo traffic trước 5 phút ở mức thử nghiệm offline.** XGBoost được chọn bằng validation, test R² 0,699 và RMSE thấp hơn persistence 68,99%; đã export artifact local. Target là `requested_rate` tổng hợp. [Kết quả 24/10](TELEMETRY_STATUS_20261024.md).
- [ ] **H05 — Chưa đạt: dự báo trước 10 phút.** Gradient Boosting có test R² −0,084 nên không đủ điều kiện export. [Metrics và biểu đồ](evidence/telemetry-status-20261024.json).
- [ ] **H06 — Chưa hoàn thành: nối dự báo vào quyết định điều tiết và đo lợi ích.** Có [TrafficForecaster](../research/forecast.py) để inference có kiểm tra artifact/history; runner chưa dùng đầu ra đó để quyết định admission. Forecast tốt không tự chứng minh control tốt.
- [ ] **H07 — Các hướng LSTM, Q-Learning/DQN và clustering chưa triển khai.** Đây là hướng từng được đánh giá trong báo cáo cũ, không phải tính năng đã hoàn thành hay điều kiện bắt buộc cho vòng LLM hiện tại. LLM gán `light/normal/heavy` không phải mô hình clustering học từ telemetry. [Phạm vi nghiên cứu](../research/README.md).

## 2. Hệ thống hiện có những khả năng gì?

| Khả năng | Đã có trong hệ thống | Bằng chứng / giới hạn |
| --- | --- | --- |
| Thu thập dữ liệu | Adapter Alonhadat/Homedy, schema thống nhất, checkpoint, timeout và pacing; crawling bật chủ động | [CRAWL_STABILITY](CRAWL_STABILITY.md), [validation 22/09 — Local](../runtime/crawl_review/validation.json): Alonhadat 20 ACK, Homedy 1 ACK trong lượt đã lưu. Homedy còn lỗi phản hồi quá lớn; Guland mới có parser offline |
| Xử lý stream | Kafka 3 broker, topic partition/replication; 3 processor cùng consumer group → MongoDB | [ARCHITECTURE](ARCHITECTURE.md), [kafka_to_mongo.py](../processing/kafka_to_mongo.py). Cùng một host, không phải HA nhiều máy |
| Chuẩn hóa và kiểm định | Parse giá/diện tích, validate, enrich feature, IQR anomaly, lưu raw/invalid/DLQ | [DATA_SCHEMA](DATA_SCHEMA.md), [PRICE_ANOMALY_DETECTION](PRICE_ANOMALY_DETECTION.md). Quy tắc/schema không chứng minh thông tin rao bán là sự thật |
| Extraction agent | Async request/result qua Kafka, gọi API LLM, schema/evidence validation, merge field thiếu, retry/cache/audit | [extraction.py](../agents/extraction.py), [worker.py](../agents/worker.py), [results.py](../agents/results.py); một fixture thật đã kiểm chứng 24/09 |
| Stress agent | Sinh dữ liệu tổng hợp có seed, profile và giới hạn; lưu manifest/ground truth/report | [stress.py](../agents/stress.py), [generator.py](../agents/generator.py); [run 09/09 — Local](../runtime/stress/final-verification-20260909/report.json): 12 sinh/12 ACK, lỗi 0. Bộ sinh này không phải LLM điều tiết |
| Cô lập dữ liệu thử | Topic `real_estate_stress_raw`, DB `real_estate_stress_db`, cờ synthetic và loại khỏi các đường train/export chính | [safety.py](../agents/safety.py), [test_synthetic_safety.py](../utils/tests/test_synthetic_safety.py). Vẫn chia sẻ CPU/RAM/broker với luồng thật |
| Huấn luyện và phục vụ giá | Trainer riêng; VotingRegressor gồm Ridge, HistGradientBoosting và SGD, target log1p/expm1, lưu model/metadata | [price_model.py](../modeling/price_model.py), [ML_PIPELINE_AUDIT](ML_PIPELINE_AUDIT.md). Giá rao bán, chưa phải giá giao dịch đã xác minh |
| API và giao diện | FastAPI đăng ký/đăng nhập/phân quyền, predict/batch/model info; React qua Nginx; health/ready | [README API](../README.md#api), [frontend](../frontend/README.md), [PROJECT_STATUS](PROJECT_STATUS.md). Có kiểm chứng health/serving lịch sử; chưa có bằng chứng đầy đủ hành trình trình duyệt đăng nhập → dự đoán |
| Lịch chạy | Loop scraper/trainer; tích hợp Airflow tùy chọn, có tránh nhiều scheduler cùng chạy | [AUTOMATION](AUTOMATION.md), [Airflow](../airflow/README.md). Airflow có mã/test; chưa tìm thấy artifact nghiệm thu live đầy đủ |
| Quan sát | Prometheus metrics và Grafana Pipeline/Agent Operations/Source Ingestion; telemetry nghiên cứu có log theo phiên | [monitoring](../monitoring/README.md), [telemetry.py](../research/telemetry.py). Không suy ra đã có distributed tracing hoặc mọi alert đã gửi thành công |
| Điều tiết theo luật | Threshold/hysteresis/cooldown/token bucket trong nghiên cứu cũ | [traffic_control.py](../agents/traffic_control.py), [paired-v4](../runtime/research/paired-v4-20260921/analysis/summary.json). Kết quả này không thuộc LLM |
| Điều tiết bằng LLM | Đọc số đo/lịch sử, chọn trạng thái, rate, quyền training và resource action trong giới hạn | [control_agent.py](../agents/control_agent.py). Chức năng đã chạy tải nhỏ, hiệu quả quá tải còn chưa chứng minh |
| Điều chỉnh Docker/Compose | Công cụ LLM cập nhật CPU/RAM/swap cho 3 processor và trainer, xác minh và rollback | [compose_resources.py](../agents/compose_resources.py). `resource_actuator.py` của nghiên cứu cũ là công cụ CPU riêng; không nhầm hai cơ chế |
| Thu lịch sử và đánh giá forecast | Session start/status/stop/export, chế độ lean, audit dữ liệu, chronological benchmark và inference thử nghiệm | [collect_session.py](../research/collect_session.py), [TRAFFIC_SESSIONS](TRAFFIC_SESSIONS.md), [forecast.py](../research/forecast.py). Chưa nối predictive control |

## 3. Ba loại bằng chứng điều tiết phải phân biệt

| Loại | Đã chứng minh | Chưa được suy ra |
| --- | --- | --- |
| Controller theo luật, paired-v4 21/09 | Có đổi rate và giảm peak lag/latency bằng cách từ chối bớt nhu cầu | LLM đã điều tiết; throughput tăng; tài nguyên tự scale hiệu quả |
| LLM feedback 07/10 | Model nhận số đo thật, cấp lease 5/s, dữ liệu đi qua Kafka/Mongo; fallback 503 thành công | Model chọn nhiều rate theo tải, tự đổi CPU/RAM hoặc ổn định lúc quá tải |
| Forecast offline 24/10 | Dự báo requested demand 5 phút vượt persistence theo tiêu chí thử nghiệm | Nhu cầu tự nhiên dài hạn; forecast đã dùng trong control; lợi ích nhân quả của LLM |

LLM quyết định trong phạm vi công cụ; schema, trần tốc độ, ngân sách tài nguyên,
lease và floor bộ nhớ vẫn do code kiểm tra. Guard là cơ chế bảo vệ cần thiết,
không được tính là quyết định của model. Model không có quyền chạy shell tùy ý.

## 4. Kết quả định lượng có thể báo cáo

### 4.1. LLM, Docker và các lượt quá tải

| Ngày / lượt | Số đo thực tế | Kết luận đúng phạm vi |
| --- | --- | --- |
| 24/09 — extraction | 1 fixture; HTTP 503, 503, rồi 200; bổ sung giá 8 tỷ, diện tích 80 m², 2 phòng ngủ, 2 phòng tắm | Gemini thật đã parse/merge/lưu thành công record này; không phải đánh giá toàn dataset |
| 06/10 — resource tool | 1 CPU/768 MiB → 0,9 CPU/704 MiB, Docker và YAML đồng nhất; restore và rollback thành công | Công cụ điều chỉnh tài nguyên hoạt động; allocation không do LLM chọn |
| 07/10 — vòng LLM | 245 + 401 = **646 record**; giao nhận lỗi 0; ingress ổn định khoảng 4,80–4,94/s; lag 0, p95 khoảng 49 ms | Vòng phản hồi hoạt động ở tải nhỏ; có giai đoạn khởi động chậm, không gộp vào chỉ số ổn định |
| 07/10 — fallback | 2 lần 503 model chính → 200 model dự phòng; `light → normal`, rate giữ 5/s | Failover dịch vụ thật; chưa kiểm chứng cạn quota hoặc nhiều mức tốc độ |
| 14/10-02 — hiệu chuẩn/baseline | Mức 600/s gây quá tải; 44.996 offered, 44.927 ACK/observed commit, 69 rejected; đỉnh lag **10.411** | Dừng bởi `hard_lag_budget`, **không phải RAM**; đã tạo quá tải thật nhưng chưa có nhánh LLM đối chứng |
| 14/10-03 | 10.357 offered/ACK/observed commit; lỗi observer khiến phép thử invalid | Xử lý hết cuối lượt không sửa được tính hợp lệ của phép đo |
| 19/10 | 68 offered/ACK/commit ở pha nhẹ; guard RAM host; chưa đến mức 500/s và chưa gọi LLM | Không có bằng chứng mới về hiệu quả điều tiết quá tải; 7 dịch vụ phụ được khôi phục |

Nguồn Git: [Gemini — phụ lục A](#phụ-lục-a--bằng-chứng-gemini-ngày-24092026),
[06/10](LLM_FEEDBACK_VALIDATION_20261006.md), [07/10](LLM_FEEDBACK_VALIDATION_20261007.md),
[14/10](LLM_OVERLOAD_VALIDATION_20261014.md), [19/10](LLM_OVERLOAD_VALIDATION_20261019.md).

### 4.2. Kết quả controller theo luật trong báo cáo cũ

| Chỉ số paired-v4, 21/09 | Baseline | Adaptive theo luật |
| --- | ---: | ---: |
| Mean committed throughput | 214,172 msg/s | 184,958 msg/s |
| Peak Kafka lag | 11.318 | 2.843 |
| Peak p95 latency | 28,456 s | 9,750 s |
| Rejected messages | 154 | 35.653 |
| Requested plateau cao nhất đạt tiêu chí đã đặt | 500 msg/s | 250 msg/s |

Peak lag giảm 74,88%, peak latency giảm 65,74%, nhưng throughput trung bình không
tăng. Leader ingress gần 33% mỗi broker; CPU broker 2 cao hơn rõ rệt. Thời gian
ra quyết định sau khi sample hoàn tất khoảng 0,986 ms, xác nhận gate khoảng
0,498 ms; từ timestamp Kafka đến quyết định khoảng 2,111 s. Recovery khoảng
80,017 s xảy ra trong **drain**, chưa chứng minh phục hồi khi nhu cầu cao còn tiếp tục.

[Báo cáo đầy đủ](LECTURER_RESEARCH_REPORT.md) ·
[summary.json — Local](../runtime/research/paired-v4-20260921/analysis/summary.json) ·
[biểu đồ rate — Local](../runtime/research/paired-v4-20260921/analysis/rate_limit.png).

### 4.3. Benchmark dự đoán giá và audit dữ liệu cũ

Cùng 3.984 train / 996 test, seed 42 và preprocessing/log target:

| Model giá | R² | MAE, tỷ VND | RMSE, tỷ VND |
| --- | ---: | ---: | ---: |
| VotingRegressor hiện có | 0,488991 | 4,616007 | 14,192591 |
| Random Forest | 0,510214 | 4,583648 | 13,894742 |
| Gradient Boosting | 0,446412 | 4,910597 | 14,772045 |
| XGBoost | 0,592611 | 4,261827 | 12,672202 |

Dữ liệu có skew giá 7,021, diện tích 9,871; thiếu bedrooms 52,44%, bathrooms
54,79%, latitude/longitude 100% trong snapshot đã audit. Có 687 giá trị giá
thuộc nhóm IQR outlier; outlier thống kê không tự đồng nghĩa dữ liệu sai.
Text chứa giá và duplicate giữa train/test còn gây rủi ro leakage. Các điểm số
trên chưa đủ để thay model phục vụ hoặc tuyên bố tổng quát hóa tốt.

[Audit và phân phối](LECTURER_RESEARCH_REPORT.md) ·
[audit.json — Local](../runtime/research/data-audit/audit.json) ·
[metrics.json — Local](../runtime/research/legacy-benchmark/metrics.json) ·
[biểu đồ benchmark — Local](../runtime/research/legacy-benchmark/algorithm_comparison.png).

## 5. Telemetry đã thu được gì và còn thiếu gì?

### 5.1. Số lượng, chất lượng và thời lượng

- Export dùng đánh giá 24/10 gồm **1.732 observations / 7 phiên đã kết thúc**. Phiên 01/10 có metadata `running` cũ, chưa hoàn tất audit nên bị loại; trạng thái file này không chứng minh còn tiến trình đang chạy.
- Phiên bắt đầu 23/10 giờ Việt Nam phát tải **1.602,453 giây**, span quan sát **1.619,140 giây**: 327 mẫu, 322 mẫu target khỏe, 3 mẫu lỗi thu thập; đoạn khỏe liên tục dài nhất khoảng 20m54s.
- Phiên này có 49.064 offered, 48.094 ACK, 970 rejected, delivery failures 0 và final lag 0; dừng vì VM available **8,23%**. Đây là fixed baseline, không gọi LLM điều tiết.
- Hai lịch sử passive cũ có 5.209 mẫu nhưng mọi giá trị ingress khỏe đều bằng 0; không coi số lượng đó là bằng chứng dự báo tải biến thiên.
- Kế hoạch 24/10 dài 7.200 giây, 120 stage, rate 5–20/s, dự kiến 85.961 record vẫn **chưa chạy**. Những phiên ngắn cộng lại không thay thế xác nhận đã hoàn thành lượt hai giờ yêu cầu.

[Kiểm kê 23/10](TELEMETRY_STATUS_20261023.md) ·
[Đánh giá 24/10](TELEMETRY_STATUS_20261024.md) ·
[JSON có hash nguồn](evidence/telemetry-status-20261024.json).

### 5.2. Forecast từ dữ liệu đã có

Benchmark dùng chia theo thời gian, loại nhãn tương lai vượt ranh giới split,
không nối qua run/gap/mẫu lỗi. Model được chọn bằng validation rồi giữ nguyên
để đo test. Target là **nhu cầu tổng hợp trước admission (`requested_rate`)**,
không phải CPU/RAM hay nhu cầu crawl tự nhiên.

| Horizon | Model đã chọn | Test RMSE / persistence, record/s | Test R² | Kết quả |
| --- | --- | ---: | ---: | --- |
| 5 phút | XGBoost | 8,588 / 27,697 | 0,699 | Đạt; artifact inference thử nghiệm đã export local |
| 10 phút | Gradient Boosting | 16,813 / 27,894 | −0,084 | Chưa đạt; không export |

Điều kiện export: cải thiện RMSE ít nhất 5% so với persistence và R² dương trên
cả validation lẫn test. Persistence dự báo rate giữ nguyên. Không đổi sang model
khác chỉ vì thấy điểm test đẹp hơn. Hai biểu đồ đã lưu Git:
[5 phút](evidence/telemetry-forecast-300s-20261024.png),
[10 phút](evidence/telemetry-forecast-600s-20261024.png).

### 5.3. Ý nghĩa các chỉ số và RAM

| Lớp / chỉ số | Đo gì | Điều không được suy ra |
| --- | --- | --- |
| RAM host Windows | `psutil.virtual_memory()`, bộ nhớ khả dụng toàn máy | VM còn trống không bảo đảm host đủ RAM |
| RAM Docker VM | `/proc/meminfo`, available/free/swap toàn VM Linux | Khác với tổng memory các container được chọn |
| RAM container / `ram_percent` tổng hợp | Từng usage/limit; tổng nhóm theo dõi chia RAM Docker engine | Không phải phần trăm RAM Windows |
| CPU | Docker core-percent từng container; số tổng hợp chuẩn hóa theo capacity engine | 100% từng container có thể chỉ là một core, không phải toàn máy |
| `offered`, `admitted`, `acknowledged`, `committed` | Nhu cầu phát sinh → qua gate → Kafka nhận → observer thấy consumer commit | ACK chưa phải xử lý xong; commit có thể thuộc nhánh invalid/DLQ, cần đọc outcome/error |
| Lag / latency | Chênh offset consumer; histogram theo khoảng và offered→observed commit có độ phân giải polling | Không lấy trung bình các p95 khoảng thành p95 toàn lượt; idle không có latency không chứng minh recovery |

Lần kiểm tra cuối đã lưu ngày 24/10 lúc 18:36: host còn 4,959 GiB (31,56%),
Docker engine chưa hoạt động. Một lần khởi động trước đó làm RAM khả dụng xuống
0,01 GiB (0,09%), sau đó đã tắt Docker trả về trạng thái ban đầu. Đây là bằng
chứng **ngày 24/10**, không khẳng định trạng thái máy ngày 26/10.

## 6. Cấu hình và điều kiện cần có

| Phần | Cần cung cấp / chuẩn bị | Đã có và còn thiếu |
| --- | --- | --- |
| Model chính | `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`; model có chat completions trả JSON | Đã dùng credential để kiểm chứng model thật; báo cáo không chép `.env`/key hoặc cam kết quyền truy cập hiện tại |
| Model điều tiết riêng | Nhóm `CONTROL_LLM_*`; để trống thì kế thừa nhóm chính theo router | Có cơ chế; khai báo trọn nhóm khi dùng endpoint/tài khoản khác |
| Model dự phòng | `LLM_FALLBACK_*` hoặc `CONTROL_LLM_FALLBACK_*`, key và quyền dùng model tương ứng | Fallback cùng provider đã thử; route miễn phí/quota độc lập chưa được xác nhận |
| Producer/trainer nhận policy | `CONTROL_ENABLED`, source topic, policy path/mount và lease thống nhất | Đã chạy tải nhỏ; một topic được điều tiết mỗi lần, không tự bật crawler hoặc tạo workload |
| Điều chỉnh tài nguyên | `CONTROL_RESOURCE_ENABLED`, Docker CLI trên host, quota gốc khôi phục được, ngân sách CPU/RAM | Công cụ chỉ cho 3 processor và trainer; không sửa tùy ý service hoặc replica |
| Thực nghiệm dài / quá tải | Kafka/Mongo/worker chạy, đủ RAM host lẫn VM, telemetry hợp lệ, publisher/scheduler được cô lập | Chưa có lượt mới đạt toàn bộ; hạ rate không xóa footprint Mongo/Kafka và lịch sử xử lý |

Cấu hình tham chiếu: [.env.example](../.env.example),
[LLM_FEEDBACK_CONTROL](LLM_FEEDBACK_CONTROL.md),
[LLM_OVERLOAD_EXPERIMENT](LLM_OVERLOAD_EXPERIMENT.md).
“Miễn phí” là điều kiện của tài khoản/provider; router không bảo đảm model luôn
miễn phí, không tự mở billing và không biến cùng một key thành quota độc lập.

## 7. Các bản sửa đã hoàn thành

| Bản sửa | Commit | Kiểm chứng và phần còn lại |
| --- | --- | --- |
| Parser Docker nhận số dạng `1e+03kB`, giữ đúng đơn vị và từ chối số sai | `f02563d` | 72 test telemetry/controller đạt; lấy lại 3 mẫu telemetry thật không lỗi parser. [06/10](LLM_FEEDBACK_VALIDATION_20261006.md) |
| Retry có giới hạn khi Windows tranh chấp file policy | `e972055` | [runtime_policy.py](../agents/runtime_policy.py), [test_runtime_policy.py](../utils/tests/test_runtime_policy.py), [14/10](LLM_OVERLOAD_VALIDATION_20261014.md) |
| Observer commit: timeout request 5 s độc lập chu kỳ poll, lưu lỗi hợp lệ | `e5c5664` | 47 test liên quan đạt theo lượt sửa; lỗi observer vẫn làm phép thử invalid. [llm_load.py](../research/llm_load.py), [test_llm_load.py](../utils/tests/test_llm_load.py) |
| Đọc lịch sử price anomaly một lượt, tránh giữ toàn bộ document | `acb8803` | 23 test anomaly/enrichment đạt; vẫn giữ percentile chính xác và đóng cursor. [price_anomaly.py](../processing/price_anomaly.py), [test_price_anomaly.py](../utils/tests/test_price_anomaly.py). Chưa đo giảm RAM Docker |
| Ghi hash dependency thực sự đang chạy trong worker | `6bc14df` | 22 test đạt; một Docker call/worker, giữ field cũ và báo lỗi rõ nếu không đọc được. [run_experiment.py](../research/run_experiment.py), [test_worker_identity.py](../utils/tests/test_worker_identity.py) |

Các số test là kết quả đã ghi tại lần sửa, không phải tuyên bố vừa chạy lại toàn
bộ suite ngày 26/10. Phần tích hợp giới hạn cache MongoDB viết dở đã được gỡ;
không báo cáo thành tính năng đã triển khai. Việc có image mới hoặc checkout mới
không chứng minh container đang chạy mã mới; cần đối chiếu dependency hashes.

## 8. Checklist còn lại và điều kiện đánh dấu hoàn thành

- [ ] **Triển khai và xác minh bản sửa worker:** container dùng đúng image/mã, hash `price_anomaly.py` và `feature_engineering.py` khớp bản cần thử; đo RAM thực tế trước khi kết luận giảm bộ nhớ.
- [ ] **Thu đủ lượt telemetry bổ sung:** session thực sự hoàn tất thời lượng đặt, có final status, audit chất lượng, số mẫu/đoạn khỏe và khôi phục dịch vụ; không coi plan hoặc file `running` cũ là đã chạy xong.
- [ ] **Chứng minh điều tiết LLM quá tải:** ít nhất 3 cặp hợp lệ, có cả thứ tự baseline→LLM và LLM→baseline, cùng seed/workload/allocation/training. Model phải thật sự giảm rate; ingress/lag/latency phục hồi với throughput dương **khi offered demand vẫn cao**; báo cáo cả lỗi, deferred và rejected. Guard/drain không được nhận công cho LLM.
- [ ] **Chứng minh LLM tự đổi allocation:** có quyết định model, receipt Docker+Compose và phép đo hiệu quả; đây là thử nghiệm riêng vì bài đối chứng rate hiện cố định resource limits.
- [ ] **Chạy training dưới điều tiết:** kiểm chứng hoãn/cho phép và fit hoàn tất, theo dõi stream trong lúc fit; đo chi phí tài nguyên thay vì chỉ quan sát `training_allowed=true`.
- [ ] **Hoàn tất fallback quota/vận hành:** cấu hình route được tài khoản cho phép; xác nhận khả năng chuyển khi quota hết, giới hạn chi phí và giới hạn quota dùng chung. Test mô phỏng chỉ xác minh logic; chỉ ghi nhận failover quota thực tế khi có lỗi quota và response fallback thật. Không cần cố tình tiêu hết quota để thử.
- [ ] **Mở rộng forecast:** dữ liệu độc lập/đa dạng hơn, đánh giá lại 5/10 phút; chỉ nối vào controller và kết luận predictive control sau thử nghiệm riêng.
- [ ] **Nếu tiếp tục mục tiêu chất lượng giá/extraction:** mẫu gán nhãn đại diện, xử lý leakage/duplicate và chia dữ liệu phù hợp; một fixture Gemini và holdout cũ chưa đủ.

## 9. Mở bằng chứng ở đâu?

| Nội dung | Bằng chứng đọc được trên Git | Raw artifact trên máy chạy, ngoài Git |
| --- | --- | --- |
| Baseline an toàn | Commit `c51b23a`, `a2c5fbb` trên `main` | Không cần runtime |
| Gemini extraction thật | Phụ lục A dưới đây | [gemini-clean-audit.json](../runtime/research/gemini-clean-audit.json), [validation-success.json](../runtime/research/gemini-verification-runtime/validation-success.json) |
| Audit/benchmark giá | [LECTURER_RESEARCH_REPORT](LECTURER_RESEARCH_REPORT.md) | [data audit](../runtime/research/data-audit/audit.json), [legacy metrics](../runtime/research/legacy-benchmark/metrics.json) |
| Controller theo luật | [LECTURER_RESEARCH_REPORT](LECTURER_RESEARCH_REPORT.md) | [paired-v4 summary](../runtime/research/paired-v4-20260921/analysis/summary.json), observations/actions/biểu đồ trong cùng thư mục |
| Docker apply/rollback | [06/10](LLM_FEEDBACK_VALIDATION_20261006.md) | [resource.apply.json](../runtime/control-validation/20261006-01/resource.apply.json), [rollback.json](../runtime/control-validation/20261006-01/rollback.json) |
| LLM và fallback thật | [07/10](LLM_FEEDBACK_VALIDATION_20261007.md), [JSON tổng hợp](evidence/llm-feedback-20261007.json) | [summary lượt 02](../runtime/control-validation/20261007-02/summary.json), [summary lượt 03](../runtime/control-validation/20261007-03/summary.json), provider/live logs cùng thư mục |
| Các lần overload chưa hoàn thành | [13/10](LLM_OVERLOAD_VALIDATION_20261013.md), [14/10](LLM_OVERLOAD_VALIDATION_20261014.md), [19/10](LLM_OVERLOAD_VALIDATION_20261019.md), JSON tương ứng trong [evidence](evidence/) | [calibration 14/10-02](../runtime/research/llm-proof-20261014-02/calibration.json), [session 19/10](../runtime/research/llm-proof-20261019-01/session.json) |
| Kiểm kê telemetry | [23/10](TELEMETRY_STATUS_20261023.md), [24/10](TELEMETRY_STATUS_20261024.md), [JSON/hash](evidence/telemetry-status-20261024.json) | [session 23/10](../runtime/research/traffic-sessions/session-20261022T233647.626631Z-8f23060b/session.json), [history audit](../runtime/research/traffic-sessions/session-20261022T233647.626631Z-8f23060b/history_audit.json) |
| Forecast 5/10 phút | [24/10](TELEMETRY_STATUS_20261024.md), hai PNG trong `docs/evidence/` | [metrics](../runtime/research/telemetry-evaluation-20261024/forecast/metrics.json), [input manifest](../runtime/research/telemetry-evaluation-20261024/observations.manifest.json), [model 5 phút](../runtime/research/telemetry-evaluation-20261024/forecast/horizon_300s/forecast.joblib) |
| Lượt hai giờ chưa chạy | [Trạng thái 24/10](TELEMETRY_STATUS_20261024.md) | [plan.json](../runtime/research/telemetry-preparation-20261024/plan.json), [launch.json](../runtime/research/telemetry-preparation-20261024/launch.json) |

Các báo cáo theo ngày được giữ nguyên như hồ sơ lịch sử. Nếu kết luận cũ như
“chưa có vòng LLM” hoặc “chưa đánh giá được forecast” khác checklist này, đối
chiếu ngày và phạm vi thí nghiệm. Không công bố key, header xác thực hoặc dữ liệu
nguồn riêng tư khi chia sẻ raw artifact. Checklist này không gộp các bằng chứng
có phạm vi khác nhau thành tuyên bố toàn hệ thống đã hoàn tất.

## Phụ lục A — Bằng chứng Gemini ngày 24/09/2026

Phần dưới giữ chi tiết INPUT/OUTPUT và quy trình từ báo cáo cũ. Tên model,
trạng thái cấu hình sau khôi phục và số test là **snapshot ngày 24/09**, không
phải cấu hình/runtime hiện tại. Xem checklist R16–R18 và bằng chứng 07/10 cho
phần model router phát triển sau đó.

**Đã xác minh thành công một record qua Kafka → ai-agent → Google Gemini →
parsed extraction → MongoDB ngày 24/09/2026.** Model thực sự trả kết quả là
`gemini-3.6-flash`. Đây là một input tổng hợp không chứa thông tin cá nhân,
được gửi tới provider thật; kết quả không được mock.

#### Từ lỗi model cũ đến lần gọi thành công

Lượt trước với `gemini-2.5-flash`, event
`aaec37e79debbcdece5b940ba080fb888aab4e1977b28f6fd0e71c652f97d637`,
trả HTTP `404 NOT_FOUND`: provider thông báo model không còn khả dụng cho
người dùng mới và đề cập `models/gemini-3.6-flash`. Bằng chứng lỗi được giữ
riêng tại [gemini-clean-audit-model-404.json](../runtime/research/gemini-clean-audit-model-404.json);
lượt đó không có parsed extraction hay cleaning thành công. Lần thăm dò đầu
`67660e15dc29203e2412906657c5c094d1e77f350dbed4f8915c985cfea1405d`
cũng trả 404, lưu tại `runtime/research/gemini-clean-audit-attempt1.json`.

Ngày 24/09, dùng credential đang có trong container để gọi API model listing,
không đoán tên model:

- `GET https://generativelanguage.googleapis.com/v1beta/openai/models`
  trả HTTP 200, có ID `models/gemini-3.6-flash`.
- `GET https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash`
  trả HTTP 200, version `3.6-flash-07-2026`, hỗ trợ `generateContent`.
- Request extraction dùng tên `gemini-3.6-flash`; response thành công cũng ghi
  đúng model này. Việc model có trong listing chưa tự chứng minh gọi được:
  bằng chứng quyết định là HTTP 200 và extraction dưới đây.

Hai kết quả discovery được lưu tại
[model-discovery.json](../runtime/research/gemini-verification-runtime/model-discovery.json)
và [model-selected.json](../runtime/research/gemini-verification-runtime/model-selected.json),
không chứa authentication header hoặc API key.

#### Tích hợp và điều kiện fallback đã kiểm tra từ code

```text
real_estate_raw (production) / real_estate_stress_raw (kiểm thử)
  -> processing/kafka_to_mongo.py: process_payload -> _enqueue_ai
  -> real_estate_ai_input
  -> agents/worker.py: run -> handle_message -> handle
  -> agents/extraction.py: ExtractionService.extract (tạo messages)
  -> agents/providers.py: OpenAICompatibleProvider.extract (HTTP POST)
  -> ExtractionOutput.model_validate_json -> merge_extraction
  -> real_estate_ai_results
  -> agents/results.py: handle_ai_result -> processor validation
  -> training_features hoặc invalid_records trong DB tương ứng
```

Worker dùng consumer group `real_estate_ai_extraction`; processor xử lý kết quả
trong group `real_estate_training_pipeline`. Kết quả lỗi được lưu thêm trong
`ai_failures` và gửi tới `real_estate_ai_dlq`.

Record được chuyển sang AI khi `extraction_routing_errors` phát hiện giá/diện
tích không hợp lệ hoặc thiếu loại bất động sản, tỉnh hay quận. Đồng thời phải
có URL và source text, không có lỗi identity/normalization cản trở, giá không
phải thỏa thuận, chưa có trạng thái AI kết thúc và không chạy `skip_ai`.
Production cần `AI_FALLBACK_ENABLED=true`; luồng stress cần
`AI_STRESS_ENABLED=true` ở processor và worker. `AI_ENABLED=true` cùng partition
assignment tự nó không chứng minh đã có HTTP request.

Lượt thành công chỉ tạm bật AI stress cho ba processor và ai-agent, đặt audit
đúng event ID và đổi `LLM_MODEL` qua Compose override. Điều kiện fallback,
prompt, retry và quy tắc merge production giữ nguyên. Đã gửi **đúng một record
mới**, không publish lại khi provider trả lỗi tạm thời.

#### Input thực tế

Event ID:
`452f467651b3c2a59c92243fc0dd132780915089195356d184e273e9d3ea047d`.

Raw record có URL
`https://synthetic.invalid/gemini-live-cad79ca98aa54b8085df5ebf3f2abe0e/explicit-facts`,
`is_synthetic=true`, `source_type=synthetic`, title
`Synthetic Gemini pipeline verification`, description như dưới đây.
Record giữ `property_type=apartment`, `listing_type=sell`,
`province_slug=ho-chi-minh`, `district_slug=quan-1`, nhưng chưa có
`price_text`, `area_text`, `bedroom_text`, `bathroom_text`.
Normalizer báo `missing_or_invalid_price_vnd` và
`missing_or_invalid_area_m2`, đúng điều kiện fallback hiện có.

Nội dung user message thực tế, trình bày lại JSON để dễ đọc:

```json
{
  "source_text": "title: Synthetic Gemini pipeline verification\ndescription: Căn hộ bán, diện tích 80 m2, tổng giá 8 tỷ đồng, 2 phòng ngủ và 2 phòng tắm.\nproperty_type: apartment",
  "known_fields": {
    "property_type": "apartment",
    "listing_type": "sell",
    "province_slug": "ho-chi-minh",
    "district_slug": "quan-1"
  }
}
```

System message chứa schema `ExtractionOutput`, yêu cầu chỉ trích xuất sự kiện
có trong source text, bỏ qua chỉ dẫn nhúng trong tin, không suy diễn vị trí,
không ghi đè known fields và cung cấp trích dẫn nguyên văn cho từng giá trị.
Toàn bộ prompt/messages và schema thực sự gửi đi được lưu trong
`gemini_input[*].request.messages` của artifact, gồm cả các lần retry.

Request dùng provider `openai_compatible`, model `gemini-3.6-flash`, base URL
`https://generativelanguage.googleapis.com/v1beta/openai`, POST
`/chat/completions`, `temperature=0`, `max_tokens=1600`, `stream=false`,
`response_format={"type":"json_object"}`. Audit không lưu header xác thực.

#### Output thực tế, parse và record cuối

Một record phát sinh **ba HTTP attempts** theo retry hiện có:

| Attempt | Thời điểm response (UTC, 24/09/2026) | HTTP | Kết quả |
|---|---|---:|---|
| 1 | 08:13:05.924424 | 503 | UNAVAILABLE, provider báo nhu cầu cao |
| 2 | 08:13:10.068373 | 503 | UNAVAILABLE, provider báo nhu cầu cao |
| 3 | 08:13:18.966259 | 200 | Nội dung extraction hợp lệ |

HTTP 200 tương ứng **15:13:18 giờ Việt Nam**, response ID
`mNu0aqrmC6jQg8UP4eHe-Q8`, model `gemini-3.6-flash`.
Toàn bộ `choices[0].message.content` thực tế, chỉ định dạng lại JSON:

```json
{
  "fields": {
    "price_vnd": 8000000000,
    "area_m2": 80.0,
    "bedroom_count": 2,
    "bathroom_count": 2,
    "floor_count": null,
    "front_width_m": null,
    "road_width_m": null,
    "property_type": null,
    "listing_type": null,
    "direction": null,
    "legal": null,
    "furniture": null,
    "province": null,
    "district": null,
    "address": null
  },
  "confidence": 1.0,
  "evidence": {
    "price_vnd": "tổng giá 8 tỷ đồng",
    "area_m2": "diện tích 80 m2",
    "bedroom_count": "2 phòng ngủ",
    "bathroom_count": "2 phòng tắm"
  }
}
```

Raw HTTP body đầy đủ, gồm response envelope, được giữ tại
`gemini_output[2].raw_response`. SHA-256 của **audit payload response**
(`http_status` và `raw_response` sau sanitization), không phải riêng chuỗi body:
`a940b791b34e1c5ebf03a88a4705d7239f3590777ac947bc6f187312a0eb00f6`.

Pydantic parse thành công; `parsed_response[0].parsed_response` giữ các giá trị
trên, chuyển `price_vnd` thành float `8000000000.0`; các field tùy chọn
không có giá trị giữ `null`, không bổ sung suy đoán. Confidence 1.0 là giá trị
Gemini tự trả về, **không phải phép đo độ chính xác trên dataset**. Các trích
dẫn evidence qua kiểm tra có trong input. Bước merge chỉ bổ sung field raw
đang thiếu; processor sau đó normalize thành record cuối:

| Field raw được merge | Trước AI | Sau merge | Field chuẩn hóa trong final record |
|---|---|---|---|
| `price_text` | chưa có | `8.0 tỷ` | `price_vnd=8000000000.0` |
| `area_text` | chưa có | `80.0 m2` | `area_m2=80.0` |
| `bedroom_text` | chưa có | `2` | `bedroom_count=2` |
| `bathroom_text` | chưa có | `2` | `bathroom_count=2` |

Đây là đúng bốn field trong `field_changes` và
`ai_cleaning_audit.changed_fields`. Title, description, URL, loại căn hộ,
loại giao dịch và tỉnh/quận giữ nguyên. Các chuỗi `8.0 tỷ`, `80.0 m2` là
định dạng do code merge tạo từ số Gemini trả về. Giá/m² cuối cùng
`100000000.0` do processor tính từ giá/diện tích; metadata và các feature
khác của processor không được gán nhầm là output Gemini.

Record cuối nằm tại **`real_estate_stress_db.training_features`** với
`ai_status=success`, `ai_model=gemini-3.6-flash`, `ai_attempts=3`,
`processing_method=ai_extraction`, `is_synthetic=true`,
`is_model_candidate=false`. Receipt có `status=completed, outcome=success`,
`origin=stress`, hoàn tất lúc `2026-09-24T08:13:36.720959+00:00`.
Tên collection `training_features` không có nghĩa fixture được dùng train:
cờ synthetic và `is_model_candidate=false` loại nó khỏi dữ liệu huấn luyện.

#### Bằng chứng đối chiếu, khôi phục cấu hình và giới hạn

| Hop | Topic | Partition | Offset |
|---|---|---:|---:|
| Raw input | real_estate_stress_raw | 2 | 55686 |
| AI input | real_estate_ai_input | 2 | 7 |
| AI result thành công | real_estate_ai_results | 2 | 6 |

- Artifact [gemini-clean-audit.json](../runtime/research/gemini-clean-audit.json)
  có `status=verified`, `expected_model=gemini-3.6-flash` và chuỗi
  `before → gemini_input → gemini_output → parsed_response → after_extraction → final_record`;
  kèm Kafka offsets, receipt và storage. Artifact được giữ local, ngoài Git.
- Mongo `ai_provider_audit` ghi các bước HTTP cho đúng event đã chọn;
  `ai_cleaning_audit` lưu before/after; `ai_extractions` và
  `ai_result_receipts` lưu kết quả tương ứng. Log
  [ai-agent-success.log](../runtime/research/gemini-verification-runtime/ai-agent-success.log)
  có event ID, audit stage và SHA-256.
- `raw_record_preserved=true`: bản gốc được giữ nguyên trong `listings_raw`.
  Đối chiếu DB chính theo URL/event ID cho `listings_raw`,
  `training_features`, `invalid_records`, `ai_cleaning_audit`,
  `ai_provider_audit` đều **0 document**.
- Sau chạy đã khôi phục `AI_STRESS_ENABLED=false`, bỏ audit event filter,
  giữ production `AI_FALLBACK_ENABLED=false` và `AI_ENABLED=true`.
  `LLM_MODEL` runtime trở lại giá trị ban đầu **`gemini-2.5-flash`**.
  Do đó đây là kiểm chứng thành công bằng override `gemini-3.6-flash`,
  **chưa phải chuyển production sang model mới**; lỗi model cũ chưa được
  loại bỏ khỏi cấu hình vận hành gốc. Bằng chứng trạng thái trước/sau tại
  `runtime/research/gemini-verification-runtime/state-before-success-attempt.json`
  và `state-after-success-attempt.json`.
- Kiểm tra liên quan: **140 tests pass**, compileall bảy file Python và
  `git diff --check` pass; bốn container liên quan healthy, trạng thái cấu hình
  sau chạy khớp trước chạy. Chi tiết tại
  [validation-success.json](../runtime/research/gemini-verification-runtime/validation-success.json).

Đã xác minh request/response Gemini thật, parse, merge, processor validation
và final storage cho **đúng record có audit ở trên**. Chưa xác minh toàn bộ
dataset crawl đã qua Gemini, cleaning lịch sử, độ chính xác extraction trên
mẫu đại diện hay độ ổn định provider dài hạn. Hai response 503 còn cho thấy
một lần thành công không chứng minh dịch vụ luôn sẵn sàng.

#### Luồng tạo file bằng chứng và cách mở xem

File trực tiếp tạo `runtime/research/gemini-clean-audit.json` là
[scripts/verify_gemini_pipeline.py](../scripts/verify_gemini_pipeline.py).
Hàm `run_case()` gửi record kiểm chứng qua Kafka, quan sát các message và
đọc kết quả từ MongoDB; hàm `write_report()` ghi dữ liệu tổng hợp ra JSON
tại đường dẫn `--output`. File audit là bản chụp của lượt chạy verifier,
không tự cập nhật liên tục khi agent xử lý các record mới.

```text
verify_gemini_pipeline.py: run_case() gửi raw record qua Kafka
  -> processor chuyển record đủ điều kiện fallback vào real_estate_ai_input
  -> agents/worker.py: capture() ghi before cho event được chọn
  -> agents/providers.py: emit() ghi request, HTTP status, raw response Gemini
  -> agents/extraction.py: emit() ghi parsed response
  -> agents/worker.py: emit() ghi extraction_result
       -> agents/provider_audit.py: che secret, thêm timestamp và SHA-256
       -> MongoDB real_estate_stress_db.ai_provider_audit (lượt kiểm chứng này)
  -> real_estate_ai_results -> processor validation -> final record + receipt
  -> verify_gemini_pipeline.py: run_case() đối chiếu Kafka và các collection MongoDB
  -> write_report() -> runtime/research/gemini-clean-audit.json
```

Các lệnh `emit()` trong luồng trên cùng sử dụng sink của
[agents/provider_audit.py](../agents/provider_audit.py), ghi các stage vào
document có `_id=event_id` trong `ai_provider_audit`. Chỉ event nằm trong
`AI_AUDIT_EVENT_IDS` mới được ghi; audit mặc định tắt. Collector còn đọc
`ai_cleaning_audit`, `ai_extractions`, `ai_result_receipts`, `listings_raw`,
`training_features` và `invalid_records` để đối chiếu quá trình xử lý.
Vì vậy, file xuất chứa cả bằng chứng provider và kết quả xử lý cuối của pipeline.

Để tự xem, mở
[runtime/research/gemini-clean-audit.json](../runtime/research/gemini-clean-audit.json)
trong IDE, rồi tìm các key sau:

| Key trong artifact | Nội dung đối chiếu |
|---|---|
| `before` | Raw record trước khi xử lý AI |
| `gemini_input[*].request.messages` | Toàn bộ system/user messages thực sự gửi Gemini, gồm các lần retry |
| `gemini_output[*]` | HTTP status và raw response của từng attempt |
| `parsed_response` | JSON extraction đã qua kiểm tra schema |
| `after_extraction` | Record sau khi merge các field được trích xuất |
| `field_changes` | Giá trị trước/sau của từng field thay đổi khi merge |
| `final_record` | Bản ghi cuối sau processor validation và chuẩn hóa |
| `receipt`, `final_storage_collection` | Trạng thái xử lý và collection lưu kết quả cuối |

Raw response được giữ trong artifact; ví dụ INPUT/OUTPUT trình bày ở trên
được đối chiếu với artifact này. Đường dẫn `runtime/` nằm ngoài Git nên cần
mở file tại máy đã chạy verifier. Nếu lượt chạy thất bại, verifier vẫn lưu
bằng chứng thu được với `status=blocked`; có file JSON không đồng nghĩa
Gemini đã trích xuất thành công.

Phân biệt các script có tên gần nhau:

| Script | Vai trò |
|---|---|
| `verify_gemini_pipeline.py` | Kiểm chứng cuộc gọi Gemini thật và xuất bằng chứng INPUT/OUTPUT đầy đủ |
| `audit_gemini_clean.py` | Tổng hợp thống kê và hash từ `ai_cleaning_audit`, không thu raw request/response Gemini; lưu sang file riêng để giữ nguyên artifact đầy đủ |
| `verify_agent_pipeline.py` | Kiểm thử pipeline với transport LLM mô phỏng, không phải bằng chứng gọi Gemini thật |

#### Code audit và cách kiểm chứng lại

[provider_audit.py](../agents/provider_audit.py),
[providers.py](../agents/providers.py), [extraction.py](../agents/extraction.py)
và [worker.py](../agents/worker.py) bổ sung audit có cấu trúc: mặc định tắt,
chỉ bật cho event được chọn, che secret, không thu thập Authorization header.
Body lỗi đọc có giới hạn; lỗi ghi audit không thay đổi kết quả nghiệp vụ.
Export tổng hợp cũ `audit_gemini_clean.py` không thay thế bằng chứng HTTP.

[verify_gemini_pipeline.py](../scripts/verify_gemini_pipeline.py) hỗ trợ
`--prepare --model`, lưu model kỳ vọng và kiểm tra model ở request/kết quả.
Cần kiểm tra listing với credential hiện tại trước mỗi lần chọn model;
lệnh dưới dùng model đã được kiểm chứng trong lần báo cáo này.
Images phải chứa code audit hiện tại; không bật producer stress khác.
Ví dụ chạy lại từ cấu hình gốc đã tắt AI stress/audit; chọn đường dẫn case mới
nếu tên dưới đây đã tồn tại:

```powershell
$casePath = "runtime/research/gemini-new-case.json"
$overridePath = "runtime/research/gemini-new-compose.json"
python scripts/verify_gemini_pipeline.py --prepare --model gemini-3.6-flash --case $casePath
if ($LASTEXITCODE -ne 0) { throw "Prepare failed" }
$eventId = (Get-Content -Raw $casePath | ConvertFrom-Json).event_id
$testServices = @{}
foreach ($service in @("processor", "processor-2", "processor-3", "ai-agent")) {
    $testServices[$service] = @{ environment = @{ AI_STRESS_ENABLED = "true" } }
}
$testServices["ai-agent"].environment.LLM_MODEL = "gemini-3.6-flash"
$testServices["ai-agent"].environment.AI_AUDIT_EVENT_IDS = $eventId
@{ services = $testServices } | ConvertTo-Json -Depth 6 |
    Set-Content -Encoding UTF8 $overridePath
try {
    docker compose -f docker-compose.yml -f $overridePath up -d --no-deps processor processor-2 processor-3 ai-agent
    if ($LASTEXITCODE -ne 0) { throw "Test containers failed" }
    python scripts/verify_gemini_pipeline.py --run --case $casePath --output runtime/research/gemini-new-audit.json --timeout-seconds 180
    if ($LASTEXITCODE -ne 0) { throw "Verification blocked; inspect artifact" }
} finally {
    docker compose up -d --no-deps processor processor-2 processor-3 ai-agent
}
```

Không dùng lại event đã publish; verifier từ chối để tránh hiểu nhầm cache
là một cuộc gọi mới. Chỉ kết luận thành công khi artifact ghi `verified`
với đủ bằng chứng HTTP 200, parsed extraction và final receipt thành công.
