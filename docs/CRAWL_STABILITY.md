# Kiểm tra độ ổn định crawl — 2026-09-22

Luồng crawl đã được gia cố để lỗi ở một tin hoặc một nguồn không làm gián đoạn
nguồn còn lại. **Chưa thể cam kết nguồn bên ngoài luôn sẵn sàng:** Homedy vẫn có
lỗi phản hồi lớn bất thường khi tải liên tiếp. Alonhadat hoạt động trong mẫu
kiểm tra nhỏ, chưa phải bằng chứng SLA dài hạn.

## Bằng chứng nguồn thực tế

Kiểm tra dùng HTTP client của dự án, đọc robots trước, giữ nhịp 3–5 giây, giới
hạn dung lượng/thời gian và không ghi Kafka/Mongo. Không đổi danh tính, xoá
cookie để né hạn chế hoặc tăng giới hạn phản hồi.

| Nguồn | Kết quả đọc trực tiếp | Đánh giá |
|---|---|---|
| Alonhadat | 20 đường dẫn; 2/2 trang chi tiết tải và parse được; 1 nhà ở hợp lệ, 1 khách sạn/thương mại được loại khỏi dữ liệu huấn luyện đúng quy tắc | Dùng được trong mẫu kiểm tra; tiếp tục theo dõi |
| Homedy | 18 đường dẫn; tin đầu khoảng 610 KB hợp lệ; tin thứ hai vượt 8 MB | Nguồn suy giảm, không coi là ổn định |
| Guland | Không bật thu thập trực tiếp; adapter fixture vẫn tồn tại | Giữ trạng thái tắt hiện có |

Chẩn đoán Homedy: URL từng lỗi có thể tải khoảng 582 KB trong phiên mới, nhưng
`Connection: close` vẫn gặp lỗi ở tin tiếp theo. Vì vậy chưa có bằng chứng lỗi
do keep-alive và không áp dụng mẹo thay phiên/cookie. Bằng chứng chi tiết:

- `runtime/crawl_review/source-probe-20260922T022511Z.json`
- `runtime/crawl_review/homedy-transport-20260922T022718Z.json`

## Kết quả sau khi triển khai Docker

Đã build và khởi động lại scraper; Prometheus đã nạp đủ bốn cảnh báo crawl mới.
Scraper và trainer đều đang chạy, không còn bị pause. Prometheus đo
`up{job="scraper"}=1` và đọc được trạng thái từng nguồn.

Lượt tự động từ **09:38:48 đến 09:40:38 ngày 22/09/2026 (UTC+7)**:

| Nguồn | Tin Kafka đã ACK | Hợp lệ | Không đạt validation | Lỗi tải chi tiết | Kết quả |
|---|---:|---:|---:|---:|---|
| Alonhadat | 20 | 17 | 3 | 0 | Hoàn tất đọc 20 tin; đánh dấu partial vì có tin không hợp lệ |
| Homedy | 1 | 1 | 0 | 3 | Dừng sau ba phản hồi vượt giới hạn dung lượng liên tiếp |

Tin không hợp lệ được gửi qua luồng xử lý/cách ly hiện có và không tính là tin
hợp lệ. Đây là số liệu ACK và validation của crawler, không phải phép đo độc lập
số bản ghi mới trong Mongo. Sau cả hai nguồn, scheduler vẫn hoạt động và hẹn
lượt tiếp theo lúc **10:10:38**, theo chu kỳ 1.800 giây. Không coi trạng thái
container healthy là bằng chứng nguồn Homedy đã phục hồi.

Kiểm thử toàn bộ repository: **526 passed**, 373 cảnh báo deprecation từ
NumPy/joblib. Kiểm tra tích hợp bằng fixture tổng hợp qua Kafka/Mongo thật:
**5 ACK, 4 URL duy nhất, 2 tin hợp lệ, 2 tin cách ly, 0 lần ghi vào DB chính**.
Build Docker, kiểm tra Compose, compile Python và Prometheus rules đều đạt.
Log, metrics, trạng thái dịch vụ và kết quả JUnit được lưu cùng
`runtime/crawl_review/validation.json`.

## Các lỗi nội bộ đã sửa

- **Lặp các tin đầu danh sách:** bỏ mặc định fresh-start mỗi lượt; lượt đầu và
  định kỳ dùng chung checkpoint. Ưu tiên URL chưa nhận, rồi URL đã lâu chưa cập
  nhật. URL đã được Kafka xác nhận sẽ được đọc lại sau `SCRAPE_REVISIT_SECONDS`
  (mặc định 86.400 giây). Không loại vĩnh viễn tin đã từng thấy.
