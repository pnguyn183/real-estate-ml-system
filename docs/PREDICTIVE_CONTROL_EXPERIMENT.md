# Predictive-control experiment gate

Forecast chưa được nối vào controller vận hành. Trước tiên phải chạy gate độc lập:

```powershell
python -m research.predictive_control_experiment `
  --artifact runtime/research/forecast-2h-20261117/horizon_300s/forecast.joblib `
  --capacity-evidence runtime/research/<calibration>/calibration/run.json `
  --output runtime/research/predictive-control-readiness.json
```

Gate chỉ cho phép bắt đầu thử nghiệm khi:

- artifact đã vượt persistence và hash còn nguyên;
- manifest khóa đúng SHA-256 của `supervised.csv` và `split.json`; artifact cũ
  thiếu hai binding này phải fail-closed và được re-export trước trial;
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
