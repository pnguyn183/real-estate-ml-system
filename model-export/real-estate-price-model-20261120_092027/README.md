# Real Estate Price Model Export

Đây là model đã được train sẵn. Các lệnh dưới đây chỉ nạp model để dự đoán,
không chạy lại quá trình huấn luyện.

## Môi trường

- Python 3.12 (model được xuất từ Python 3.12.10)
- Cài đúng phiên bản thư viện trong `requirements.txt`

## Chạy trên Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python src/modeling/predict_price.py --model-path model/price_model.joblib --input-json sample_input.json
```

Sửa `sample_input.json` để dự đoán bất động sản khác.

## Nội dung

- `model/price_model.joblib`: model đã train và metadata nhúng bên trong
- `model/metadata.json`: phiên bản, schema và thông tin đánh giá
- `model/metrics.json`: các metric của lần train
- `src/`: bản copy tối thiểu của mã suy luận mà joblib cần khi nạp model
- `SHA256SUMS.txt`: mã kiểm tra tính toàn vẹn sau khi gửi file

Gói này không chứa dữ liệu huấn luyện, tài khoản hay secret. Không nạp file
joblib nhận từ nguồn không tin cậy vì định dạng joblib/pickle có thể thực thi mã.