- **Mất tiến độ khi lỗi gửi:** chỉ ghi checkpoint sau ACK Kafka. Tin chưa được
  xác nhận vẫn được thử lại; Mongo upsert theo URL xử lý khả năng gửi lại.
- **Một tin lỗi làm dừng cả danh sách:** lỗi tải tạm thời hoặc 404/410 để lại URL
  chưa hoàn thành rồi thử tin khác. Sau ba lỗi liên tiếp thì dừng nguồn trong
  lượt đó; nguồn khác tiếp tục. Tin parse sai được lưu cách ly, không giả là hợp lệ.
  URL từng lỗi được xếp sau URL chưa thử trong lượt sau để không chặn mãi các
  tin phía cuối danh sách; trạng thái lỗi không được coi là ACK thành công.
- **Nguồn đầu dùng hết thời gian nguồn sau:** scheduler chạy mỗi nguồn trong
  subprocess có timeout riêng. Lỗi, timeout hay access denied của một nguồn
  không ngăn lịch chạy nguồn tiếp theo.
- **Không tôn trọng cooldown khi chuyển tin:** khi cạn retry 429/Retry-After,
  dừng toàn nguồn; không chuyển sang URL khác để tiếp tục gửi sớm. 401/403,
  challenge và robots từ chối vẫn dừng nguồn.
- **Lỗi luồng HTTP và robots:** retry có giới hạn cho lỗi gzip/chunk; chỉ cache
  robots sau khi kiểm tra xong; Homedy trial cũng tôn trọng `Request-rate`.
- **Trạng thái hỏng làm ngừng crawl:** giữ bản `.corrupt-*` để chẩn đoán và khôi
  phục bằng cơ chế gửi lại có kiểm soát. Counter reset được thể hiện riêng.
- **Metrics báo thành công sai:** phân biệt tin hợp lệ đã ACK, tin cách ly,
  lượt hoàn tất, lượt không có tin đến hạn, lượt lỗi một phần và timeout.

## Cấu hình hiện tại

```dotenv
ENABLED_SOURCES=alonhadat,homedy
SCRAPE_FRESH_START=false
SCRAPE_INITIAL_FRESH_START=false
SCRAPE_STATE_FILE=runtime/scrape_state/producer_state.json
SCRAPE_INITIAL_STATE_FILE=runtime/scrape_state/producer_state.json
SCRAPE_REVISIT_SECONDS=86400
SCRAPE_MAX_CONSECUTIVE_FAILURES=3
```

`SCRAPE_TIMEOUT` áp dụng riêng cho từng nguồn. `SCRAPE_LIMIT` là trần số tin,
không phải số tin chắc chắn lấy được; cấu hình một trang chỉ lấy được các tin
đang có trên trang đó. Chỉ chạy một scheduler: legacy hoặc Airflow. Airflow
đang là profile tuỳ chọn; không bật đồng thời với scheduler legacy.

Các thay đổi trên đã được đưa vào `.env.example`, Compose và các khoá crawl
liên quan trong `.env` hiện tại. Giữ nguyên tài khoản, khoá API và các cấu hình
không liên quan.

## Theo dõi và kiểm chứng lại

Prometheus có cảnh báo khi nguồn được bật nhưng lỗi lặp lại trong hai giờ,
không kiểm tra được trang danh sách trong hai giờ, hoặc không có tin hợp lệ
được ACK trong hai ngày, cũng như khi mất trạng thái metrics. Đây là cảnh báo hiển thị tại Prometheus/Grafana;
dự án chưa cấu hình Alertmanager để gửi thông báo ngoài hệ thống.

Metrics đáng xem: `source_crawl_enabled`, `source_last_checked_timestamp`,
`source_last_success_timestamp`, `valid_listings_published_total`,
`crawl_partial_total`, `detail_fetch_errors_total`, `scheduler_timeouts_total`.
Lượt không có URL đến hạn vẫn cập nhật thời điểm kiểm tra nguồn, nhưng không
giả lập thời điểm có dữ liệu mới.

Dừng lịch crawl cùng nguồn trước khi chạy kiểm tra trực tiếp bên dưới, rồi khôi
phục trạng thái lịch sau khi kiểm tra. Tránh hai tiến trình cùng gửi yêu cầu hoặc
cùng ghi checkpoint/metrics; lệnh fixture Kafka không truy cập website.

