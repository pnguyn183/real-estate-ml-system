# Project Report

**Cập nhật: 20/11/2026 · Nhánh: `dev`.**

Báo cáo tổng hợp các chức năng đã triển khai, kết quả kiểm chứng, những hạn chế
hiện tại và kế hoạch phát triển tiếp theo của hệ thống. Dữ liệu thực nghiệm
gồm lượt kiểm chứng container ngày **05/11/2026**, xác minh khôi phục ngày
**11/11/2026**, lượt lifecycle hữu hạn có kiểm soát ngày **14/11/2026** và
đánh giá forecast từ phiên telemetry hai giờ ngày **17/11/2026**.
Ngày **18/11/2026** bổ sung panel extraction, chuẩn bị sạch dataset giá,
predictive-control gate và tái kiểm chứng Prometheus/lifecycle/overload guard.
Ngày **20/11/2026** siết provenance gate và thử calibration lại; lượt core-only
đi tới 300 rps nhưng safety guard vẫn chặn trước A/B khi RAM host xuống dưới 10%.

**Trạng thái tổng thể:** đã có stress agent, extraction agent, vòng phản hồi LLM,
công cụ sửa tài nguyên Docker/Compose và hệ thống đo lường. Đã xác minh vòng LLM
ở tải nhỏ, chuyển model khi lỗi 503, dừng/bật lại một dịch vụ phụ theo telemetry
và khôi phục xử lý tải nhỏ. **Chưa chứng minh LLM tự điều tiết để giữ ổn định khi
quá tải.** Trên phiên synthetic hai giờ, forecast 5 và 10 phút đều vượt
persistence theo gate offline; forecast vẫn chưa tham gia controller.

Quy ước checklist:

- `[x]`: hoàn thành đúng phạm vi ghi trong mục; có thể chỉ là hoàn thành mã/công cụ nếu được ghi rõ.
- `[ ] Một phần`: đã triển khai một phần hoặc còn thiếu kiểm chứng để nghiệm thu.
- `[ ] Chưa hoàn thành`: chưa có triển khai hoặc chưa có bằng chứng đạt tiêu chí.
- **Git**: bằng chứng đã lưu trong repository. **Local**: file trong `runtime/` hoặc artifact trên máy chạy, không có khi chỉ clone GitHub. Test mô phỏng không thay thế số đo thực tế.

## 0. Bài toán, phạm vi và mức sẵn sàng

### 0.1. Hệ thống giải quyết vấn đề gì?

Tin đăng bất động sản đến từ nhiều nguồn, không đồng nhất về schema và thường có
văn bản thiếu cấu trúc. Khi tốc độ nhận cao hơn tốc độ xử lý, hàng đợi và độ trễ
tăng; vận hành thủ công khó phân biệt nhu cầu được chào, dữ liệu đã nhận và dữ
liệu đã xử lý xong. Hệ thống hiện tại giải quyết các phần sau:

| Thành phần | Giá trị đã có bằng chứng | Mục tiêu thiết kế chưa được chứng minh |
| --- | --- | --- |
| Ingestion/Kafka | Nhận dữ liệu có checkpoint, tách topic synthetic, hấp thụ chênh lệch tốc độ | High availability nhiều máy và khả năng chịu lỗi production |
| Processing | Chuẩn hóa, validate, enrich, lưu raw/valid/invalid/DLQ; ba processor cùng group | Tính đúng sự thật của nội dung tin đăng |
| Extraction | LLM bổ sung field thiếu với schema/evidence guard; một fixture provider thật đã đi hết pipeline | Precision/recall/F1 trên mẫu crawl đại diện |
| Price forecasting | Mô hình giá có API; pipeline mới chặn target-text leakage và group split | Giá giao dịch thực, score sạch mới và tổng quát hóa ngoài snapshot |
| Workload forecasting | Forecast 5/10 phút vượt persistence trên một phiên synthetic hai giờ | Nhu cầu tự nhiên theo ngày/tuần và lợi ích predictive control |
| Monitoring | Quan sát traffic, tail latency, lỗi, lag và CPU/RAM theo phạm vi | Golden Signals end-to-end cho toàn API và USE đầy đủ cho disk/network/queue |
| Agent control | LLM đã phân loại tải, cấp rate lease và chọn stop/start dịch vụ phụ trong lượt hữu hạn | Mức cải thiện khi quá tải, tự chọn allocation có lợi và vận hành liên tục |

### 0.2. Khi nào dùng self-hosted, khi nào dùng managed cloud?

Không có cơ sở để kết luận kiến trúc hiện tại luôn tốt hơn Cloud. Bản self-hosted
phù hợp cho đồ án, thử nghiệm on-premise, dữ liệu cần giữ trong phạm vi kiểm soát,
workload nhỏ/tương đối dự đoán được và nghiên cứu cgroup/container control. Managed
cloud phù hợp hơn khi cần SLA, đa vùng, scale nhanh, backup/patching do nhà cung cấp
quản lý và đội vận hành nhỏ.

| Tiêu chí | Kiến trúc hiện tại | Managed cloud |
| --- | --- | --- |
| Chi phí hạ tầng | Dùng máy sẵn có; phải tính khấu hao, điện, mạng, backup | Tính theo compute/broker/database/storage/egress/observability/API |
| Chi phí vận hành | Tự patch Kafka/Mongo, giám sát, backup và xử lý sự cố | Giảm một phần toil nhưng vẫn cần cấu hình, giám sát và kiểm soát chi phí |
| Scale/HA | Scale dọc và giới hạn Compose; ba broker vẫn cùng một host | Dễ scale/replicate hơn nhưng phụ thuộc quota, dịch vụ và vùng |
| Latency/data control | Có thể đặt gần nguồn và giữ dữ liệu nội bộ | Có latency mạng/egress; đổi lại có dịch vụ bảo mật và compliance tùy gói |
| Maintenance | Toàn quyền thử nghiệm controller, đồng thời tự chịu downtime | Ít quyền chạm hạ tầng thấp; có nguy cơ vendor lock-in |

Cấu hình hiện đo được gồm ba processor `1 CPU/768 MiB`, trainer `1 CPU/1024 MiB`,
ba Kafka broker và các service phụ trên một host. Chưa có số điện, khấu hao, giờ
vận hành, retention, egress, mức hỗ trợ hoặc báo giá Cloud tương đương nên chưa thể
đưa ra so sánh tiền tệ. Mô hình cần dùng là:
`TCO_self = khấu hao + điện + mạng/backup + giờ vận hành + rủi ro downtime` và
`TCO_cloud = compute + broker/database + storage/egress + observability/API + giờ vận hành`.

### 0.3. Ma trận audit theo yêu cầu sản phẩm

| Yêu cầu | Hiện trạng ban đầu | Evidence/code | Phần còn thiếu | Hành động 17–18/11 | Phân loại hiện tại |
| --- | --- | --- | --- | --- | --- |
| Demo cho người không chuyên | Một phần: UI chỉ mở màn hình đăng nhập/dự đoán | `frontend/src/App.tsx`, Grafana | Chưa kể được flow và giới hạn evidence | Bổ sung demo công khai dựa trên artifact thật; không giả số live | Implemented; build/lint/HTTP preview tested; chưa có browser E2E |
| Nêu bài toán/giá trị | Một phần, rải rác trong tài liệu | Mục 2 và kiến trúc | Thiếu proven-vs-design | Bổ sung mục 0.1 | Documented và đối chiếu evidence |
| Self-hosted so với Cloud | Chưa có | Compose/resource limits | Thiếu TCO và điều kiện lựa chọn | Bổ sung mục 0.2, không tự tạo giá | Documented; monetary TCO not verified |
| Cơ sở metrics | Một phần | `research/telemetry.py`, Prometheus/Grafana | Thiếu mapping Golden/USE và nguồn | Bổ sung mục 5.4 và References | Implemented một phần; controlled telemetry experimentally verified |
| Luồng xử lý/điều tiết | Đã có code/test; LLM overload chưa kiểm chứng | controller, harness, evidence 07/10–14/11 | Thiếu A/B LLM hợp lệ | Giữ trạng thái chưa kiểm chứng; bổ sung p95/p99 dashboard | Implemented/tested; LLM overload improvement not verified |
| Đánh giá model giá | Một phần | production metrics và legacy benchmark | Legacy thiếu MdAPE; leakage/group split chưa xử lý | Thêm MdAPE; redaction giá, dedup, group split và benchmark sạch | Clean single-holdout diagnostic verified; deployment/generalization not verified |
| Dự báo tải | Offline 5 phút đạt, 10 phút cũ không đạt | benchmark/forecast artifact | Chưa đánh giá phiên hai giờ, chưa tích hợp control | Chạy chronology 5/10 phút; thêm fail-closed predictive gate, không nối controller | 5/10 phút offline synthetic verified; predictive-control trial blocked/not implemented |
| Extraction | Pipeline đầy đủ, một fixture provider thật | schema/worker/router/Kafka/Mongo/DLQ | Chưa có benchmark nhãn người | Thêm protocol/evaluator và panel 34 record cho hai reviewer + adjudication | Panel prepared; human labels và dataset accuracy not verified |
| Đánh giá Agent | Harness có; chưa có cặp overload LLM hợp lệ | `llm_experiment.py`, lifecycle evidence | Không có improvement % của LLM | Không lấy guard/drain làm thành tích; giữ `not verified` | Harness implemented/tested; LLM improvement not verified |

