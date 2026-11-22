# Predictive-control experiment gate

Forecast chưa được nối vào controller vận hành. Trước tiên phải chạy gate độc lập:

```powershell
python -m research.predictive_control_experiment `
  --artifact runtime/research/forecast-2h-20261120-bound/horizon_300s/forecast.joblib `
  --capacity-evidence runtime/research/<calibration>/calibration/run.json `
  --output runtime/research/predictive-control-readiness.json
```

Gate chỉ cho phép bắt đầu thử nghiệm khi:

- artifact đã vượt persistence và hash còn nguyên;
- manifest khóa đúng SHA-256 của `supervised.csv` và `split.json`; artifact bound
  ngày 21/11 đã vượt kiểm tra này, còn artifact cũ phải fail-closed;
- target là offered demand trước admission;
- calibration quá tải hoàn tất trên một allocation có fingerprint;
- miền tải của đúng các chỉ mục train (không dùng validation/test range) bao phủ
  mức quá tải đã đo.

Sau đó vẫn phải chạy tối thiểu ba cặp AB/BA giữa reactive controller và
experimental forecast controller, cùng workload, seed, tài nguyên, trạng thái đầu
và thời lượng. Báo cáo phải chứa offered/admitted/committed/deferred/rejected,
p95/p99, lỗi, peak lag, recovery time và served fraction. Mỗi hành động dự báo
phải tham chiếu hash artifact và xảy ra trước phản ứng đo được.

`ready_for_isolated_predictive_control_trial` chỉ là quyền chạy thử nghiệm. Nó
không chứng minh cải thiện và không cho phép tự động nối forecast vào production.
Nếu gate trả `blocked_before_trial`, giữ controller hiện tại không đổi.

Lần kiểm tra ngày 21/11 đã giải quyết ba blocker về binding provenance và
resource fingerprint, nhưng vẫn bị chặn do calibration chưa hoàn tất và chưa đo
được overload rate. Miền train 5 phút chỉ đạt tối đa 57,0544 record/s; không được
suy diễn ra vùng quá tải cho tới khi có calibration tương thích. Xem
[`evidence/predictive-control-readiness-20261121.json`](evidence/predictive-control-readiness-20261121.json).

## Overlay thử nghiệm, không nối production

[`predictive_controller.py`](../research/predictive_controller.py) cung cấp một
overlay chỉ được phép hạ/giữ trần admission của reactive controller. Module từ
chối khởi tạo nếu readiness, artifact hash, support/split hash, capacity evidence
hoặc resource fingerprint không hợp lệ. Controller production hiện tại không gọi
module này.

[`predictive_control_simulation.py`](../research/predictive_control_simulation.py)
chỉ replay hàng đợi deterministic để kiểm tra conservation, giới hạn và thứ tự
hành động. Output luôn ghi `simulation_only_not_causal` và không suy ra p95/p99
hay lỗi live. Cùng module tạo/kiểm tra kế hoạch ít nhất ba cặp AB/BA, nhưng chỉ dữ
liệu live vượt gate mới được dùng để tính chênh lệch quan sát.
