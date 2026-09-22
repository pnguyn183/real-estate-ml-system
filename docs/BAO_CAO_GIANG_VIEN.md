# Báo cáo trả lời câu hỏi giảng viên

> Phạm vi: kết quả kiểm tra dữ liệu, benchmark model giá bất động sản và stress test Kafka/adaptive rate limiting. Các kết luận bên dưới dùng artifact đã lưu, không suy diễn ngoài phạm vi thí nghiệm.

## 1. Tóm tắt kết luận

- Chưa thể xác minh đầy đủ chất lượng dữ liệu sau Gemini vì repository không lưu prompt, phiên bản model, dữ liệu trước/sau, các bản ghi bị loại và log provenance của Gemini.
- Dữ liệu có lệch mạnh ở giá, diện tích và một số biến số; đây là skew thống kê, chưa đồng nghĩa mọi outlier đều sai.
- Trên cùng một holdout, Random Forest và XGBoost tăng R² so với model hiện tại; Gradient Boosting giảm R². Đây là benchmark chẩn đoán, chưa deploy model mới.
- Trong stress test, leader ingress của 3 Kafka broker gần như đều nhau, khoảng 33% mỗi broker. Tuy nhiên CPU broker 2 cao hơn rõ rệt, nên không thể nói toàn bộ tải tài nguyên là cân bằng.
- Cơ chế đã triển khai là adaptive admission control/rate limiting, không phải tự động scale số replica hoặc CPU. Khi phát hiện nghẽn, controller đổi limit mới qua rate gate.
- Với run v4, phát hiện sau khoảng 0,000986 giây kể từ sample đã hoàn tất và xác nhận thay đổi limit sau khoảng 0,000498 giây. Tính từ timestamp Kafka sample đến quyết định khoảng 2,111 giây do thời gian thu thập telemetry.
- Mức cao nhất đạt tiêu chí ổn định trong run v4 là 500 messages/s ở baseline và 250 messages/s ở adaptive. Chưa đủ bằng chứng để gọi đây là điểm tối ưu tuyệt đối.
- Thuật toán giá hiện tại là VotingRegressor gồm Ridge, HistGradientBoostingRegressor và SGDRegressor, dùng log1p/expm1 cho target. Phần traffic controller là luật phản ứng dựa trên lag, latency, CPU/RAM và error rate; traffic forecast 5/10 phút chưa có đủ dữ liệu hợp lệ.

## 2. Câu hỏi 1: Dữ liệu sau Gemini đã được clean kỹ chưa?

### Trả lời

Chưa thể kết luận là đã clean kỹ sau Gemini. Repository không có đủ bằng chứng provenance để kiểm tra Gemini đã làm gì với dữ liệu. Có thể xác nhận pipeline hiện tại có validation, chuẩn hóa kiểu dữ liệu, kiểm tra miền giá trị, lọc record không phù hợp với training query và kiểm tra anomaly; nhưng các bước này không chứng minh được dữ liệu đã được Gemini làm sạch.

Các bằng chứng còn thiếu:

- prompt và cấu hình Gemini;
- model/version và thời điểm chạy;
- dữ liệu raw trước khi clean;
- dữ liệu sau khi clean;
- danh sách record bị sửa, xóa hoặc từ chối;
- log lý do xử lý theo từng record.

### Bằng chứng

