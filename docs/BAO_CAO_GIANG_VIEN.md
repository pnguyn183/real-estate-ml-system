# Báo cáo kiểm định dữ liệu, mô hình và cơ chế điều tiết tải

> Phạm vi nghiên cứu: kiểm tra dữ liệu đầu vào, benchmark mô hình giá bất động sản và đánh giá stress test Kafka với cơ chế adaptive rate limiting. Kết luận được xây dựng từ các artifact thực nghiệm đã lưu.

## 1. Tóm tắt kết quả

- Cơ chế provenance cho Gemini đã được bổ sung: mỗi AI result được lưu snapshot trước/sau, provider/model, field thay đổi và SHA-256 trong collection `ai_cleaning_audit`. Dữ liệu lịch sử trước thời điểm triển khai cơ chế này chưa có đủ provenance để xác minh.
- Dữ liệu có lệch mạnh ở giá, diện tích và một số biến số; đây là skew thống kê, chưa đồng nghĩa mọi outlier đều sai.
- Trên cùng một holdout, Random Forest và XGBoost tăng R² so với model hiện tại; Gradient Boosting giảm R². Đây là benchmark chẩn đoán, chưa deploy model mới.
- Trong stress test, leader ingress của 3 Kafka broker gần như đều nhau, khoảng 33% mỗi broker. Tuy nhiên CPU broker 2 cao hơn rõ rệt, nên không thể nói toàn bộ tải tài nguyên là cân bằng.
- Cơ chế đã triển khai là adaptive admission control/rate limiting, không phải tự động scale số replica hoặc CPU. Khi phát hiện nghẽn, controller đổi limit mới qua rate gate.
- Với run v4, phát hiện sau khoảng 0,000986 giây kể từ sample đã hoàn tất và xác nhận thay đổi limit sau khoảng 0,000498 giây. Tính từ timestamp Kafka sample đến quyết định khoảng 2,111 giây do thời gian thu thập telemetry.
- Mức cao nhất đạt tiêu chí ổn định trong run v4 là 500 messages/s ở baseline và 250 messages/s ở adaptive. Chưa đủ bằng chứng để gọi đây là điểm tối ưu tuyệt đối.
- Thuật toán giá hiện tại là VotingRegressor gồm Ridge, HistGradientBoostingRegressor và SGDRegressor, dùng log1p/expm1 cho target. Phần traffic controller là luật phản ứng dựa trên lag, latency, CPU/RAM và error rate; traffic forecast 5/10 phút chưa có đủ dữ liệu hợp lệ.

## 2. Kiểm chứng dữ liệu sau Gemini

Cấu hình có API key Gemini, nhưng API key không phải là bằng chứng Gemini đã được gọi hoặc đã làm sạch dữ liệu. Cơ chế mới sử dụng Gemini qua endpoint OpenAI-compatible của `ai-agent`; processor giữ raw record và lưu audit trước/sau trong `ai_cleaning_audit`. Mỗi audit có `event_id`, provider/model, snapshot trước/sau, field thay đổi và SHA-256 để truy nguyên. Cấu hình hiện tại chỉ route các record thiếu field bắt buộc khi `AI_FALLBACK_ENABLED=true`, không tự động gửi toàn bộ dataset. Do đó, chỉ các record có audit tương ứng mới được xác nhận là đã qua Gemini; các record lịch sử không có audit vẫn chưa thể xác minh.

Các bằng chứng còn thiếu:

- prompt và cấu hình Gemini;
- model/version và thời điểm chạy;
- dữ liệu raw trước khi clean;
- dữ liệu sau khi clean;
- danh sách record bị sửa, xóa hoặc từ chối;
- log lý do xử lý theo từng record.

Quy trình tạo artifact audit sau workload:

```powershell
python scripts/audit_gemini_clean.py `
	--output runtime/research/gemini-clean-audit.json `
	--provider openai_compatible
```

`GEMINI_API_KEY` được lưu trong `.env`, và `.env` được khai báo trong `.gitignore`; giá trị bí mật không thuộc phạm vi artifact báo cáo. API key cần được thu hồi và cấp lại nếu đã bị lộ.

### Bằng chứng

