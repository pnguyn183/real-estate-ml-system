# HomeNest: API và Docker

## Docker

Cài Docker Desktop và bật Linux containers. Chạy trong thư mục dự án:

```powershell
Copy-Item .env.example .env
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Sao chép giá trị được in ra vào `AUTH_JWT_SECRET` trong `.env` — không để nguyên giá trị
mẫu và không commit `.env`. Với tài khoản User đăng ký bằng email/mật khẩu, đây là cấu hình
bắt buộc; người dùng sẽ luôn được cấp role User.

Để bật đăng nhập Manager/Admin qua OIDC, sao chép
`.streamlit\secrets.toml.example` thành `.streamlit\secrets.toml`, rồi điền client
secret, cookie secret và callback URL. Cũng điền issuer, metadata URL, client ID trong
`.env`. Không commit các tệp bí mật. Callback production phải là URL HTTPS công khai
kết thúc bằng `/oauth2callback`, đồng thời được đăng ký chính xác ở identity provider.
OIDC là phương thức bổ sung; không bật OIDC thì tài khoản nội bộ vẫn dùng email/mật khẩu.

Sau đó chạy:

```powershell
docker compose up --build -d
```

Tạo tài khoản có quyền cao trực tiếp trong API container. Dùng email bạn kiểm soát;
không có tài khoản/mật khẩu mặc định:

```powershell
docker compose exec api python create_account.py --role Admin --email admin@your-domain.com --name "HomeNest Admin" --generate-password
docker compose exec api python create_account.py --role Manager --email manager@your-domain.com --name "HomeNest Manager" --generate-password
```

Mỗi mật khẩu ngẫu nhiên được in một lần trong terminal. Lưu an toàn và chia sẻ riêng
cho người nhận; đăng ký trên giao diện chỉ tạo role User.

- Website: http://localhost:8501
- Swagger API: http://localhost:8000/docs
- Healthcheck: http://localhost:8000/health

```powershell
docker compose logs -f
docker compose down
```

Volume `homenest-data` giữ dữ liệu và ảnh khi tạo lại container. Lần đầu khởi tạo dữ liệu mẫu;
Docker không đóng gói JSON và ảnh cá nhân của workspace. Để nhập dữ liệu hiện có sau khi tạo container:

```powershell
docker compose stop web
docker compose cp properties.json api:/app/data/properties.json
docker compose cp users.json api:/app/data/users.json
docker compose cp messages.json api:/app/data/messages.json
docker compose cp images/. api:/app/data/images/
docker compose start web
```

## Chạy trực tiếp

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe run_local.py
```

Hoặc dùng `run_app.ps1` / `run_app.bat`. Launcher chạy API, chờ sẵn sàng rồi chạy Streamlit;
dừng bằng Ctrl+C. Nếu chạy riêng, dùng hai terminal:

```powershell
.\venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
.\venv\Scripts\python.exe -m streamlit run web.py
```

Chạy `web.py` thay vì `app.py` để bật endpoint phiên trình duyệt và giữ đăng nhập sau khi tải lại trang.

## Cấu hình

- `VALUATION_API_URL`: mặc định `http://127.0.0.1:8000`; Compose đặt `http://api:8000`.
- `AUTH_OIDC_ISSUER`, `AUTH_OIDC_METADATA_URL`, `AUTH_OIDC_CLIENT_ID`: phải khớp với issuer, discovery metadata URL và client ID trong `.streamlit/secrets.toml`.
- `AUTH_JWT_SECRET`: khóa ký JWT cho tài khoản đăng ký nội bộ, tối thiểu 32 byte; giữ ổn định và bí mật. Đổi khóa sẽ làm phiên đang đăng nhập mất hiệu lực.
- `AUTH_ADMIN_GROUPS`, `AUTH_MANAGER_GROUPS`, `AUTH_ADMIN_EMAILS`, `AUTH_MANAGER_EMAILS`: role policy cho API; đồng bộ các group/email với `[roles]` trong `secrets.toml`. Email chỉ được dùng khi provider đánh dấu đã xác minh.
- `[auth].client_secret` và `cookie_secret` chỉ đặt trong `.streamlit/secrets.toml`; không đưa vào `VITE_*`, `.env` đã commit, log hoặc image.
- `HOMENEST_DATA_DIR`: thư mục dữ liệu; mặc định thư mục dự án, Docker dùng `/app/data`.

Compose tự đọc `.env`; `run_local.py` cũng nạp `AUTH_JWT_SECRET` từ `.env` nếu biến này chưa được đặt trong terminal. Các biến OIDC còn lại cần đặt trong terminal khi chạy local. Khi chạy production, dùng HTTPS reverse proxy, cập nhật callback URL và giới hạn truy cập mạng. Không có luồng đăng nhập demo.

## API

```text
POST /api/v1/valuations
{"location":"Quận 7, TP.HCM","type":"Căn hộ","area":85}

POST /api/v1/valuations/batch
{"subjects":[{"id":"BDS-001","location":"Quận 7, TP.HCM","type":"Căn hộ","area":85}]}
```

Batch nhận tối đa 500 dòng, giữ lỗi từng dòng. Các endpoint định giá nhận
`Authorization: Bearer <OIDC ID token>`. Backend tải metadata/JWKS
từ issuer đã cấu hình, xác minh chữ ký, issuer, audience và thời hạn; batch cùng
`GET /api/v1/model` chỉ cho Manager; định giá đơn lẻ cho User/Manager. Admin có API
quản trị `/api/v1/admin/users`, không có quyền gọi các API định giá. `GET /health` không yêu cầu đăng nhập và trả
trạng thái API, estimator cùng số tin so sánh hợp lệ. Backend đọc danh mục server.
Giao diện gọi HTTP từ tiến trình Streamlit; trình duyệt không gọi API trực tiếp và
không cần cấu hình CORS cho luồng này. Quản lý tin, ảnh và hồ sơ hiện vẫn dùng kho JSON chung.

Đây là API do dự án cung cấp, chưa tích hợp nguồn giá thị trường bên ngoài.
Thuật toán dùng trung vị đơn giá tin chào bán cùng khu vực và loại, chưa điều chỉnh các
đặc điểm bổ sung. Báo cáo HTML tải xuống có thể mở trong trình duyệt và in ra PDF.
Không có confidence score vì hiện chưa có mô hình ML đã hiệu chuẩn. Kho JSON phù hợp triển khai nhỏ với một web và một API;
chưa có giao dịch cơ sở dữ liệu cho nhiều phiên ghi đồng thời hoặc nhiều replica.

Cấu hình tham khảo tài liệu [FastAPI Docker](https://fastapi.tiangolo.com/deployment/docker/)
và [Docker Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/).

## Kiểm thử

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Test gọi endpoint thật qua TestClient với dữ liệu tạm, không sửa danh mục đang dùng.