## 1. Tiến độ triển khai và kiểm chứng

### 1.1. Agent quan sát và tự điều tiết

- [x] **Có telemetry phục vụ điều tiết.** Đã đo CPU/RAM container, RAM/swap VM, tài nguyên host, Kafka ingress/throughput/lag, latency, lỗi và lịch sử quyết định. Có phát hiện số đo thiếu/cũ. Mã: [telemetry.py](../research/telemetry.py), [control_agent.py](../agents/control_agent.py). Chạy thật: [06/10](evidence/LLM_FEEDBACK_VALIDATION_20261006.md), [07/10](evidence/LLM_FEEDBACK_VALIDATION_20261007.md).
- [x] **Phân loại trạng thái tải bằng LLM.** Model nhận telemetry và chọn `light`, `normal`, `heavy`; đã quan sát `light → normal` khi có dữ liệu thật. Trạng thái và tốc độ được model đề xuất từ số đo; code kiểm tra giới hạn an toàn trước khi áp dụng. [Thiết kế và phạm vi](LLM_FEEDBACK_CONTROL.md), [bằng chứng 07/10](evidence/llm-feedback-20261007.json).
- [x] **Có dynamic feedback loop rate limiting chạy được.** Telemetry → LLM → quyết định JSON → policy có thời hạn → producer/Kafka → telemetry vòng sau đã chạy thật với 646 record. Rate gate điều chỉnh nhịp phát theo `rate_per_second`. [runtime_policy.py](../agents/runtime_policy.py), [kiểm chứng 07/10](evidence/LLM_FEEDBACK_VALIDATION_20261007.md).
- [ ] **Một phần: LLM tự tăng/giảm nhiều mức tốc độ theo tải.** Cơ chế đã có nhưng mọi quyết định LLM hợp lệ trong hai lượt 07/10 đều chọn **5 record/s**. Việc giảm về 0 do RAM/provider thuộc guard của code, chưa phải bằng chứng LLM tự giảm tốc để giải quyết nghẽn. [Giới hạn thực nghiệm](evidence/LLM_FEEDBACK_VALIDATION_20261007.md).
- [ ] **Một phần: agent biết giới hạn hệ thống và phân biệt rảnh/bình thường/nặng.** Có phân loại LLM, capacity Docker và throughput đã quan sát; đã tạo được quá tải ở cấu hình cụ thể. Chưa xác định công suất tối đa hoặc mức vận hành tối ưu có tính lặp lại. `capacity_evidence()` không coi vài mẫu rảnh là bằng chứng saturation. [control_agent.py](../agents/control_agent.py), [hiệu chuẩn 14/10](evidence/LLM_OVERLOAD_VALIDATION_20261014.md).
- [x] **Có bảo vệ khi thiếu RAM hoặc telemetry/model bị lỗi.** Policy thiếu/sai/hết hạn đưa producer về rate an toàn; thiếu RAM ngăn phát mới và ngăn bắt đầu training, processor tiếp tục xử lý backlog. Đã quan sát thật các lần `emergency_host_memory`. Cơ chế này không tạo thêm RAM và không bảo đảm cả stack vẫn chạy khi footprint nền vượt bộ nhớ. [06/10](evidence/LLM_FEEDBACK_VALIDATION_20261006.md), [runtime_policy.py](../agents/runtime_policy.py).

### 1.2. Docker resource constraints và luồng training