- [docs/LECTURER_RESEARCH_REPORT.md](LECTURER_RESEARCH_REPORT.md#L69-L73)
- [processing/llm_review.py](../processing/llm_review.py)
- [agents/extraction.py](../agents/extraction.py)
- [agents/results.py](../agents/results.py)
- [.env.example](../.env.example)
- [scripts/audit_gemini_clean.py](../scripts/audit_gemini_clean.py)
- [runtime/research/data-audit/audit.json](../runtime/research/data-audit/audit.json)

## 3. Phân tích phân phối dữ liệu

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

Kết luận: dữ liệu có skew và missingness lớn. Pipeline sử dụng median imputation, one-hot encoding và log-transform target; các biện pháp này không sửa được sai lệch semantic hoặc giá trị nguồn sai. Các outlier được giữ lại để phân biệt giữa cực trị thống kê và dữ liệu sai theo nghiệp vụ.

### Bằng chứng

- [docs/LECTURER_RESEARCH_REPORT.md](LECTURER_RESEARCH_REPORT.md#L104-L145)
- [runtime/research/data-audit/audit.json](../runtime/research/data-audit/audit.json)
- Biểu đồ: [price_vnd_distribution.png](../runtime/research/data-audit/charts/price_vnd_distribution.png), [area_m2_distribution.png](../runtime/research/data-audit/charts/area_m2_distribution.png), [missing_values.png](../runtime/research/data-audit/charts/missing_values.png)

## 4. Benchmark các thuật toán hồi quy

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

## 5. Phân phối tải giữa ba Kafka broker

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

Kết luận: message ingress được phân phối rất đều giữa ba leader partition, nhưng broker 2 có CPU khoảng gấp đôi broker 1/3 ở baseline. Telemetry hiện tại tính leader log-offset growth, không phải JMX request/byte counter; do đó kết luận là **ingress cân bằng, resource load chưa cân bằng hoàn toàn**.

### Bằng chứng

- [runtime/research/paired-v4-20260921/analysis/summary.json](../runtime/research/paired-v4-20260921/analysis/summary.json)
- [runtime/research/paired-v4-20260921/analysis/broker_load.png](../runtime/research/paired-v4-20260921/analysis/broker_load.png)
- [runtime/research/paired-v4-20260921/baseline/observations.jsonl](../runtime/research/paired-v4-20260921/baseline/observations.jsonl)
- [runtime/research/paired-v4-20260921/adaptive/observations.jsonl](../runtime/research/paired-v4-20260921/adaptive/observations.jsonl)
- [research/telemetry.py](../research/telemetry.py)

## 6. Thời gian phản ứng của cơ chế điều tiết tải

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

Giá trị 0,000986 giây là thời gian xử lý quyết định sau khi telemetry sample đã hoàn tất, không phải thời gian từ lúc nghẽn vật lý bắt đầu. Collector lấy mẫu khoảng 5 giây; thời gian từ Kafka-source timestamp đến quyết định khoảng 2,111 giây. Các lần giảm limit tiếp theo là 1.000 -> 700 -> 490 -> 343 -> 240,10 -> 168,07 msg/s.

Recovery 80 giây xảy ra trong pha drain sau khi tải dừng; chưa chứng minh recovery ổn định trong khi tải cao vẫn tiếp tục.

### Bằng chứng

- [runtime/research/paired-v4-20260921/adaptive/report.json](../runtime/research/paired-v4-20260921/adaptive/report.json)
- [runtime/research/paired-v4-20260921/adaptive/actions.jsonl](../runtime/research/paired-v4-20260921/adaptive/actions.jsonl)
- [research/telemetry.py](../research/telemetry.py)
- [agents/traffic_control.py](../agents/traffic_control.py)

## 7. So sánh trước và sau khi điều chỉnh limit

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

Các biểu đồ kết quả:

- [throughput.png](../runtime/research/paired-v4-20260921/analysis/throughput.png)
- [kafka_lag.png](../runtime/research/paired-v4-20260921/analysis/kafka_lag.png)
- [latency.png](../runtime/research/paired-v4-20260921/analysis/latency.png)
- [rate_limit.png](../runtime/research/paired-v4-20260921/analysis/rate_limit.png)
- [input_vs_throughput.png](../runtime/research/paired-v4-20260921/analysis/input_vs_throughput.png)
- [agent_actions.png](../runtime/research/paired-v4-20260921/analysis/agent_actions.png)

## 8. Cơ chế điều tiết tải và giới hạn chịu tải

### Cơ chế đã có

- Observe: đọc lag, throughput, latency, CPU/RAM và error rate.
- Assess: so sánh với threshold và kiểm tra dữ liệu thiếu/stale.
- Decide: giảm hoặc tăng admission limit trong giới hạn cấu hình.
- Act: cập nhật token-bucket rate gate.
- Measure: ghi actions, acknowledgements, observations và recovery episode.

### Cơ chế chưa có

Chưa có actuator tự động tăng/giảm số processor replicas, broker resources hoặc Docker CPU/memory. Vì vậy cơ chế được đánh giá trong nghiên cứu là **adaptive admission control / rate limiting**, chưa phải autoscaling tài nguyên.

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

## 9. Thuật toán được sử dụng

Có hai phần cần phân biệt:

### Dự đoán giá bất động sản

Model production hiện tại là `VotingRegressor`, trung bình ba estimator:

- Ridge;
- HistGradientBoostingRegressor;
- SGDRegressor.

Pipeline dùng preprocessing số/categorical/text, TF-IDF và text embedding; target `price_vnd` được train với `log1p` và đưa về VND bằng `expm1`. Artifact hiện tại là [artifacts/models/price_model.joblib](../artifacts/models/price_model.joblib). Code định nghĩa tại [modeling/price_model.py](../modeling/price_model.py#L150-L205).

### Dự đoán traffic/Kafka

Stress controller hiện tại không dùng model ML để dự đoán traffic. Đây là reactive controller dựa trên threshold, hysteresis, cooldown và token bucket. Benchmark forecast 5/10 phút có chạy persistence, Random Forest, Gradient Boosting và XGBoost, nhưng chưa đủ các cặp dữ liệu chronology-safe để báo cáo R² traffic. Agent hiện tại không dùng XGBoost để tự điều chỉnh limit.

## 10. Thiết lập và tái lập kiểm chứng Gemini

Thiết lập thực nghiệm sử dụng các biến môi trường sau; API key không được lưu trong repository:

```env
AI_ENABLED=true
AI_FALLBACK_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL=gemini-2.5-flash
GEMINI_API_KEY=your_rotated_key
```

Các service liên quan được khởi động lại sau khi cập nhật cấu hình:

```powershell
docker compose up -d --build ai-agent processor processor-2 processor-3
```

Sau workload, artifact audit được tạo bằng script ở trên. Các record có
`status=success`, provider/model đúng cấu hình và audit tồn tại được xác định là
những record Gemini đã trả kết quả được pipeline chấp nhận. Raw record vẫn được
giữ nguyên trong `listings_raw`; dữ liệu sau validation nằm trong
`training_features` hoặc `invalid_records`.

## 11. Kết luận

Nghiên cứu xác nhận ba kết quả chính. Thứ nhất, dữ liệu có skew và missingness đáng kể; chất lượng Gemini chỉ được xác minh đáng tin cậy đối với các record có audit before/after. Thứ hai, XGBoost và Random Forest cho R² cao hơn model hiện tại trên holdout cố định, nhưng chưa đủ cơ sở để khẳng định khả năng tổng quát hoặc thay thế model production. Thứ ba, adaptive rate limiting làm giảm peak Kafka lag và peak latency bằng cách chủ động từ chối một phần tải; run hiện tại chưa chứng minh autoscaling tài nguyên hoặc tăng throughput trung bình. Mức ổn định cao nhất quan sát được là 500 msg/s ở baseline và 250 msg/s ở adaptive; cần thêm các plateau trung gian, nhiều seed và đảo thứ tự run để xác định điểm tối ưu có tính lặp lại.

## 12. Bộ bằng chứng chính

- [docs/LECTURER_RESEARCH_REPORT.md](LECTURER_RESEARCH_REPORT.md)
- [runtime/research/paired-v4-20260921/analysis/summary.json](../runtime/research/paired-v4-20260921/analysis/summary.json)
- [runtime/research/legacy-benchmark/metrics.json](../runtime/research/legacy-benchmark/metrics.json)
- [runtime/research/data-audit/audit.json](../runtime/research/data-audit/audit.json)
- [research/report.py](../research/report.py)
- [research/telemetry.py](../research/telemetry.py)
- [agents/traffic_control.py](../agents/traffic_control.py)
- [agents/results.py](../agents/results.py)
- [scripts/audit_gemini_clean.py](../scripts/audit_gemini_clean.py)