- [docs/LECTURER_RESEARCH_REPORT.md](LECTURER_RESEARCH_REPORT.md#L69-L73)
- [processing/llm_review.py](../processing/llm_review.py)
- [agents/extraction.py](../agents/extraction.py)
- [agents/results.py](../agents/results.py)
- [runtime/research/data-audit/audit.json](../runtime/research/data-audit/audit.json)

## 3. Câu hỏi 2: Dữ liệu có bị lệch không?

### Trả lời

Có. Dữ liệu lệch mạnh ở một số biến chính:

| Biến | Median | P99 | Maximum | Skewness |
|---|---:|---:|---:|---:|
| Giá | 7,20 tỷ VND | 150 tỷ VND | 450 tỷ VND | 7,021 |
| Diện tích | 94,5 m² | 2.549,19 m² | 10.000 m² | 9,871 |
| Giá/m² | 84,0 triệu VND | 580,27 triệu VND | 2,099 tỷ VND | 3,733 |
| Phòng ngủ | 3 | 17 | 50 | 5,944 |
| Phòng tắm | 3 | 17 | 47 | 5,221 |
| Mặt tiền | 5 m | 40 m | 220 m | 11,377 |
| Đường trước nhà | 8 m | 42 m | 300 m | 8,581 |

Một số missingness đáng chú ý:

- bedrooms thiếu 52,44% ở feature records;
- bathrooms thiếu 54,79%;
- floors thiếu 63,58%;
- latitude/longitude thiếu 100%;
- province/district/ward slug thiếu khoảng 99%;
- 687 giá trị giá là IQR outlier, nhưng không tự động kết luận là dữ liệu sai.

Kết luận trình bày: dữ liệu có skew và missingness lớn; pipeline đang dùng median imputation, one-hot encoding và log-transform target, nhưng các biện pháp này không sửa được semantic sai hoặc giá trị nguồn sai. Cần giữ outlier để kiểm tra nghiệp vụ thay vì xóa tự động.

### Bằng chứng

- [docs/LECTURER_RESEARCH_REPORT.md](LECTURER_RESEARCH_REPORT.md#L104-L145)
- [runtime/research/data-audit/audit.json](../runtime/research/data-audit/audit.json)
- Biểu đồ: [price_vnd_distribution.png](../runtime/research/data-audit/charts/price_vnd_distribution.png), [area_m2_distribution.png](../runtime/research/data-audit/charts/area_m2_distribution.png), [missing_values.png](../runtime/research/data-audit/charts/missing_values.png)

## 4. Câu hỏi 3: Random Forest, Gradient Boosting, XGBoost có làm R² tăng không?

Các model được chạy trên cùng 3.984 train / 996 test, cùng preprocessing, cùng log target và cùng random seed 42.

| Model | R² | MAE (tỷ VND) | RMSE (tỷ VND) | So với model hiện tại |
|---|---:|---:|---:|---:|
| Existing VotingRegressor | 0,488991 | 4,616007 | 14,192591 | baseline |
| Random Forest | 0,510214 | 4,583648 | 13,894742 | tăng +0,021223 |
| Gradient Boosting | 0,446412 | 4,910597 | 14,772045 | giảm -0,042579 |
| XGBoost | 0,592611 | 4,261827 | 12,672202 | tăng +0,103620 |

### Kết luận

Random Forest và XGBoost tăng R² trên holdout này; XGBoost tốt nhất. Gradient Boosting không cải thiện. Tuy nhiên đây chỉ là một holdout, dữ liệu còn leakage từ text chứa giá, có duplicate nội dung giữa train/test và chưa có confidence interval/hyperparameter search. Vì vậy chưa nên tuyên bố XGBoost chắc chắn tốt hơn trong production hoặc tự động thay model hiện tại.

### Bằng chứng

- [runtime/research/legacy-benchmark/metrics.json](../runtime/research/legacy-benchmark/metrics.json)
- [runtime/research/legacy-benchmark/algorithm_comparison.png](../runtime/research/legacy-benchmark/algorithm_comparison.png)
- [research/benchmark.py](../research/benchmark.py)

## 5. Câu hỏi 4: Ba Kafka broker có phân phối tải đều trong stress test không?

### Trả lời

Có, xét riêng **leader ingress của stress topic**. Mỗi broker nhận gần một phần ba message. Nhưng xét CPU thì không đều hoàn toàn.

| Mode | Broker | Ingress share | Mean CPU (% một core) | Mean RAM (MiB) |
|---|---:|---:|---:|---:|
| Baseline | 1 | 33,232% | 15,306 | 858,22 |
| Baseline | 2 | 33,308% | 31,798 | 1.071,17 |
| Baseline | 3 | 33,460% | 15,253 | 880,76 |
| Adaptive | 1 | 33,101% | 17,055 | 1.118,13 |
| Adaptive | 2 | 33,156% | 31,370 | 1.177,69 |
| Adaptive | 3 | 33,742% | 18,205 | 1.107,46 |

Chỉ số cân bằng ingress:

| Mode | Max/mean | Coefficient of variation |
|---|---:|---:|
| Baseline | 1,003798 | 0,002845 |
| Adaptive | 1,012267 | 0,008700 |

Cách diễn giải: message ingress được phân phối rất đều giữa 3 leader partition, nhưng broker 2 có CPU khoảng gấp đôi broker 1/3 ở baseline. Vì telemetry hiện tại tính leader log-offset growth, không phải JMX request/byte counter, kết luận chính xác là **ingress cân bằng, resource load chưa cân bằng hoàn toàn**.

### Bằng chứng

- [runtime/research/paired-v4-20260921/analysis/summary.json](../runtime/research/paired-v4-20260921/analysis/summary.json)
- [runtime/research/paired-v4-20260921/analysis/broker_load.png](../runtime/research/paired-v4-20260921/analysis/broker_load.png)
- [runtime/research/paired-v4-20260921/baseline/observations.jsonl](../runtime/research/paired-v4-20260921/baseline/observations.jsonl)
- [runtime/research/paired-v4-20260921/adaptive/observations.jsonl](../runtime/research/paired-v4-20260921/adaptive/observations.jsonl)
- [research/telemetry.py](../research/telemetry.py)

## 6. Câu hỏi 5: Khi nghẽn, bao lâu hệ thống tự set limit mới?

Trong adaptive run v4 có một congestion episode:

| Mốc | Ý nghĩa | Thời gian đo được |
|---|---|---:|
| T0 | Sample đầu tiên xác nhận lag vượt ngưỡng, lag 296 > 200 | 10:05:06,133672 |
| T1 | Controller phát hiện và ra quyết định | 10:05:06,134658 |
| T2 | Rate gate xác nhận đổi limit 1.000 -> 700 msg/s | 10:05:06,135156 |
| T3 | Hoàn tất continuous safe window | 10:06:26,151915 |

| Khoảng thời gian | Kết quả |
|---|---:|
| Detection: T1 - T0 | 0,000986 giây |
| Adjustment: T2 - T1 | 0,000498 giây |
| Recovery: T3 - T2 | 80,016758 giây |
| Tổng T0 - T3 | 80,018243 giây |

Cần trình bày đúng giới hạn: 0,000986 giây là thời gian xử lý quyết định sau khi telemetry sample đã hoàn tất, không phải thời gian từ lúc nghẽn vật lý bắt đầu. Do collector lấy mẫu khoảng 5 giây, thời gian từ Kafka-source timestamp đến quyết định khoảng 2,111 giây. Các lần giảm limit tiếp theo là 1.000 -> 700 -> 490 -> 343 -> 240,10 -> 168,07 msg/s.

Recovery 80 giây xảy ra trong pha drain sau khi tải dừng; chưa chứng minh recovery ổn định trong khi tải cao vẫn tiếp tục.

### Bằng chứng

- [runtime/research/paired-v4-20260921/adaptive/report.json](../runtime/research/paired-v4-20260921/adaptive/report.json)
- [runtime/research/paired-v4-20260921/adaptive/actions.jsonl](../runtime/research/paired-v4-20260921/adaptive/actions.jsonl)
- [research/telemetry.py](../research/telemetry.py)
- [agents/traffic_control.py](../agents/traffic_control.py)

## 7. So sánh trước và sau khi agent điều chỉnh limit

Run v4 dùng cùng seed, cùng workload profile, cùng topic stress cô lập và cùng ba processor. Baseline chạy fixed limit; adaptive cho phép controller thay đổi admission limit.

| Chỉ số | Trước: baseline | Sau: adaptive | Thay đổi |
|---|---:|---:|---:|
| Mean committed throughput | 214,172 msg/s | 184,958 msg/s | -29,214 |
| Mean accepted rate | 267,478 msg/s | 187,312 msg/s | -80,166 |
| Mean CPU | 10,187% | 11,198% | +1,011 điểm % |
| Mean RAM | 47,381% | 57,140% | +9,759 điểm % |
| Mean Kafka lag | 680,750 | 449,459 | -231,291 |
| Mean p95 latency | 1,227 s | 1,956 s | +0,730 s |
| Peak Kafka lag | 11.318 | 2.843 | giảm 74,88% |
| Peak p95 latency | 28,456 s | 9,750 s | giảm 65,74% |
| Error fraction | 0% | 0% | không đổi |
| Rejected messages | 154 | 35.653 | agent chủ động chặn tải |

### Diễn giải

Agent giảm peak lag và peak latency bằng cách giảm lượng traffic được phép vào Kafka, nhưng không làm throughput trung bình tăng và cũng không làm CPU/RAM trung bình giảm trong run này. Vì vậy bằng chứng hiện tại chứng minh **rate limiting có tác dụng bảo vệ hệ thống khi nghẽn**, chưa chứng minh **auto-scaling làm tăng capacity** hay tối ưu toàn diện.

Các biểu đồ cần trình bày:

- [throughput.png](../runtime/research/paired-v4-20260921/analysis/throughput.png)
- [kafka_lag.png](../runtime/research/paired-v4-20260921/analysis/kafka_lag.png)
- [latency.png](../runtime/research/paired-v4-20260921/analysis/latency.png)
- [rate_limit.png](../runtime/research/paired-v4-20260921/analysis/rate_limit.png)
- [input_vs_throughput.png](../runtime/research/paired-v4-20260921/analysis/input_vs_throughput.png)
- [agent_actions.png](../runtime/research/paired-v4-20260921/analysis/agent_actions.png)

## 8. Mục tiêu auto-scaling, rate limiting và điểm chịu tải

### Cơ chế đã có

- Observe: đọc lag, throughput, latency, CPU/RAM và error rate.
- Assess: so sánh với threshold và kiểm tra dữ liệu thiếu/stale.
- Decide: giảm hoặc tăng admission limit trong giới hạn cấu hình.
- Act: cập nhật token-bucket rate gate.
- Measure: ghi actions, acknowledgements, observations và recovery episode.

### Cơ chế chưa có

Chưa có actuator tự động tăng/giảm số processor replicas, broker resources hoặc Docker CPU/memory. Vì vậy không nên báo cáo rằng project đã chứng minh auto-scaling tài nguyên. Cách gọi chính xác là **adaptive admission control / rate limiting agent**.

### Mức chịu tải quan sát được

| Requested plateau | Baseline | Adaptive |
|---:|---|---|
| 10 msg/s | Stable | Stable |
| 40 msg/s | Stable | Stable |
| 100 msg/s | Stable | Stable |
| 250 msg/s | Stable | Stable |
| 500 msg/s | Stable, throughput 500,012 msg/s | Unstable, p95 peak 9,75 s |
| 1.000 msg/s | Inconclusive, safety stop | Unstable, chỉ admit 23,83% |

Kết luận thực nghiệm: run này quan sát được vùng đạt tiêu chí là 10-500 msg/s cho baseline và 10-250 msg/s cho adaptive. Mốc chính xác giữa 500 và 1.000 msg/s chưa được xác định; cần chạy plateau dài hơn, thêm mức trung gian, đổi thứ tự baseline/adaptive và lặp nhiều seed trước khi gọi là điểm tối ưu.

### Bằng chứng

- [runtime/research/paired-v4-20260921/analysis/summary.json](../runtime/research/paired-v4-20260921/analysis/summary.json)
- [runtime/research/paired-v4-20260921/analysis/cpu.png](../runtime/research/paired-v4-20260921/analysis/cpu.png)
- [runtime/research/paired-v4-20260921/analysis/ram.png](../runtime/research/paired-v4-20260921/analysis/ram.png)
- [runtime/research/paired-v4-20260921/analysis/kafka_lag.png](../runtime/research/paired-v4-20260921/analysis/kafka_lag.png)
- [docs/LECTURER_RESEARCH_REPORT.md](LECTURER_RESEARCH_REPORT.md#L440-L480)

## 9. Hôm trước đã dùng thuật toán gì để dự đoán?

Có hai phần cần phân biệt:

### Dự đoán giá bất động sản

Model production hiện tại là `VotingRegressor`, trung bình ba estimator:

- Ridge;
- HistGradientBoostingRegressor;
- SGDRegressor.

Pipeline dùng preprocessing số/categorical/text, TF-IDF và text embedding; target `price_vnd` được train với `log1p` và đưa về VND bằng `expm1`. Artifact hiện tại là [artifacts/models/price_model.joblib](../artifacts/models/price_model.joblib). Code định nghĩa tại [modeling/price_model.py](../modeling/price_model.py#L150-L205).

### Dự đoán traffic/Kafka

Stress controller hiện tại không dùng model ML để dự đoán traffic. Nó là reactive controller dựa trên threshold, hysteresis, cooldown và token bucket. Benchmark forecast 5/10 phút có chạy persistence, Random Forest, Gradient Boosting và XGBoost, nhưng không đủ các cặp dữ liệu chronology-safe nên không báo cáo R² traffic. Không nên nói rằng agent hiện tại đang dùng XGBoost để tự điều chỉnh limit.

## 10. Câu trả lời ngắn khi trình bày

> Em đã kiểm tra dữ liệu bằng audit định lượng nhưng chưa thể xác minh đầy đủ phần Gemini vì thiếu provenance trước/sau. Dữ liệu có skew mạnh và missingness đáng kể. Trên cùng một holdout, Random Forest tăng R² từ 0,489 lên 0,510 và XGBoost lên 0,593, còn Gradient Boosting giảm xuống 0,446; đây mới là benchmark, chưa deploy. Trong stress test, ingress của ba Kafka broker đều khoảng 33%, nhưng CPU broker 2 cao hơn nên chỉ kết luận message ingress cân bằng, không phải toàn bộ resource load cân bằng. Agent phát hiện lag sau khoảng 0,001 giây từ sample hoàn tất và xác nhận limit mới sau khoảng 0,0005 giây; tính cả thời gian thu thập Kafka sample là khoảng 2,1 giây. Cơ chế hiện tại là adaptive rate limiting/admission control, chưa phải auto-scaling replica. Peak lag giảm 74,88% và peak latency giảm 65,74%, nhưng throughput trung bình cũng giảm, nên hiệu quả chính là bảo vệ hệ thống khi nghẽn. Mức stable cao nhất trong run này là 500 msg/s baseline và 250 msg/s adaptive; cần thêm thí nghiệm để xác định điểm tối ưu chính xác.

## 11. Bộ bằng chứng chính

- [docs/LECTURER_RESEARCH_REPORT.md](LECTURER_RESEARCH_REPORT.md)
- [runtime/research/paired-v4-20260921/analysis/summary.json](../runtime/research/paired-v4-20260921/analysis/summary.json)
- [runtime/research/legacy-benchmark/metrics.json](../runtime/research/legacy-benchmark/metrics.json)
- [runtime/research/data-audit/audit.json](../runtime/research/data-audit/audit.json)
- [research/report.py](../research/report.py)
- [research/telemetry.py](../research/telemetry.py)
- [agents/traffic_control.py](../agents/traffic_control.py)