```powershell
# Mẫu đọc nguồn có giới hạn, không ghi Kafka/Mongo; exit 1 nếu nguồn suy giảm.
python scripts/check_crawl_sources.py --sources alonhadat,homedy --limit 2 --output runtime/crawl_review/latest.json

# Kiểm thử offline và kiểm tra cấu hình.
python -m pytest -q
docker compose config --quiet
docker compose exec -T prometheus promtool check rules /etc/prometheus/alert_rules.yml

# Tích hợp Kafka/Mongo bằng fixture có đánh dấu synthetic, không ghi DB chính.
docker compose exec -T scraper python scripts/verify_source_pipeline.py
```

Kết quả lần chạy cuối được lưu tại `runtime/crawl_review/validation.json`.
Để xác nhận ổn định lâu dài cần quan sát nhiều chu kỳ thực, tỷ lệ tin hợp lệ và
thời gian gián đoạn từng nguồn. Nếu Homedy tiếp tục lỗi, Alonhadat vẫn được xử
lý độc lập; không dùng số liệu giả hay giảm chuẩn kiểm tra để tăng số tin.

## Tệp đã thay đổi trong phần kiểm tra crawl

| Tệp | Thay đổi và mục đích |
|---|---|
| `scraper/multi_source.py` | Checkpoint sau ACK, lịch đọc lại, thứ tự thử lại, ngân sách lỗi và cách ly lỗi từng nguồn |
| `scraper/kafka_producer.py` | Tham số refresh/ngân sách lỗi; xử lý lỗi gửi Kafka đồng bộ có giới hạn |
| `scraper/http_policy.py` | Phân loại 404/410 và cooldown; retry lỗi truyền tải; cache robots sau validation |
| `scraper/homedy_scraper.py` | Trial tôn trọng Request-rate và validation trước cache robots |
| `scraper/source_metrics.py` | Metrics tiến độ, tin hợp lệ, lỗi một phần, freshness và nguồn được bật |
| `scripts/auto_scrape.py` | Timeout riêng cho từng nguồn; checkpoint chung cho lượt đầu/định kỳ |
| `scripts/check_crawl_sources.py` | Lệnh kiểm tra trực tiếp có giới hạn, xuất JSON và báo suy giảm trung thực |
| `docker-compose.yml` | Truyền cấu hình checkpoint/refresh/ngân sách lỗi cho crawler và Airflow |
| `.env.example` | Mặc định giữ tiến độ, đọc lại sau một ngày; ghi rõ cấu hình |
| `.env` (local, không commit) | Áp dụng checkpoint chung và tắt fresh-start cho các lượt tự động |
| `monitoring/alert_rules.yml` | Cảnh báo metrics không đọc được, lỗi lặp lại, nguồn không được kiểm tra, dữ liệu cũ |
| `utils/tests/test_multi_source_runtime.py` | Kiểm tra ACK/replay, refresh, URL lỗi, metrics và state hỏng |
| `utils/tests/test_scrape_scheduler.py` | Kiểm tra timeout/lỗi độc lập và tiếp tục nguồn sau |
| `utils/tests/test_source_http_stability.py` | Kiểm tra retry, robots, cooldown và giới hạn lỗi HTTP |
| `utils/tests/test_source_check.py` | Kiểm tra công cụ chẩn đoán báo đúng nguồn suy giảm/tắt |
| `README.md` | Liên kết kết quả; hướng dẫn tránh chạy crawler đồng thời |
| `airflow/README.md` | Sửa mô tả nguồn mặc định và điều kiện bật crawl |
| `docs/SOURCE_MIGRATION.md` | Đánh dấu kiến trúc cũ là lịch sử, liên kết trạng thái hiện tại |
| `docs/AUTOMATION.md` | Cập nhật retry, metrics, giới hạn lượt đầu và checkpoint |
| `DEPLOYMENT.md` | Cập nhật mặc định 10 tin/một trang mỗi nguồn và giữ checkpoint |
| `docs/ERROR_HANDLING_STRATEGY.md` | Cập nhật ACK, timeout từng nguồn, lịch retry và metrics |
| `docs/CRAWL_STABILITY.md` | Bằng chứng kiểm tra, giới hạn kết luận và cách kiểm chứng lại |

Báo cáo nghiên cứu trước đó được hoàn tất riêng tại
[LECTURER_RESEARCH_REPORT.md](LECTURER_RESEARCH_REPORT.md), gồm kết quả thực nghiệm,
biểu đồ và các yêu cầu giảng viên còn thiếu dữ liệu để kết luận.