- [x] **Khai báo giới hạn tài nguyên Docker.** Compose có CPU/RAM/swap cho processor và trainer; cấu hình mẫu mỗi processor 1 CPU/768 MiB, trainer 1 CPU/1024 MiB. Đây là giới hạn khai báo, không phải số đo mức tiêu thụ hiện tại. [docker-compose.yml](../docker-compose.yml), [.env.example](../.env.example).
- [x] **Có công cụ để agent sửa resource limit trực tiếp trong `docker-compose.yml`.** Công cụ kiểm tra ngân sách, áp dụng `docker update`, xác minh runtime rồi lưu YAML; có backup, kiểm tra thay đổi đồng thời và rollback. Ngày 06/10 đã đổi processor 1 CPU/768 MiB → 0,9 CPU/704 MiB và khôi phục; lỗi được chèn để kiểm tra rollback cũng khôi phục thành công. [compose_resources.py](../agents/compose_resources.py), [bằng chứng actuator](evidence/LLM_FEEDBACK_VALIDATION_20261006.md).
- [ ] **Một phần: LLM tự chọn CPU/RAM và chứng minh thay đổi đó có ích.** Công cụ đã chạy thật, nhưng các allocation của bài thử 06/10 do người thử cung cấp. Quyết định LLM 07/10 có `resources={}`. Chưa có bằng chứng LLM tự chọn allocation, áp dụng và cải thiện quá tải. Không có autoscaling số replica hoặc broker. [Phạm vi công cụ](LLM_FEEDBACK_CONTROL.md).
- [x] **Tách tiến trình training khỏi xử lý stream và cho agent cấp quyền bắt đầu.** Trainer là service riêng, đọc `training_allowed`, có khóa tránh fit đồng thời; Airflow nhường training cho trainer khi control bật. Một fit đang chạy được phép hoàn tất. [auto_train.py](../scripts/auto_train.py), [train_model.py](../modeling/train_model.py), [runtime_policy.py](../agents/runtime_policy.py), [DAG](../airflow/dags/real_estate_pipeline.py).
- [ ] **Một phần: kiểm chứng training hoàn tất dưới điều tiết mà stream vẫn ổn định.** Đã thấy hoãn training và cấp quyền thật, nhưng các lượt control cố ý không chạy fit để giữ model phục vụ. Tách service vẫn dùng chung host/VM; chưa phải cô lập tài nguyên tuyệt đối. [06/10](evidence/LLM_FEEDBACK_VALIDATION_20261006.md), [07/10](evidence/LLM_FEEDBACK_VALIDATION_20261007.md).
- [x] **LLM chọn dừng/bật một dịch vụ phụ theo telemetry.** LLM chọn hành động; guard tạm dừng admission và actuator kiểm tra quyền, dependency, cooldown, host/VM memory cùng byte reserve trước khi gọi Docker. Nhóm kiểm thử lịch sử liên quan đạt **337 tests**. Ngày 14/11, LLM dừng rồi bật lại đúng cùng container `mongo-express`: RAM host khả dụng tại hai quyết định lần lượt 9,73% và 11,67%; sau khi bật, model mở ingress 0,2 rps và có ba cửa sổ sạch liên tiếp với lag 0, p95 tối đa 0,04875s và throughput dương. Cleanup 15/15 bước thành công. Ngày 18/11, actuator cũng hoàn tất một chu kỳ stop/start cùng-ID cho predictor và isolated trainer probe quan sát ba drain ACK, nhưng các hành động probe này không phải LLM; ACK chỉ có console capture và trainer production cũ chưa recreate với image mới. Chưa có ba chu kỳ lặp hoặc tác động RAM nhân quả. [Mã công cụ](../agents/container_lifecycle.py), [bằng chứng 14/11](evidence/container-lifecycle-recovery-20261114.json), [ops 18/11](evidence/ops-validation-20261118.json), [cấu hình](LLM_FEEDBACK_CONTROL.md#agent-dừng-và-bật-lại-dịch-vụ-phụ).

### 1.3. Model và chuyển dự phòng

- [x] **Có cấu hình model/API key và router dự phòng.** Hỗ trợ endpoint/model/key chính và dự phòng, có thể tách cấu hình điều tiết khỏi extraction; cooldown và deadline được xử lý trong router. [model_router.py](../agents/model_router.py), [providers.py](../agents/providers.py), [cấu hình vận hành](#6-cấu-hình-và-điều-kiện-vận-hành).
- [x] **Chuyển model khi model chính lỗi dịch vụ.** Ngày 07/10 có hai lần HTTP 503 thật ở Gemini 3.6 Flash, chuyển sang Gemini 3.5 Flash-Lite và nhận HTTP 200; luồng tiếp tục khi lease còn hiệu lực. Đây là tên model trong bằng chứng lịch sử. [Báo cáo và log tham chiếu](evidence/LLM_FEEDBACK_VALIDATION_20261007.md).
- [ ] **Một phần: tự switch khi hết token/quota và có model miễn phí dự phòng.** Router đã xử lý quota/rate limit và có test mô phỏng, nhưng chưa kiểm chứng cạn quota thật. Fallback đã thử dùng cùng key/provider, không tạo quota độc lập; chưa chứng minh một route miễn phí được cấu hình vận hành thường xuyên. Phải có tài khoản, model và key được cấp quyền; không tự tạo key hay mua quota. [test_model_router.py](../utils/tests/test_model_router.py), [cấu hình fallback](LLM_FEEDBACK_CONTROL.md).

### 1.4. Kiểm chứng tích hợp và độ ổn định

- [x] **Kiểm chứng tích hợp trên Docker, Kafka/Mongo và provider thật.** Đã có dữ liệu Kafka/Mongo thật, gọi model thật và thử actuator thật; kết quả được ghi theo từng lượt, gồm cả thất bại. [06/10](evidence/LLM_FEEDBACK_VALIDATION_20261006.md), [07/10](evidence/LLM_FEEDBACK_VALIDATION_20261007.md), [13/10](evidence/LLM_OVERLOAD_VALIDATION_20261013.md), [14/10](evidence/LLM_OVERLOAD_VALIDATION_20261014.md), [19/10](evidence/LLM_OVERLOAD_VALIDATION_20261019.md), [lifecycle 14/11](evidence/container-lifecycle-recovery-20261114.json).
- [x] **Xây dựng bộ thử đối chứng điều tiết LLM.** Đã xây harness baseline/LLM, hiệu chuẩn quá tải, workload cùng seed, tách nhu cầu trước gate khỏi lượng được nhận, lưu quyết định/ingress/commit/lag/latency/lỗi và trạng thái khôi phục. [llm_experiment.py](../research/llm_experiment.py), [llm_report.py](../research/llm_report.py), [tiêu chí đối chứng](LLM_OVERLOAD_EXPERIMENT.md).
- [ ] **Chưa hoàn thành: chứng minh LLM giữ hệ thống ổn định khi quá tải.** Các lượt đối chứng đến 20/11 chưa hoàn thành cặp hợp lệ; không có quyết định LLM trong các nhánh overload đã lưu. Lượt core-only thứ hai bắt đầu với RAM host 15,72%, qua 10/30/100 rps và tới 300 rps; tại mẫu guard RAM còn 9,95%, lag 1 và p95 0,049s. Cả 8.154 record đã commit, nhưng chưa xuất hiện overload bền vững và stage 600 rps không được phép chạy, nên nhánh LLM/A-B không bắt đầu. [14/11](evidence/llm-overload-20261114.json), [18/11](evidence/llm-overload-20261118.json), [20/11](evidence/llm-overload-20261120.json).
- [x] **Cải thiện độ ổn định và khả năng truy vết.** Đã sửa tranh chấp policy file Windows, timeout observer, đọc lịch sử anomaly theo luồng và ghi hash dependency thực sự trong worker. Chi tiết commit/tests ở [mục 7](#7-các-bản-sửa-đã-hoàn-thành).
- [ ] **Một phần: kiểm chứng bản sửa giảm RAM trên Docker.** Mã và test đã hoàn tất; phiên 23/10 vẫn dùng cùng container/image identity như trước bản sửa và thiếu hash dependency cần thiết. Phải xác minh/recreate worker trước khi nhận kết quả mới là kiểm chứng bản sửa. Chưa có số đo giảm RAM thực tế. [Provenance và giới hạn](evidence/TELEMETRY_VALIDATION_20261024.md).
- [x] **Theo dõi riêng RAM host, Docker VM và container.** Các số đo được lưu cùng thời điểm và phạm vi đo, giúp xác định áp lực bộ nhớ ở từng lớp. RAM container thấp không đồng nghĩa host hoặc VM còn đủ bộ nhớ. [Định nghĩa số đo](#5-dữ-liệu-telemetry-và-kết-quả-dự-báo).

### 1.5. Thu telemetry và đánh giá mô hình

- [x] **Kiểm kê dữ liệu telemetry hiện có và đánh giá có đủ dùng không.** Đã audit lịch sử, loại phiên chưa kết thúc khỏi export và benchmark 1.732 observations của 7 phiên đã kết thúc. Đủ để đánh giá bước đầu trên workload tổng hợp, chưa đủ chứng minh hoạt động dài hạn hoặc điều tiết LLM. [Kiểm kê 23/10](evidence/TELEMETRY_VALIDATION_20261023.md), [kết quả 24/10](evidence/TELEMETRY_VALIDATION_20261024.md).
- [x] **Thu telemetry trong phiên hai giờ.** Ngày 14/11, phiên controlled-synthetic được yêu cầu chạy 7.200 giây, healthy load span 7.195,088 giây, có 1.446 mẫu hợp lệ, 219.434 ACK, không có sample lỗi/gap timestamp và lag cuối bằng 0; lean-mode khôi phục dịch vụ phụ không lỗi. Dữ liệu đủ làm đầu vào đánh giá chronology nhưng chưa đại diện traffic tự nhiên và không tự chứng minh LLM control. [Bằng chứng phiên](evidence/telemetry-session-20261114.json).
- [x] **Kiểm định dữ liệu và đối chiếu Gemini input/output.** Đã có audit skew/missingness/outlier và một record tổng hợp qua Gemini thật → parse → merge → MongoDB. Chưa xác minh chất lượng extraction toàn dataset. [Audit dữ liệu](#43-benchmark-mô-hình-giá-và-kiểm-định-dữ-liệu), [phụ lục Gemini](#phụ-lục-a--bằng-chứng-gemini-ngày-24092026).
- [x] **Benchmark các mô hình giá trên cùng tập chia sạch.** Đã so VotingRegressor, Random Forest, Gradient Boosting và XGBoost sau redaction giá, bỏ exact duplicate và chia 70/15/15 theo block bất động sản không chứa URL/giá/free text. Run sạch 18/11 hoàn tất nhưng chỉ là một fixed holdout diagnostic, chưa thay model production. [Kết quả định lượng](#43-benchmark-mô-hình-giá-và-kiểm-định-dữ-liệu), [evidence sạch](evidence/price-benchmark-grouped-20261118.json), [benchmark.py](../research/benchmark.py).
- [x] **Đo tải ba broker, thời gian phản ứng, trước/sau và tạo biểu đồ.** Có paired-v4 bằng controller theo luật, log T0–T3 và biểu đồ từ dữ liệu thật. Ingress gần đều không đồng nghĩa CPU cân bằng; recovery đo được xảy ra trong drain. [Kết quả paired-v4](#42-kết-quả-controller-theo-luật).
- [x] **Dự báo traffic trước 5 phút ở mức thử nghiệm offline.** Lượt 24/10 đã đạt; đánh giá mới trên phiên synthetic hai giờ chọn XGBoost bằng validation, test MAE 4,528 record/s, RMSE 5,569, R² 0,848 và RMSE thấp hơn persistence 78,77%. Artifact chỉ dành cho inference thử nghiệm. [Bằng chứng 17/11](evidence/telemetry-forecast-2h-20261117.json).
- [x] **Dự báo traffic trước 10 phút đạt gate offline trên phiên hai giờ.** Lượt ngắn 24/10 từng không đạt (R² −0,084); lượt 17/11 chọn Gradient Boosting bằng validation, test MAE 6,387 record/s, RMSE 7,616, R² 0,724 và RMSE thấp hơn persistence 68,79%. Đây vẫn là một run synthetic có autocorrelation cao, không phải bằng chứng traffic tự nhiên. [Bằng chứng 17/11](evidence/telemetry-forecast-2h-20261117.json).
- [ ] **Chưa hoàn thành: nối dự báo vào quyết định điều tiết và đo lợi ích.** Có [TrafficForecaster](../research/forecast.py) và [predictive-control readiness gate](../research/predictive_control_experiment.py), nhưng gate chặn trước trial vì artifact lịch sử chưa bind hash support/split và chưa có overload calibration/fingerprint hợp lệ. Miền train artifact hiện chỉ 5–57,0544 rps. Export mới sẽ ghi hai hash; chưa có experimental forecast controller hoặc ba cặp AB/BA nên production controller giữ nguyên. [Kết quả gate](evidence/predictive-control-readiness-20261118.json).
- [ ] **Các hướng LSTM, Q-Learning/DQN và clustering chưa triển khai.** Đây là các hướng mở rộng chưa tích hợp vào hệ thống. LLM gán `light/normal/heavy` không phải mô hình clustering học từ telemetry. [Phạm vi nghiên cứu](../research/README.md).

## 2. Chức năng hệ thống

| Khả năng | Đã có trong hệ thống | Bằng chứng / giới hạn |
| --- | --- | --- |
| Thu thập dữ liệu | Adapter Alonhadat/Homedy, schema thống nhất, checkpoint, timeout và pacing; crawling bật chủ động | [CRAWL_STABILITY](CRAWL_STABILITY.md), [validation 22/09 — Local](../runtime/crawl_review/validation.json): Alonhadat 20 ACK, Homedy 1 ACK trong lượt đã lưu. Homedy còn lỗi phản hồi quá lớn; Guland mới có parser offline |
| Xử lý stream | Kafka 3 broker, topic partition/replication; 3 processor cùng consumer group → MongoDB | [ARCHITECTURE](ARCHITECTURE.md), [kafka_to_mongo.py](../processing/kafka_to_mongo.py). Cùng một host, không phải HA nhiều máy |
| Chuẩn hóa và kiểm định | Parse giá/diện tích, validate, enrich feature, IQR anomaly, lưu raw/invalid/DLQ | [DATA_SCHEMA](DATA_SCHEMA.md), [PRICE_ANOMALY_DETECTION](PRICE_ANOMALY_DETECTION.md). Quy tắc/schema không chứng minh thông tin rao bán là sự thật |
| Extraction agent | Async request/result qua Kafka, gọi API LLM, schema/evidence validation, merge field thiếu, retry/cache/audit | [extraction.py](../agents/extraction.py), [worker.py](../agents/worker.py), [results.py](../agents/results.py); một fixture thật đã kiểm chứng 24/09 |
| Stress agent | Sinh dữ liệu tổng hợp có seed, profile và giới hạn; lưu manifest/ground truth/report | [stress.py](../agents/stress.py), [generator.py](../agents/generator.py); [run 09/09 — Local](../runtime/stress/final-verification-20260909/report.json): 12 sinh/12 ACK, lỗi 0. Bộ sinh này không phải LLM điều tiết |
| Cô lập dữ liệu thử | Topic `real_estate_stress_raw`, DB `real_estate_stress_db`, cờ synthetic và loại khỏi các đường train/export chính | [safety.py](../agents/safety.py), [test_synthetic_safety.py](../utils/tests/test_synthetic_safety.py). Vẫn chia sẻ CPU/RAM/broker với luồng thật |
| Huấn luyện và phục vụ giá | Trainer riêng; VotingRegressor gồm Ridge, HistGradientBoosting và SGD, target log1p/expm1, lưu model/metadata | [price_model.py](../modeling/price_model.py), [ML_PIPELINE_AUDIT](ML_PIPELINE_AUDIT.md). Giá rao bán, chưa phải giá giao dịch đã xác minh |
| API và giao diện | FastAPI đăng ký/đăng nhập/phân quyền, predict/batch/model info; React qua Nginx; health/ready; demo công khai giải thích flow, metrics và evidence | [README API](../README.md#api), [frontend](../frontend/README.md), [SystemDemo](../frontend/src/components/SystemDemo.tsx), [pipeline validation](evidence/PIPELINE_VALIDATION_20260910.md). Demo dùng số liệu artifact có nhãn synthetic; chưa có bằng chứng đầy đủ hành trình trình duyệt đăng nhập → dự đoán |
| Lịch chạy | Loop scraper/trainer; tích hợp Airflow tùy chọn, có tránh nhiều scheduler cùng chạy | [AUTOMATION](AUTOMATION.md), [Airflow](../airflow/README.md). Airflow có mã/test; chưa tìm thấy artifact nghiệm thu live đầy đủ |
| Quan sát | Prometheus metrics và Grafana Pipeline/Agent Operations/Source Ingestion; telemetry nghiên cứu có log theo phiên | [monitoring](../monitoring/README.md), [telemetry.py](../research/telemetry.py). Không suy ra đã có distributed tracing hoặc mọi alert đã gửi thành công |
| Điều tiết theo luật | Threshold/hysteresis/cooldown/token bucket trong nghiên cứu cũ | [traffic_control.py](../agents/traffic_control.py), [paired-v4](../runtime/research/paired-v4-20260921/analysis/summary.json). Kết quả này không thuộc LLM |
| Điều tiết bằng LLM | Đọc số đo/lịch sử, chọn trạng thái, rate, quyền training và resource action trong giới hạn | [control_agent.py](../agents/control_agent.py). Chức năng đã chạy tải nhỏ, hiệu quả quá tải còn chưa chứng minh |
| Điều chỉnh Docker/Compose | Công cụ LLM cập nhật CPU/RAM/swap cho 3 processor và trainer, xác minh và rollback | [compose_resources.py](../agents/compose_resources.py). `resource_actuator.py` của nghiên cứu cũ là công cụ CPU riêng; không nhầm hai cơ chế |
| Dừng/bật dịch vụ phụ theo LLM | Model chọn một dịch vụ trong danh sách được phép; lưu quyền khôi phục, chờ trainer rảnh và kiểm tra trước khi bật lại | [Bằng chứng 14/11](evidence/container-lifecycle-recovery-20261114.json): LLM dừng/bật lại cùng `mongo-express` và có ba cửa sổ xử lý sạch sau bật; không phải bằng chứng ổn định khi quá tải |
| Thu lịch sử và đánh giá forecast | Session start/status/stop/export, chế độ lean, audit dữ liệu, chronological benchmark và inference thử nghiệm | [collect_session.py](../research/collect_session.py), [TRAFFIC_SESSIONS](TRAFFIC_SESSIONS.md), [forecast.py](../research/forecast.py). Chưa nối predictive control |

## 3. Ba loại bằng chứng điều tiết phải phân biệt

| Loại | Đã chứng minh | Chưa được suy ra |
| --- | --- | --- |
| Controller theo luật, paired-v4 21/09 | Có đổi rate và giảm peak lag/latency bằng cách từ chối bớt nhu cầu | LLM đã điều tiết; throughput tăng; tài nguyên tự scale hiệu quả |
| LLM feedback 07/10 | Model nhận số đo thật, cấp lease 5/s, dữ liệu đi qua Kafka/Mongo; fallback 503 thành công | Model chọn nhiều rate theo tải, tự đổi CPU/RAM hoặc ổn định lúc quá tải |
| Forecast offline 24/10 | Dự báo requested demand 5 phút vượt persistence theo tiêu chí thử nghiệm | Nhu cầu tự nhiên dài hạn; forecast đã dùng trong control; lợi ích nhân quả của LLM |
| Lifecycle hữu hạn 14/11 | LLM chọn stop và start cùng container; guard cho phép; tải nhỏ xử lý tiếp | Vận hành liên tục; lệnh stop gây tăng RAM; ổn định quá tải |
| Forecast offline 17/11 | Forecast 5/10 phút vượt persistence trên một run synthetic hai giờ | Seasonality tự nhiên; predictive control đã hoạt động hoặc cải thiện hệ thống |

LLM quyết định trong phạm vi công cụ; schema, trần tốc độ, ngân sách tài nguyên,
lease và floor bộ nhớ vẫn do code kiểm tra. Guard là cơ chế bảo vệ cần thiết,
không được tính là quyết định của model. Model không có quyền chạy shell tùy ý.

## 4. Kết quả thực nghiệm

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
| 05/11 — lifecycle | Gemini chọn dừng Grafana/mongo-express, Docker xác nhận; 23 mẫu / 205,24 giây; 0 record phát/xử lý | Xác minh thao tác dừng theo LLM; chưa chứng minh tự bật lại hoặc throughput dưới tải. Script khôi phục dịch vụ; bước stress còn lỗi được sửa ngày 11/11 |
| 14/11 — lifecycle recovery | Gemini 3.6 Flash chọn stop, fallback Gemini 3.5 Flash-Lite chọn start cùng `mongo-express`; 3 cửa sổ sau start, lag 0, throughput 0,432–0,548/s | Xác minh chuỗi stop/start và tải nhỏ sau khôi phục trong lượt hữu hạn; guard/actuator thi hành, không chứng minh RAM causality hay overload stability |
| 14/11 — overload calibration | 9.747 commit, lên đến pha 300 rps; guard RAM host dừng; 0 quyết định LLM | Baseline calibration không tạo được bằng chứng overload đủ điều kiện và không được tính là thành tích LLM |

Nguồn Git: [Gemini — phụ lục A](#phụ-lục-a--bằng-chứng-gemini-ngày-24092026),
[06/10](evidence/LLM_FEEDBACK_VALIDATION_20261006.md), [07/10](evidence/LLM_FEEDBACK_VALIDATION_20261007.md),
[14/10](evidence/LLM_OVERLOAD_VALIDATION_20261014.md), [19/10](evidence/LLM_OVERLOAD_VALIDATION_20261019.md),
[05/11 và khôi phục 11/11](evidence/container-lifecycle-live-20261105.json),
[lifecycle 14/11](evidence/container-lifecycle-recovery-20261114.json),
[calibration 14/11](evidence/llm-overload-20261114.json).

### 4.2. Kết quả controller theo luật

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

[Mã phân tích kết quả](../research/report.py) ·
[summary.json — Local](../runtime/research/paired-v4-20260921/analysis/summary.json) ·
[biểu đồ rate — Local](../runtime/research/paired-v4-20260921/analysis/rate_limit.png).

### 4.3. Benchmark mô hình giá và kiểm định dữ liệu

Bảng legacy dưới đây dùng 3.984 train / 996 test, random holdout seed 42 và
preprocessing/log target cũ. MdAPE được tính lại từ đúng 996 prediction đã lưu,
không refit model:

| Model giá | R² | MAE, tỷ VND | RMSE, tỷ VND | MdAPE |
| --- | ---: | ---: | ---: | ---: |
| VotingRegressor hiện có | 0,488991 | 4,616007 | 14,192591 | 27,494% |
| Random Forest | 0,510214 | 4,583648 | 13,894742 | 26,255% |
| Gradient Boosting | 0,446412 | 4,910597 | 14,772045 | 30,411% |
| XGBoost | 0,592611 | 4,261827 | 12,672202 | 25,769% |

Run sạch ngày 18/11 dùng 3.491 train / 750 validation / 731 test, seed 42;
mọi model dùng cùng preprocessing và log target:

| Model giá — grouped holdout sạch | R² | MAE, tỷ VND | RMSE, tỷ VND | MdAPE |
| --- | ---: | ---: | ---: | ---: |
| VotingRegressor hiện có | 0,330272 | 6,715332 | 25,703392 | 28,906% |
| Random Forest | 0,242547 | 7,194976 | 27,335002 | 31,800% |
| Gradient Boosting | 0,336119 | 7,067361 | 25,590945 | 32,655% |
| XGBoost | 0,362721 | 6,668967 | 25,072983 | 28,539% |

Dữ liệu có skew giá 7,021, diện tích 9,871; thiếu bedrooms 52,44%, bathrooms
54,79%, latitude/longitude 100% trong snapshot đã audit. Có 687 giá trị giá
thuộc nhóm IQR outlier; outlier thống kê không tự đồng nghĩa dữ liệu sai.
Audit cũ thấy text chứa giá trong 4.917/4.980 candidate, duplicate và cross-split
overlap. Pipeline mới xử lý snapshot này thành **4.972 dòng sau khi loại 8 exact
duplicate**, redaction giá ở 4.927 dòng và tạo **4.683 block bất động sản** độc lập
với URL, giá và free text. Split seed 42 có 3.491/750/731 dòng train/validation/test,
group overlap bằng 0. Block vị trí + thuộc tính vật lý là heuristic bảo thủ: có thể
gộp hai bất động sản giống nhau hoặc bỏ sót relisting đổi metadata; zero key overlap
không chứng minh property entity leakage bằng 0. XGBoost tốt nhất về số học trên bốn
metric của run sạch này, nhưng dùng test để so bốn model không phải protocol chọn model
production; không có cross-validation, confidence interval hoặc tập thời gian/nguồn độc lập.
Vì vậy cả bảng legacy lẫn run mới chưa đủ để tuyên bố tổng quát hóa. Artifact production
5.219 mẫu là population khác, không so trực tiếp với bảng legacy 4.980 mẫu.

[Công cụ kiểm định dữ liệu](../research/data_audit.py) ·
[audit.json — Local](../runtime/research/data-audit/audit.json) ·
[metrics.json — Local](../runtime/research/legacy-benchmark/metrics.json) ·
[MdAPE và hash prediction — Git](evidence/price-benchmark-mdape-20261117.json) ·
[chuẩn bị sạch và split hash — Git](evidence/price-dataset-preparation-20261118.json) ·
[grouped benchmark sạch — Git](evidence/price-benchmark-grouped-20261118.json) ·
[biểu đồ benchmark — Local](../runtime/research/legacy-benchmark/algorithm_comparison.png).

### 4.4. Extraction: mức triển khai và khả năng đánh giá

| Hạng mục | Trạng thái | Bằng chứng / giới hạn |
| --- | --- | --- |
| Schema, prompt boundary, evidence/merge | Implemented + tested | `ExtractionOutput` strict; chỉ bổ sung field thiếu có quote trong input |
| Router/retry/rate limit/circuit/cache/audit | Implemented + tested | Có metric success/failure/duration/retry; confidence không phải accuracy đã hiệu chuẩn |
| Kafka request/result, Mongo receipt, invalid/DLQ | Implemented + tested | Có idempotency và validation lặp lại trước storage |
| Provider thật end-to-end | Experimentally verified cho 1 fixture synthetic | Phụ lục A; không đại diện dữ liệu crawl |
| Accuracy trên dataset đại diện | Not verified | Đã chuẩn bị panel 34 record thật được xác định rõ provenance; hai reviewer và adjudicator người thật chưa hoàn tất |

Đã bổ sung [protocol gán nhãn](EXTRACTION_EVALUATION.md),
[JSON Schema](templates/extraction-ground-truth.schema.json) và
[evaluator](../research/extraction_evaluation.py). Evaluator chỉ nhận file nhãn
`adjudicated` tách khỏi prediction và tính field precision/recall/F1, exact/tolerance,
completeness, invalid output, evidence validity, success, latency và retry. Không có
số accuracy nào được báo cáo cho đến khi hai reviewer độc lập và adjudicator hoàn
tất panel. 4.946/4.980 record nguồn không có provenance synthetic rõ nên bị loại;
panel hiện là toàn bộ frame 34 record `is_synthetic=false`, chưa đại diện toàn corpus.
[Manifest panel](evidence/extraction-panel-20261118/manifest.json).

## 5. Dữ liệu telemetry và kết quả dự báo

### 5.1. Số lượng, chất lượng và thời lượng

- Export dùng đánh giá 24/10 gồm **1.732 observations / 7 phiên đã kết thúc**. Phiên 01/10 có metadata `running` cũ, chưa hoàn tất audit nên bị loại; trạng thái file này không chứng minh còn tiến trình đang chạy.
- Phiên bắt đầu 23/10 giờ Việt Nam phát tải **1.602,453 giây**, span quan sát **1.619,140 giây**: 327 mẫu, 322 mẫu target khỏe, 3 mẫu lỗi thu thập; đoạn khỏe liên tục dài nhất khoảng 20m54s.
- Phiên này có 49.064 offered, 48.094 ACK, 970 rejected, delivery failures 0 và final lag 0; dừng vì VM available **8,23%**. Đây là fixed baseline, không gọi LLM điều tiết.
- Hai lịch sử passive cũ có 5.209 mẫu nhưng mọi giá trị ingress khỏe đều bằng 0; không coi số lượng đó là bằng chứng dự báo tải biến thiên.
- Kế hoạch cụ thể ngày 24/10 dài 7.200 giây không chạy và đã được thay thế bởi một phiên khác ngày 14/11. Phiên mới có **1.446 mẫu khỏe**, requested load 7.200 giây, healthy span 7.195,088 giây, 219.434 ACK, final lag 0 và không có error row/gap timestamp. Đây vẫn là fixed baseline synthetic, không gọi LLM.

[Kiểm kê 23/10](evidence/TELEMETRY_VALIDATION_20261023.md) ·
[Đánh giá 24/10](evidence/TELEMETRY_VALIDATION_20261024.md) ·
[JSON có hash nguồn](evidence/telemetry-status-20261024.json) ·
[phiên hai giờ 14/11](evidence/telemetry-session-20261114.json).

### 5.2. Forecast từ dữ liệu đã có

Benchmark dùng chia theo thời gian, loại nhãn tương lai vượt ranh giới split,
không nối qua run/gap/mẫu lỗi. Model được chọn bằng validation rồi giữ nguyên
để đo test. Target là **nhu cầu tổng hợp trước admission (`requested_rate`)**,
không phải CPU/RAM hay nhu cầu crawl tự nhiên. Phiên hai giờ dùng workload seed
`926027668`; các estimator dùng random seed `42`.

| Dataset / horizon | Model đã chọn | Test MAE | Test RMSE / persistence, record/s | Test R² | Kết quả |
| --- | --- | ---: | ---: | ---: | --- |
| 1.732 mẫu lịch sử cũ / 5 phút | XGBoost | 7,063 | 8,588 / 27,697 | 0,699 | Đạt; artifact local |
| 1.732 mẫu lịch sử cũ / 10 phút | Gradient Boosting | 13,554 | 16,813 / 27,894 | −0,084 | Không đạt |
| 1.446 mẫu phiên 2h / 5 phút | XGBoost | 4,528 | 5,569 / 26,233 | 0,848 | Đạt gate offline; 272 test |
| 1.446 mẫu phiên 2h / 10 phút | Gradient Boosting | 6,387 | 7,616 / 24,400 | 0,724 | Đạt gate offline; 260 test |

Điều kiện export: cải thiện RMSE ít nhất 5% so với persistence và R² dương trên
cả validation lẫn test. Persistence dự báo rate giữ nguyên. Không đổi sang model
khác chỉ vì thấy điểm test đẹp hơn. Hai biểu đồ đã lưu Git:
[5 phút](evidence/telemetry-forecast-300s-20261024.png),
[10 phút](evidence/telemetry-forecast-600s-20261024.png). Lượt mới có
[summary và hash](evidence/telemetry-forecast-2h-20261117.json); raw metrics/model
nằm local. Run mới có lag-1 autocorrelation 0,992 và chỉ một chuỗi scripted, vì vậy
không chứng minh seasonality hoặc tổng quát hóa production.

### 5.3. Ý nghĩa các chỉ số và RAM

| Lớp / chỉ số | Đo gì | Điều không được suy ra |
| --- | --- | --- |
| RAM host Windows | `psutil.virtual_memory()`, bộ nhớ khả dụng toàn máy | VM còn trống không bảo đảm host đủ RAM |
| RAM Docker VM | `/proc/meminfo`, available/free/swap toàn VM Linux | Khác với tổng memory các container được chọn |
| RAM container / `ram_percent` tổng hợp | Từng usage/limit; tổng nhóm theo dõi chia RAM Docker engine | Không phải phần trăm RAM Windows |
| CPU | Docker core-percent từng container; số tổng hợp chuẩn hóa theo capacity engine | 100% từng container có thể chỉ là một core, không phải toàn máy |
| `offered`, `admitted`, `acknowledged`, `committed` | Nhu cầu phát sinh → qua gate → Kafka nhận → observer thấy consumer commit | ACK chưa phải xử lý xong; commit có thể thuộc nhánh invalid/DLQ, cần đọc outcome/error |
| Lag / latency | Chênh offset consumer; histogram theo khoảng và offered→observed commit có độ phân giải polling | Không lấy trung bình các p95 khoảng thành p95 toàn lượt; idle không có latency không chứng minh recovery |

Snapshot lịch sử ngày 24/10 lúc 18:36 ghi host còn 4,959 GiB (31,56%),
Docker engine chưa hoạt động. Một lần khởi động trước đó làm RAM khả dụng xuống
0,01 GiB (0,09%), sau đó đã tắt Docker trả về trạng thái ban đầu. Đây là bằng
chứng **ngày 24/10**, không khẳng định trạng thái máy ngày 26/10.

### 5.4. Golden Signals, USE và cơ sở chọn metric

Phạm vi controlled Kafka experiment đã có đủ bốn Golden Signals ở mức đáng kể;
đây chưa phải quan sát end-to-end cho toàn sản phẩm/API. USE mới một phần: đã có
CPU/RAM utilization, Kafka/pending saturation và error, nhưng chưa có CPU run queue,
cgroup throttling, disk busy/queue, network capacity/drop, Mongo connection queue hay
OOM counters. Mỗi sample nghiên cứu có epoch/UTC, interval, collection start/end,
source timestamp và scope; Prometheus dùng timestamp scrape 15 giây.

| Metric | Ý nghĩa / đơn vị / scope | Dùng đánh giá | Vì sao chọn | Nguồn |
| --- | --- | --- | --- | --- |
| `requested/offered_rate`, `incoming_rate`, `throughput` | record/s; nhu cầu trước gate, Kafka nhận, consumer commit | Traffic và chênh lệch demand→accepted→committed | Không nhầm giảm đầu vào với tăng hiệu năng | [R1], [R3] |
| `latency_p95_seconds`, dashboard p95/p99 | giây; producer timestamp sau admission→durable input commit | Tail latency | Trung bình che khuất request chậm; histogram cho quantile | [R1], [R5], [R12] |
| `offered_to_commit_p95_upper_seconds` | giây; scheduled offer→observed commit, gồm chờ gate và polling bound | Trải nghiệm toàn luồng thử tải | Interval p95 không chứa thời gian chờ trước admission | [R1], [R3] |
| `error_rate`, invalid/DLQ/failed | tỷ lệ trên interval, application scope | Explicit/policy failure | Lỗi có thể xảy ra cả khi latency/CPU thấp | [R1] |
| Kafka group lag, `pending_offered` | record chờ consumer / trước gate | Saturation/backlog | Queue cho thấy công việc vượt khả năng phục vụ tức thời | [R1], [R2], [R6] |
| CPU/RAM host, VM, container | %, byte và swap theo từng scope | USE utilization/capacity | Xác định lớp tài nguyên bị ép; không trộn các mẫu số | [R2] |
| MAE/RMSE | VND cho giá; record/s cho forecast | Độ lớn sai số, RMSE phạt lỗi lớn hơn | Cùng đơn vị target, dễ so với baseline | [R7], [R9] |
| R² | không đơn vị; có thể âm | Mức cải thiện so với dự báo hằng số trên holdout | Không gọi là “độ chính xác phần trăm” | [R7] |
| MdAPE | median `abs(error/actual) × 100`; chỉ dùng target giá dương | Sai số tỷ lệ điển hình, ít bị một vài outlier chi phối hơn mean | Bổ sung góc nhìn scale; không thay MAE/RMSE | [R8], [R9] |
| Extraction precision/recall/F1 | field-level trên nhãn người | False extraction và missing extraction | Chuẩn đánh giá held-out information extraction | [R10] |
| Numeric tolerance, completeness, evidence validity | rule định trước theo field | Sai số số học, độ phủ và quote grounding | Phù hợp schema dự án; tolerance là lựa chọn protocol, không phải chuẩn phổ quát | Protocol dự án |
| Baseline-vs-Agent throughput/lag/latency/error/recovery | cùng workload, seed, resource và duration | Lợi ích nhân quả của chức năng Agent | TEVV phải lặp lại, có benchmark và ghi giới hạn | [R11] |

Các ngưỡng 10% RAM, lag 1.000, mean 5 giây hoặc mức rate là cấu hình operator/
safety, không phải hằng số khoa học do các nguồn trên quy định. Chúng chỉ được coi
là capacity threshold khi có load test/SLO phù hợp. `latency_p95_seconds` cũng không
được gọi là offered-demand latency; phải đọc cùng deferred/rejected và served fraction.

## 6. Cấu hình và điều kiện vận hành

| Phần | Yêu cầu cấu hình | Trạng thái |
| --- | --- | --- |
| Model chính | `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`; model có chat completions trả JSON | Đã kiểm chứng model thật; credential được quản lý qua biến môi trường, quyền truy cập model phụ thuộc tài khoản |
| Model điều tiết riêng | Nhóm `CONTROL_LLM_*`; để trống thì kế thừa nhóm chính theo router | Có cơ chế; khai báo trọn nhóm khi dùng endpoint/tài khoản khác |
| Model dự phòng | `LLM_FALLBACK_*` hoặc `CONTROL_LLM_FALLBACK_*`, key và quyền dùng model tương ứng | Fallback cùng provider đã thử; route miễn phí/quota độc lập chưa được xác nhận |
| Producer/trainer nhận policy | `CONTROL_ENABLED`, source topic, policy path/mount và lease thống nhất | Đã chạy tải nhỏ; một topic được điều tiết mỗi lần, không tự bật crawler hoặc tạo workload |
| Điều chỉnh tài nguyên | `CONTROL_RESOURCE_ENABLED`, Docker CLI trên host, quota gốc khôi phục được, ngân sách CPU/RAM | Công cụ chỉ cho 3 processor và trainer; không sửa tùy ý service hoặc replica |
| Thực nghiệm dài | Kafka/Mongo/worker chạy, đủ RAM host lẫn VM, telemetry hợp lệ, publisher/scheduler được cô lập | Phiên synthetic hai giờ đã hoàn tất 14/11 |
| Thực nghiệm quá tải LLM | Cùng seed/workload/resource/training, có quyết định LLM và offered demand còn cao | Chưa có cặp baseline/LLM hợp lệ; hạ rate không xóa footprint Mongo/Kafka và backlog |

Cấu hình tham chiếu: [.env.example](../.env.example),
[LLM_FEEDBACK_CONTROL](LLM_FEEDBACK_CONTROL.md),
[LLM_OVERLOAD_EXPERIMENT](LLM_OVERLOAD_EXPERIMENT.md).
“Miễn phí” là điều kiện của tài khoản/provider; router không bảo đảm model luôn
miễn phí, không tự mở billing và không biến cùng một key thành quota độc lập.

## 7. Các bản sửa đã hoàn thành

| Bản sửa | Commit | Kiểm chứng và phần còn lại |
| --- | --- | --- |
| Parser Docker nhận số dạng `1e+03kB`, giữ đúng đơn vị và từ chối số sai | `f02563d` | 72 test telemetry/controller đạt; lấy lại 3 mẫu telemetry thật không lỗi parser. [06/10](evidence/LLM_FEEDBACK_VALIDATION_20261006.md) |
| Retry có giới hạn khi Windows tranh chấp file policy | `e972055` | [runtime_policy.py](../agents/runtime_policy.py), [test_runtime_policy.py](../utils/tests/test_runtime_policy.py), [14/10](evidence/LLM_OVERLOAD_VALIDATION_20261014.md) |
| Observer commit: timeout request 5 s độc lập chu kỳ poll, lưu lỗi hợp lệ | `e5c5664` | 47 test liên quan đạt theo lượt sửa; lỗi observer vẫn làm phép thử invalid. [llm_load.py](../research/llm_load.py), [test_llm_load.py](../utils/tests/test_llm_load.py) |
| Đọc lịch sử price anomaly một lượt, tránh giữ toàn bộ document | `acb8803` | 23 test anomaly/enrichment đạt; vẫn giữ percentile chính xác và đóng cursor. [price_anomaly.py](../processing/price_anomaly.py), [test_price_anomaly.py](../utils/tests/test_price_anomaly.py). Chưa đo giảm RAM Docker |
| Ghi hash dependency thực sự đang chạy trong worker | `6bc14df` | 22 test đạt; một Docker call/worker, giữ field cũ và báo lỗi rõ nếu không đọc được. [run_experiment.py](../research/run_experiment.py), [test_worker_identity.py](../utils/tests/test_worker_identity.py) |

Full suite ngày 20/11 đạt **1.422 tests**; Python compileall và Compose config đạt.
Frontend type-check/ESLint/build/HTTP preview và promtool không đổi từ lượt 18/11,
trong đó promtool đã xác nhận Prometheus config cùng **10 alert rules** hợp lệ.
Có 5.617 warning dependency/deprecation/runtime đã biết, không có test fail.
[Validation 20/11](evidence/project-validation-20261120.json),
[validation 18/11](evidence/project-validation-20261118.json),
[ops revalidation](evidence/ops-validation-20261118.json).
Việc xác minh bản triển khai live vẫn cần đối chiếu dependency hashes trong
container với phiên bản mã nguồn cần kiểm chứng.

## 8. Hạng mục còn lại và tiêu chí nghiệm thu

- [ ] **Triển khai và xác minh bản sửa worker:** container dùng đúng image/mã, hash `price_anomaly.py` và `feature_engineering.py` khớp bản cần thử; đo RAM thực tế trước khi kết luận giảm bộ nhớ.
- [ ] **Chứng minh điều tiết LLM quá tải:** ít nhất 3 cặp hợp lệ, có cả thứ tự baseline→LLM và LLM→baseline, cùng seed/workload/allocation/training. Model phải thật sự giảm rate; ingress/lag/latency phục hồi với throughput dương **khi offered demand vẫn cao**; báo cáo cả lỗi, deferred và rejected. Guard/drain không được nhận công cho LLM.
- [ ] **Chứng minh LLM tự đổi allocation:** có quyết định model, receipt Docker+Compose và phép đo hiệu quả; đây là thử nghiệm riêng vì bài đối chứng rate hiện cố định resource limits.
- [ ] **Mở rộng kiểm chứng lifecycle:** stop/start cùng container và tải nhỏ sau start đã đạt ngày 14/11; ngày 18/11 thêm một chu kỳ predictor stop/start cùng-ID và isolated trainer probe quan sát ba ACK. Còn deploy/recreate trainer production với image mới, lưu ACK durable khi fit đang chạy, ba chu kỳ lặp/continuous operation, tác động RAM nhân quả và ổn định overload; probe/script không được nhận công cho LLM.
- [ ] **Chạy training dưới điều tiết:** kiểm chứng hoãn/cho phép và fit hoàn tất, theo dõi stream trong lúc fit; đo chi phí tài nguyên thay vì chỉ quan sát `training_allowed=true`.
- [ ] **Hoàn tất fallback quota/vận hành:** cấu hình route được tài khoản cho phép; xác nhận khả năng chuyển khi quota hết, giới hạn chi phí và giới hạn quota dùng chung. Test mô phỏng chỉ xác minh logic; chỉ ghi nhận failover quota thực tế khi có lỗi quota và response fallback thật. Không cần cố tình tiêu hết quota để thử.
- [ ] **Mở rộng forecast:** 5/10 phút đã đạt gate offline trên một run synthetic hai giờ; readiness gate fail-closed vì manifest lịch sử chưa bind support/split, thiếu capacity calibration và miền tải phù hợp. Còn re-export artifact có provenance binding, dữ liệu độc lập nhiều ngày/tự nhiên, experimental forecast controller và ba cặp AB/BA. Chỉ nối production sau trial đạt.
- [ ] **Đánh giá chất lượng model giá:** redaction target, dedup, conservative property-group split, dataset/split hash, hyperparameters, predictions và clean diagnostic đã có evidence. Còn đánh giá heuristic entity grouping bằng nhãn property, cross-validation hoặc tập thời gian/nguồn độc lập và protocol chọn model không dùng test trước khi deploy.
- [ ] **Đánh giá extraction trên dữ liệu đại diện:** protocol/schema/evaluator và panel 34 record đã có; còn hai người thật gán nhãn, adjudicator độc lập, đánh giá representativeness và chạy benchmark. Một fixture Gemini không đủ.

## 9. Danh mục bằng chứng

| Nội dung | Bằng chứng đọc được trên Git | Raw artifact trên máy chạy, ngoài Git |
| --- | --- | --- |
| Gemini extraction thật | Phụ lục A dưới đây | [gemini-clean-audit.json](../runtime/research/gemini-clean-audit.json), [validation-success.json](../runtime/research/gemini-verification-runtime/validation-success.json) |
| Audit/benchmark giá | [Kết quả kiểm định và benchmark](#43-benchmark-mô-hình-giá-và-kiểm-định-dữ-liệu), [MdAPE cũ](evidence/price-benchmark-mdape-20261117.json), [dataset/split sạch](evidence/price-dataset-preparation-20261118.json), [grouped metrics](evidence/price-benchmark-grouped-20261118.json) | [data audit](../runtime/research/data-audit/audit.json), grouped metrics/split/predictions/chart ở `runtime/research/price-grouped-20261118/` |
| Controller theo luật | [Kết quả paired-v4](#42-kết-quả-controller-theo-luật) | [paired-v4 summary](../runtime/research/paired-v4-20260921/analysis/summary.json), observations/actions/biểu đồ trong cùng thư mục |
| Docker apply/rollback | [06/10](evidence/LLM_FEEDBACK_VALIDATION_20261006.md) | [resource.apply.json](../runtime/control-validation/20261006-01/resource.apply.json), [rollback.json](../runtime/control-validation/20261006-01/rollback.json) |
| LLM và fallback thật | [07/10](evidence/LLM_FEEDBACK_VALIDATION_20261007.md), [JSON tổng hợp](evidence/llm-feedback-20261007.json) | [summary lượt 02](../runtime/control-validation/20261007-02/summary.json), [summary lượt 03](../runtime/control-validation/20261007-03/summary.json), provider/live logs cùng thư mục |
| LLM container lifecycle | [Lượt 05/11](evidence/container-lifecycle-live-20261105.json), [stop/start và recovery 14/11](evidence/container-lifecycle-recovery-20261114.json) | Raw result/decisions/samples/receipts ở `runtime/diagnostics/container-lifecycle-*`; hash ghi trong summary Git |
| Các lần overload chưa hoàn thành | [13/10](evidence/LLM_OVERLOAD_VALIDATION_20261013.md), [14/10](evidence/LLM_OVERLOAD_VALIDATION_20261014.md), [19/10](evidence/LLM_OVERLOAD_VALIDATION_20261019.md), [14/11](evidence/llm-overload-20261114.json), [18/11](evidence/llm-overload-20261118.json), [20/11](evidence/llm-overload-20261120.json) | Raw calibration/session ở `runtime/research/llm-proof-*`; chưa có cặp A/B LLM hợp lệ hoặc quyết định LLM trong overload |
| Kiểm kê và phiên telemetry | [23/10](evidence/TELEMETRY_VALIDATION_20261023.md), [24/10](evidence/TELEMETRY_VALIDATION_20261024.md), [phiên 2h 14/11](evidence/telemetry-session-20261114.json) | Session/observations/history audit ở `runtime/research/traffic-sessions*`; hash ghi trong summaries Git |
| Forecast 5/10 phút | [Đánh giá cũ 24/10](evidence/TELEMETRY_VALIDATION_20261024.md), [đánh giá phiên 2h 17/11](evidence/telemetry-forecast-2h-20261117.json), [predictive readiness gốc 18/11, re-evaluate 20/11](evidence/predictive-control-readiness-20261118.json) | Metrics, predictions, split và model trong `runtime/research/telemetry-evaluation-20261024/` và `runtime/research/forecast-2h-20261117/`; control trial chưa bắt đầu |
| Extraction evaluation readiness | [Protocol](EXTRACTION_EVALUATION.md), [schema nhãn](templates/extraction-ground-truth.schema.json), [panel/manifest](evidence/extraction-panel-20261118/manifest.json), [evaluator](../research/extraction_evaluation.py) | Hai reviewer/adjudicator chưa điền nhãn; chưa có metrics accuracy đại diện |
| Ops revalidation 18/11 | [Promtool/drain/lifecycle scope](evidence/ops-validation-20261118.json) | Raw lifecycle receipt ở `runtime/control/lifecycle-repeat-probe/`; drain ACK chỉ console capture, không có claim continuous/causal RAM |
| Demo công khai | [SystemDemo](../frontend/src/components/SystemDemo.tsx), [evidence constants](../frontend/src/data/projectEvidence.ts) | Hiển thị artifact tĩnh đã gắn nhãn; không giả làm live dashboard |
| Validation 18/11 | [Command/results summary](evidence/project-validation-20261118.json), [ops follow-up](evidence/ops-validation-20261118.json) | Full suite snapshot, frontend, compile/config/JSON, preview/link checks và promtool follow-up đạt |
| Validation 20/11 | [Final code/safety checks](evidence/project-validation-20261120.json) | Full suite 1.422 tests; predictive gate fail-closed và overload calibration bị RAM guard dừng an toàn |

Mỗi bộ bằng chứng gắn với ngày chạy, phiên bản mã và phạm vi thí nghiệm cụ thể.
Artifact trong `runtime/` được lưu cục bộ; các báo cáo và số liệu tổng hợp trong
repository phục vụ đối chiếu kết quả giữa các phiên bản.

## 10. Đánh giá Agent và mức cải thiện

| Chức năng | Thành phần quyết định | Baseline phù hợp | Trạng thái evidence |
| --- | --- | --- | --- |
| Extraction | LLM provider/router; schema/evidence/merge là code | Cùng panel adjudicated, không extraction hoặc provider/model khác | Panel 34 record đang chờ hai reviewer/adjudicator; accuracy chưa kiểm chứng |
| Stress generation | Generator có seed/rule, không phải LLM control | Không áp dụng improvement hệ thống | Implemented/tested |
| Load classification/rate/training permission | LLM đề xuất; guard giới hạn | Fixed-rate hoặc cùng controller không LLM | Tải nhỏ đã chạy; overload A/B chưa đạt |
| Resource allocation | LLM có schema đề xuất; actuator deterministic | Cùng workload và allocation cố định | Tool thật đã chạy; allocation do người chọn |
| Container lifecycle | LLM chọn stop/start; guard/actuator thi hành | Cùng telemetry/workload, lifecycle disabled | Functional recovery đạt; không có A/B overload |
| Workload forecast | ML offline, chưa phải Agent controller | Persistence cho forecast; reactive controller cho control trial | 5/10 phút đạt trên một run synthetic; readiness gate bị chặn, chưa có control benefit |

Kết quả paired-v4 theo luật giảm peak lag 74,88% và peak p95 65,74%, nhưng
committed throughput thấp hơn và rejected tăng mạnh. Đây **không phải** phần trăm
cải thiện của LLM. Lượt lifecycle 14/11 cũng không có baseline tương đương, nên
không tính improvement %. Khi có cặp hợp lệ, metric “thấp hơn tốt hơn” dùng
`(baseline − agent) / baseline × 100%`; metric “cao hơn tốt hơn” dùng
`(agent − baseline) / baseline × 100%`. Không tính khi baseline bằng 0 và luôn
báo cáo offered, admitted, committed, backlog, deferred/rejected cùng nhau.

## 11. Hướng dẫn demo bảo vệ

1. Mở frontend chưa đăng nhập: trang mặc định là **Demo vận hành có bằng chứng**.
2. Đi theo flow nguồn dữ liệu → Kafka → processor → MongoDB/model; giải thích Kafka
   là hàng đợi giúp tách tốc độ nhận và xử lý.
3. Giới thiệu bốn tín hiệu bằng tiếng Việt: lưu lượng, p95/p99, lỗi, bão hòa; nhấn
   mạnh RAM host/VM/container có mẫu số khác nhau.
4. Trình bày timeline 14/11: LLM chọn stop, guard kiểm tra, LLM fallback chọn start,
   ba cửa sổ tải nhỏ sạch. Nói rõ đây là functional trial, không phải overload proof.
5. Mở Grafana `Real Estate Pipeline`: xem throughput, per-partition lag sample và
   p95/p99 admitted-to-commit. Không gọi gauge lag này là total consumer-group lag.
6. So sánh paired-v4 rule controller và đọc cả throughput/rejected để tránh kết luận
   “giảm lag = tăng hiệu năng”.
7. Kết thúc bằng hai cột “đã có bằng chứng/chưa thể kết luận”, sau đó mới đăng nhập
   khu vực dự đoán giá nếu cần demo API/model.

Các số trong demo là snapshot từ evidence Git và đều ghi rõ synthetic/local scope;
chúng không tự cập nhật như live telemetry. Nếu cần live control, chạy theo runbook
và chỉ trình bày kết quả sau khi artifact hoàn tất, không thay số tĩnh bằng số giả.

## 12. References

- **[R1]** Rob Ewaschuk; edited by Betsy Beyer (2016), “Monitoring Distributed
  Systems,” trong *Site Reliability Engineering: How Google Runs Production
  Systems*, O'Reilly. [Official Google SRE chapter](https://sre.google/sre-book/monitoring-distributed-systems/).
  Nguồn cho Four Golden Signals, histogram và tail latency/p99; trang không công bố DOI.
- **[R2]** Brendan Gregg (2012; trang cập nhật 2017), “The USE Method.”
  [Official page](https://www.brendangregg.com/usemethod.html). Định nghĩa
  utilization, saturation, errors và áp dụng cho container/cgroup; không có DOI.
- **[R3]** Alejandro Forero Cuervo (2016), “Handling Overload,” trong Google SRE
  Book. [Official chapter](https://sre.google/sre-book/handling-overload/).
  Hỗ trợ phân biệt attempted requests và accepted requests.
- **[R4]** Mike Ulrich (2016), “Addressing Cascading Failures,” trong Google SRE
  Book. [Official chapter](https://sre.google/sre-book/addressing-cascading-failures/).
  Nguồn cho load shedding, queue/resource limits và useful work khi overload.
- **[R5]** Prometheus project, “Histograms and summaries,” truy cập 18/11/2026.
  [Official documentation](https://prometheus.io/docs/practices/histograms/).
  Nguồn cho `histogram_quantile`, aggregation và sai số xấp xỉ bucket.
- **[R6]** Apache Kafka project, “Monitoring,” Kafka 4.1.
  [Official documentation](https://kafka.apache.org/41/operations/monitoring/).
  Nguồn cho message/byte rate, request queue/time và consumer lag/fetch rate.
- **[R7]** scikit-learn developers, “Regression metrics,” version 1.9.1.
  [Official documentation](https://scikit-learn.org/stable/modules/model_evaluation.html#regression-metrics).
  Định nghĩa/caveat cho MAE, RMSE, R² và percentage errors; không có DOI.
- **[R8]** J. Scott Armstrong & Fred Collopy (1992), “Error Measures for
  Generalizing About Forecasting Methods: Empirical Comparisons,” *International
  Journal of Forecasting* 8(1), 69–80.
  [DOI: 10.1016/0169-2070(92)90008-W](https://doi.org/10.1016/0169-2070(92)90008-W).
  Tham khảo cho median percentage error; không tự chứng minh phù hợp với dataset này.
- **[R9]** Rob J. Hyndman & Anne B. Koehler (2006), “Another look at measures of
  forecast accuracy,” *International Journal of Forecasting* 22(4), 679–688.
  [DOI: 10.1016/j.ijforecast.2006.03.001](https://doi.org/10.1016/j.ijforecast.2006.03.001).
  Nguồn cho đánh giá out-of-sample và caveat của scale/percentage metrics.
- **[R10]** Erik F. Tjong Kim Sang & Fien De Meulder (2003), “Introduction to the
  CoNLL-2003 Shared Task: Language-Independent Named Entity Recognition,” pp. 142–147.
  [ACL Anthology W03-0419](https://aclanthology.org/W03-0419/). Nguồn cho held-out
  precision/recall/F1; ACL record không công bố DOI.
- **[R11]** Elham Tabassi (2023), *Artificial Intelligence Risk Management
  Framework (AI RMF 1.0)*, NIST AI 100-1.
  [DOI: 10.6028/NIST.AI.100-1](https://doi.org/10.6028/NIST.AI.100-1).
  Nguồn cho TEVV lặp lại, test set/metric được ghi tài liệu, benchmark và giới hạn.
- **[R12]** Jeffrey Dean & Luiz André Barroso (2013), “The Tail at Scale,”
  *Communications of the ACM* 56(2), 74–80.
  [DOI: 10.1145/2408776.2408794](https://doi.org/10.1145/2408776.2408794).
  Nguồn cho tầm quan trọng của tail latency, không quy định percentile duy nhất.

## Phụ lục A — Bằng chứng Gemini ngày 24/09/2026

Phụ lục ghi lại INPUT/OUTPUT và quy trình kiểm chứng ngày **24/09/2026**.
Tên model, cấu hình sau khôi phục và số test tương ứng với thời điểm này.
Cơ chế model router và chuyển dự phòng được trình bày tại
[mục 1.3](#13-model-và-chuyển-dự-phòng), kèm bằng chứng ngày 07/10.

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
- Sau lượt **24/09**, hệ thống đã khôi phục `AI_STRESS_ENABLED=false`, bỏ audit
  event filter, giữ `AI_FALLBACK_ENABLED=false`, `AI_ENABLED=true` và trả
  `LLM_MODEL` runtime về giá trị khi đó là **`gemini-2.5-flash`**. Vì vậy lượt
  này là kiểm chứng bằng override `gemini-3.6-flash`, không phải bằng chứng cấu
  hình production đã đổi ngay trong ngày 24/09. Cấu hình mẫu Git hiện tại dùng
  `gemini-3.6-flash`; lịch sử fallback ngày 07/10 và lifecycle ngày 14/11 được
  ghi riêng ở mục 1.3/4.1. Bằng chứng trạng thái trước/sau của lượt 24/09 tại
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
