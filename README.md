# HomeNest — Phân tích và quản lý bất động sản

Ứng dụng Streamlit để quản lý tin đăng, tìm kiếm bất động sản và ước tính giá tham khảo từ các tin chào bán tương đồng. Giao diện dùng nền kem ấm, màu cam đất chủ đạo và bộ biểu tượng **Material Symbols** cho các thao tác.

## Bắt đầu nhanh (Windows)

Cài Python **3.11 trở lên**, mở PowerShell tại thư mục gốc dự án (thư mục có `requirements.txt`) và chạy:

```powershell
python --version
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Sao chép khóa vừa in vào dòng `AUTH_JWT_SECRET` trong file `.env` (thay `CHANGE_ME`), lưu file rồi khởi động cả API và giao diện:

```powershell
.\venv\Scripts\python.exe run_local.py
```

Mở **http://localhost:8501** khi Streamlit khởi động xong. Đăng ký tài khoản để sử dụng; nhấn `Ctrl+C` trong terminal để dừng. Không chia sẻ hoặc commit file `.env`. Hướng dẫn đầy đủ, tạo tài khoản Admin/Manager, Docker và xử lý lỗi nằm ở mục [Cài đặt và chạy](#cài-đặt-và-chạy).

Khi mở URL với phiên chưa đăng nhập, HomeNest hiển thị **trang giới thiệu** trước: giới thiệu nền tảng, số liệu từ danh mục hiện có, sáu nhóm tính năng, phương pháp tính giá tham khảo, công cụ theo vai trò và câu hỏi thường gặp. Đầu trang có ảnh bất động sản lưu cục bộ làm nền, thẻ tạo tài khoản, số liệu danh mục hiện có và thanh điều hướng xanh đậm với điểm nhấn cam đất; các phần còn lại dùng nền kem ấm và bố cục thích ứng với điện thoại. Các liên kết trên thanh đầu trang cuộn đến đúng phần; nút ở từng thẻ tính năng mở đăng nhập và đưa người dùng đến mục đã chọn sau khi xác thực.

Chọn **Đăng nhập**, **Bắt đầu cùng HomeNest** hoặc **Đăng nhập để khám phá** để mở trang đăng nhập; từ đó chọn **Tạo tài khoản** nếu cần đăng ký. Nút **Về trang giới thiệu** đưa về trang công khai. Đăng nhập/đăng ký thành công mở tổng quan; đăng xuất trở về trang giới thiệu. Phiên OIDC hợp lệ tiếp tục được khôi phục như trước.

Ứng dụng sử dụng các thành phần giao diện native của Streamlit. Sau khi đăng nhập, thanh điều hướng ngang phía trên dùng nền xanh navy và điểm nhấn cam để mở các khu vực chính. Sidebar bên trái dùng nền kem ấm, logo và thông tin vai trò riêng, nhóm chức năng trong các thẻ gọn, trạng thái dịch vụ và nút đăng xuất dễ nhận biết. Trên màn hình nhỏ, thanh ngang có thể cuộn và sidebar có thể thu gọn.

### Thanh điều hướng User và Manager

| Nút | Nội dung |
|---|---|
| Trang chủ | Dashboard, KPI theo vai trò, biểu đồ danh mục và tin mới |
| Vị trí | Tìm tin theo từ khóa, khu vực, loại hình, nhu cầu và ngân sách |
| Mặt bằng | So sánh giá chào bán/m² theo khu vực từ dữ liệu HomeNest hiện có |
| Tiện ích | Bộ công cụ theo nhu cầu: tìm nhà, định giá, đăng/quản lý tin, tin đã lưu và mặt bằng giá; Manager có định giá CSV |
| Thiết kế | Bộ sưu tập cảm hứng không gian sống, lọc theo loại hình và mở tin phù hợp; ảnh có nhãn minh họa |
| Tin tức | Góc nhìn tự động tính từ danh mục, không giả làm tin tức báo chí hoặc dữ liệu thị trường trực tiếp |
| Liên hệ với chúng tôi | Gửi yêu cầu hỗ trợ vào hộp thư nội bộ; Admin tiếp nhận trong Hộp thư hỗ trợ |

## Chức năng

Admin có khu vực quản trị riêng, gồm tổng quan quản trị, quản lý tin đăng, tài khoản User, kiểm tra tin trùng, báo cáo, hộp thư hỗ trợ, cài đặt dữ liệu và đăng tin. Các mục tìm kiếm, thị trường, tiện ích cá nhân, yêu thích, diễn đàn, hồ sơ cá nhân và định giá không xuất hiện hoặc mở được bằng role Admin. User và Manager giữ giao diện thành viên.

| Vai trò | Chức năng |
|---|---|
| Admin | Quản lý tài khoản User và toàn bộ tin đăng; đăng tin; kiểm tra trùng; xem báo cáo, xuất CSV; tiếp nhận hỗ trợ và cấu hình dữ liệu |
| Manager | Xem thông tin phương pháp định giá và định giá hàng loạt bằng CSV |
| User | Khám phá và lọc tin, quản lý bài của tôi, lưu yêu thích theo tài khoản, xem hộp thư và cập nhật hồ sơ |
| Cả ba | Xem tổng quan phù hợp vai trò, đăng tin có ảnh, xem chi tiết, sửa và xóa tin trong phạm vi quyền của mình |

Admin có thể quản lý toàn bộ tin; Manager có thêm công cụ định giá; User quản lý các tin thuộc tài khoản của mình. Trang chi tiết hiển thị thông tin bất động sản và ảnh đã tải lên. Danh sách yêu thích được lưu cùng hồ sơ người dùng.

### Thanh điều hướng theo vai trò

| Nhóm | Admin | Manager | User |
|---|---|---|---|
| Tổng quan | Tổng quan quản trị | Tổng quan; Phân tích & định giá; thông tin phương pháp; batch CSV | Tổng quan; Phân tích & định giá |
| Danh mục | Quản lý tin đăng; Kiểm tra tin trùng; Báo cáo & xuất dữ liệu | Khám phá bất động sản; Bài đăng của tôi; Tin đã lưu; Hộp thư | Khám phá bất động sản; Bài đăng của tôi; Tin đã lưu; Hộp thư |
| Tài khoản | Quản lý tài khoản User; Cài đặt dữ liệu | Hồ sơ cá nhân | Hồ sơ cá nhân |
| Thao tác chung | Đăng tin mới; Đăng xuất | Đăng tin mới; Đăng xuất | Đăng tin mới; Đăng xuất |

### Tổng quan và báo cáo

- Admin có tổng quan vận hành: số tin bán/cho thuê, yêu cầu hỗ trợ, lối tắt quản trị và danh sách tin gần đây; không có công cụ tìm nhà hoặc lưu tin.
- User thấy số tin của mình, tin đang bán, số tin đã lưu và trung vị giá chào bán/m² của danh mục để tham khảo.
- Manager thấy quy mô danh mục, số tin đang bán/cho thuê và trung vị giá chào bán trên mỗi m² từ những tin có giá và diện tích hợp lệ.
- Biểu đồ số tin theo loại bất động sản và trung vị đơn giá theo khu vực.
- Ba tin mới nhất trong danh mục.
- Báo cáo Admin bổ sung tổng giá chào bán, giá chào bán trung bình, số tin có dấu hiệu trùng và nút tải CSV trực tiếp. CSV dùng UTF-8 có BOM để hỗ trợ tiếng Việt khi mở bằng Excel; các ô văn bản có thể bị diễn giải thành công thức được xuất dưới dạng văn bản an toàn.

Trang đăng nhập/đăng ký và tổng quan dùng ảnh tham khảo đã lưu cục bộ trong `assets/properties/`, không tải ảnh từ bên thứ ba khi chạy app. Tin đăng luôn ưu tiên ảnh do người đăng tải; khi chưa có ảnh thực tế, HomeNest dùng ảnh tham khảo có nhãn rõ ràng, không trình bày ảnh minh họa như ảnh của tin đăng.
| Diễn đàn | Tìm/lọc chủ đề theo danh mục, đính kèm ảnh, thích và trao đổi trong phản hồi |
Biểu đồ và chỉ số lấy từ dữ liệu hiện có trong ứng dụng. Tin cho thuê được tách khỏi các phép tổng hợp giá bán; các thống kê tổng quan chưa điều chỉnh khác biệt giữa loại hình và khu vực.

### Tìm kiếm, chi tiết và yêu thích

Diễn đàn HomeNest có các chủ đề hướng dẫn được ghim rõ là nội dung từ đội ngũ HomeNest. Thành viên có thể tìm kiếm, lọc danh mục, sắp xếp chủ đề, xem thống kê theo danh mục, đăng bài, gửi phản hồi và thích/bỏ thích. Chủ đề cho phép đính kèm tối đa 3 ảnh JPG, PNG hoặc WEBP (5 MB/ảnh); ảnh được chuẩn hóa thành JPEG và gỡ metadata trước khi lưu. Nội dung chủ đề nằm trong `forum.json`, ảnh nằm trong `images/`; khi chạy Docker, cả hai được giữ trong volume `homenest-data`.

Giao diện diễn đàn có ba cột: menu, tag và bình luận gần đây bên trái; ô viết bài, dãy chủ đề nổi bật và bảng thảo luận ở giữa; góc nhìn thị trường và thống kê cộng đồng bên phải. Tag đồng bộ với bộ lọc danh mục; bình luận gần đây mở đúng chủ đề. Biểu đồ dùng trung vị giá chào bán theo khu vực từ tin hiện có trong HomeNest. Trên điện thoại, phần thảo luận được đưa lên đầu và dãy bài nổi bật có thể vuốt ngang.

- Tìm từ khóa trong tiêu đề, khu vực hoặc mô tả; lọc theo loại, nhu cầu và ngân sách tối đa.
- Ngân sách bằng `0` nghĩa là không giới hạn giá; có thể tìm tin trên 10 tỷ đồng.
- Sắp xếp theo tin mới nhất, giá tăng/giảm hoặc diện tích lớn nhất; mỗi trang có tối đa 9 tin.
- Bộ lọc và trang kết quả được giữ trong phiên khi mở chi tiết rồi quay lại.
- Thẻ tin hiển thị ảnh thực tế đầu tiên nếu có. Khi chưa có ảnh, dùng ảnh tham khảo HomeNest kèm nhãn ảnh minh họa.
- Nút **Chi tiết** mở thông tin đầy đủ; **Liên hệ** mở chi tiết và thông tin điện thoại của người đăng; **Lưu tin / Đã lưu** thêm hoặc bỏ yêu thích.

Giá trên thẻ và các chỉ số được rút gọn theo triệu/tỷ đồng; trang chi tiết hiển thị thêm giá đầy đủ.

### Đăng, sửa và xóa tin

1. Chọn **Đăng tin mới** trên sidebar hoặc **Thêm tin đăng** trong danh sách quản lý.
2. Nhập tiêu đề, khu vực, loại, nhu cầu, giá, diện tích, số phòng, liên hệ và mô tả. Tiêu đề, khu vực, liên hệ, mô tả phải có nội dung; giá và diện tích phải lớn hơn `0`.
3. Tải ảnh JPG, JPEG hoặc PNG nếu có. Tổng số ảnh của một tin không vượt quá 5.
4. Chọn **Đăng tin** để lưu và mở trang chi tiết. Nút **Xóa form** đưa form tạo mới về giá trị ban đầu.

Trong danh sách quản lý hoặc trang chi tiết, chọn **Chỉnh sửa** để cập nhật tin có quyền quản lý. Form sửa cho phép bổ sung ảnh hoặc chọn thay thế toàn bộ ảnh hiện tại; nút **Khôi phục nội dung** đưa form về nội dung đã lưu. Việc sửa giữ ID, chủ tin, ngày đăng và dữ liệu đánh giá, đồng thời bổ sung thời điểm cập nhật.

Khi chọn **Xóa tin**, ứng dụng mở hộp thoại cho phép xác nhận xóa hoặc **Giữ lại**. Thao tác xóa loại tin khỏi danh mục; các tệp ảnh đã lưu chưa được tự động dọn khỏi thư mục `images/`.

Thông báo đăng/cập nhật thành công xuất hiện sau thao tác lưu. Mở lại một bài cũ không hiển thị thông báo đăng thành công.
├── forum.json             # Chủ đề, phản hồi, lượt thích và bài ghim của diễn đàn

### Hộp thư và hồ sơ

Hộp thư User hiển thị tối đa 20 tin nhận được, sắp xếp mới nhất trước. Form liên hệ lưu yêu cầu vào hộp thư nội bộ để Admin tiếp nhận; nội dung được ghi vào tệp tin nhắn cục bộ của ứng dụng. Hồ sơ cho phép cập nhật họ tên, số điện thoại, email và giới thiệu; lưu hồ sơ giữ lại danh sách yêu thích.

### Quản lý tài khoản User dành cho Admin

Trang **Quản lý tài khoản User** lấy tài khoản thật từ `accounts.sqlite3`, không coi các hồ sơ mẫu trong `users.json` là tài khoản đăng nhập. Danh sách chỉ gồm role `User` đăng nhập nội bộ; OIDC tiếp tục do nhà cung cấp danh tính quản lý.

- Tìm theo họ tên/email, lọc tài khoản đang hoạt động hoặc đã khóa; xem ngày tạo và lần đăng nhập gần nhất theo giờ Việt Nam.
- Tạo User mới, cập nhật họ tên, số điện thoại, giới thiệu và ghi chú nội bộ. Email đăng nhập cố định; không có công cụ tự cấp Manager/Admin từ trang này.
- Xem số tin bán/thuê, tin đã lưu, chủ đề/phản hồi diễn đàn, yêu cầu hỗ trợ và danh sách bài đăng; mở từng bài để quản lý.
- Khóa/mở khóa với xác nhận; khóa bắt buộc có lý do. Dữ liệu liên quan được giữ lại.
- Đặt lại mật khẩu với lý do, mật khẩu xác nhận và xác nhận thu hồi phiên. Mật khẩu được băm, không hiển thị lại hoặc ghi vào nhật ký.
- Nhật ký ghi người thực hiện, thời gian, loại thao tác, lý do hoặc các trường đã cập nhật; hiển thị tối đa 100 sự kiện gần nhất.

Các API quản trị kiểm tra quyền Admin và chỉ cho phép tác động tới tài khoản User. Phiên nội bộ được xác thực lại ở mỗi lần thao tác; khóa tài khoản hoặc đổi mật khẩu làm phiên cũ mất hiệu lực, kể cả sau khi mở khóa trở lại. Các callback ghi dữ liệu cũng kiểm tra lại phiên. Hồ sơ thành viên nội bộ đồng bộ họ tên với tài khoản, giữ nguyên email đăng nhập và dữ liệu yêu thích; email liên hệ có thể khác email đăng nhập.

## Phương pháp định giá

Tại **Phân tích & định giá**, chọn khu vực, loại bất động sản và diện tích. Kết quả cập nhật theo lựa chọn:

1. Lọc tin có trạng thái **Bán**, cùng loại và cùng toàn bộ tên khu vực sau khi chuẩn hóa chữ hoa/thường, Unicode và khoảng trắng.
2. Bỏ tin có giá/diện tích không hợp lệ và tin trùng theo tiêu đề, giá, diện tích, liên hệ.
3. Tính đơn giá mỗi tin bằng giá chào bán chia diện tích.
4. Tính **giá tham khảo = trung vị đơn giá các tin × diện tích cần định giá**.

Màn hình kết quả gồm giá chào bán tham khảo, trung vị đơn giá, số mẫu hợp lệ và bảng dữ liệu đối chiếu. Khi có ít nhất 2 mẫu, ứng dụng bổ sung biểu đồ đơn giá, đường trung vị và khoảng giá thấp nhất–cao nhất theo mẫu. Khi có ít hơn 3 mẫu, ứng dụng báo dữ liệu còn ít. Nếu không có mẫu phù hợp, ứng dụng thông báo thiếu dữ liệu và không suy đoán từ khu vực khác.

Đây là ước tính từ giá chào bán trong danh mục, chưa phải giá giao dịch đã xác nhận. Ứng dụng chưa có mô hình dự báo theo thời gian hay nguồn dữ liệu thị trường trực tiếp; phép tính chưa điều chỉnh pháp lý, mặt tiền, chất lượng hoặc tiện ích. Khoảng giá theo mẫu không phải khoảng tin cậy thống kê.

## Cài đặt và chạy

Hướng dẫn này chạy đầy đủ cả API FastAPI và giao diện Streamlit trên Windows, không cần Docker hay cấu hình OIDC. Cần **Python 3.11 trở lên** (Docker dùng Python 3.12), PowerShell và kết nối Internet trong lần cài thư viện đầu tiên. Mở PowerShell tại thư mục gốc dự án — nơi có `requirements.txt` — kiểm tra `python --version` trước (nếu máy có nhiều phiên bản, chọn Python 3.11+) rồi chạy:

```powershell
# Kiểm tra Python; cần phiên bản 3.11 trở lên
python --version

# 1. Tạo môi trường Python riêng cho project
python -m venv venv

# 2. Cài các thư viện của API, Streamlit và ứng dụng
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. Tạo file cấu hình local và khóa ký an toàn cho đăng nhập
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$content = Get-Content .env
if ($content -match '^AUTH_JWT_SECRET=(CHANGE_ME)?$') {
    $secret = & .\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
    $content = $content -replace '^AUTH_JWT_SECRET=.*$', "AUTH_JWT_SECRET=$secret"
    Set-Content .env -Value $content -Encoding ascii
}

# 4. Chạy API và giao diện cùng lúc
.\venv\Scripts\python.exe run_local.py
```

Giữ bí mật file `.env`; không gửi file này cho người khác hoặc commit lên Git. `AUTH_JWT_SECRET` bắt buộc cho đăng ký/đăng nhập tài khoản nội bộ. Nếu đã có môi trường `venv`, có thể bỏ qua bước tạo môi trường; nếu `.env` đã có khóa hợp lệ thì không cần tạo lại khóa. Các lệnh dùng trực tiếp Python trong **`venv`**, không cần kích hoạt môi trường và không dùng thư mục `.venv`.

Khi API sẵn sàng, terminal sẽ khởi chạy Streamlit. Mở **http://localhost:8501** trong trình duyệt; đăng ký tài khoản mới để vào ứng dụng với vai trò `User`. API có thể kiểm tra tại **http://127.0.0.1:8000/health**. Dữ liệu local và database tài khoản được tạo trong thư mục dự án khi chạy lần đầu. Dừng cả hai tiến trình bằng `Ctrl+C` trong terminal.

Để tạo tài khoản quản trị viên hoặc quản lý, mở PowerShell thứ hai tại thư mục dự án và chạy một trong các lệnh sau. Chương trình sẽ hỏi mật khẩu an toàn; tuỳ chọn `--generate-password` tạo mật khẩu ngẫu nhiên và chỉ in ra một lần:

```powershell
.\venv\Scripts\python.exe create_account.py --role Admin --email admin@your-domain.com --name "HomeNest Admin"
.\venv\Scripts\python.exe create_account.py --role Manager --email manager@your-domain.com --name "HomeNest Manager" --generate-password
```

Thay địa chỉ mẫu bằng email bạn kiểm soát và lưu mật khẩu an toàn. Đăng ký từ giao diện chỉ tạo tài khoản `User`.

Cũng có thể khởi động bằng một trong hai script:

```powershell
.\run_app.ps1
# Hoặc
.\run_app.bat
```

`run_local.py` khởi chạy API cùng giao diện Streamlit qua `web.py`, bao gồm endpoint phiên trình duyệt để giữ đăng nhập sau khi nhấn F5. Hai script launcher cũng chạy `run_local.py`, nhưng chọn cổng giao diện trống từ `8505` trở lên (`run_app.bat` thử các cổng `8505`–`8510`). Mở đúng địa chỉ mà terminal hiển thị; launcher không tự mở trình duyệt. Đảm bảo cổng API `8000` còn trống.

Không chạy `app.py` trực tiếp bằng Streamlit nếu cần giữ đăng nhập qua F5; cách đó bỏ qua endpoint phiên trình duyệt. Nếu API đã chạy riêng, có thể chạy entry point `web.py` và chỉ định cổng:

```powershell
.\venv\Scripts\python.exe -m streamlit run web.py --server.address 127.0.0.1 --server.port 8505 --server.headless true
```

Dừng ứng dụng bằng `Ctrl+C` trong terminal đang chạy.

### Chạy bằng Docker

Nếu đã cài Docker Desktop và muốn chạy trong container, làm theo hướng dẫn đầy đủ tại [`DOCKER.md`](./DOCKER.md). Compose chạy riêng service API và web, đồng thời lưu dữ liệu trong volume Docker. Đường dẫn truy cập mặc định là **http://localhost:8501**; cấu hình OIDC là tùy chọn.

### Xử lý lỗi khởi động thường gặp

- **Không tìm thấy `venv\Scripts\python.exe`:** tạo môi trường bằng `python -m venv venv` từ thư mục gốc dự án.
- **`ModuleNotFoundError` hoặc thiếu thư viện:** chạy lại `.\venv\Scripts\python.exe -m pip install -r requirements.txt` bằng đúng Python trong `venv`.
- **Báo thiếu hoặc khóa `AUTH_JWT_SECRET` không an toàn:** kiểm tra file `.env` đã có khóa được tạo ở bước 3 và khóa dài ít nhất 32 byte.
- **Cổng 8000 đang được sử dụng hoặc API không khởi động:** dừng tiến trình API HomeNest cũ trước khi chạy lại; `run_local.py` cần cổng 8000 khi tự khởi chạy backend.
- **Đăng nhập OIDC không được cấu hình:** OIDC không bắt buộc; có thể dùng tài khoản email/mật khẩu. Muốn bật OIDC, hãy làm theo mục [Đăng ký, đăng nhập và phân quyền](#đăng-ký-đăng-nhập-và-phân-quyền).

Nếu sau khi cập nhật mã có lỗi `ImportError: cannot import name ... from 'api_client'` (ví dụ `login_account` hoặc `update_account_profile`), hãy dừng hoàn toàn tiến trình Streamlit cũ bằng `Ctrl+C` rồi khởi động lại. Python có thể còn giữ module đã import trong bộ nhớ. Project đã bật `server.fileWatcherType = "poll"` và `runOnSave = true` để theo dõi các module cục bộ trên Windows; cấu hình này cần khởi động lại tiến trình cũ mới có hiệu lực. Từ thư mục dự án, chạy lại `.\venv\Scripts\python.exe run_local.py` (hoặc lệnh khởi động bạn đang dùng); tránh để nhiều bản HomeNest cũ và mới cùng chạy trên các cổng khác nhau.

`requirements.txt` yêu cầu Streamlit `1.62.0` với extra xác thực OIDC và Pandas `3.0.5`. Môi trường `venv` dùng Streamlit `1.62.0` và Pandas `3.0.5`.

## Đăng ký, đăng nhập và phân quyền

Trang đăng nhập và đăng ký dùng email cùng mật khẩu. Người dùng mới đăng ký luôn có role `User`; không có bộ chọn role hay đăng nhập demo. Mật khẩu tối thiểu 12 ký tự, được băm bằng PBKDF2-HMAC-SHA256 trước khi lưu trong `accounts.sqlite3`. Tài khoản được lưu trên máy chủ, không trong trình duyệt.

FastAPI ký bearer token nội bộ bằng `AUTH_JWT_SECRET` (tối thiểu 32 byte); token hết hạn sau 8 giờ và được giữ trong trạng thái phiên Streamlit. Đăng xuất xóa trạng thái đăng nhập. Khi chạy local bằng `run_local.py`, khóa được đọc từ `.env` (biến môi trường có sẵn sẽ được ưu tiên). Hướng dẫn cài đặt phía trên tạo `.env` và chỉ sinh khóa nếu file vẫn còn giá trị mẫu; giữ nguyên khóa đã cấu hình để tài khoản và các phiên hiện có tiếp tục hợp lệ. Tạo tài khoản Manager/Admin bằng lệnh trong mục **Cài đặt và chạy**. Trong Docker, dùng lệnh tương đương `docker compose exec api python create_account.py ...`. Không đặt mật khẩu mặc định trong source hoặc biến public. Database tài khoản nằm trong volume `homenest-data` cùng dữ liệu ứng dụng. Khi sao lưu/khôi phục, giữ an toàn cả volume và khóa ký; đổi khóa sẽ làm token hiện tại mất hiệu lực. Trang đăng ký/đăng nhập gọi API qua tiến trình Streamlit nên không cần bật CORS. Chưa có email xác minh, khôi phục mật khẩu, MFA hoặc giới hạn tần suất đăng nhập/đăng ký; cần bổ sung biện pháp vận hành trước khi mở đăng ký công khai cho dịch vụ nhạy cảm.

Nếu cấu hình OIDC, ứng dụng cũng giữ nút đăng nhập OIDC như phương thức bổ sung cho nhân viên/quản trị. Phiên OIDC được khôi phục bằng cookie Streamlit và đăng xuất gọi `st.logout()`.

1. Sao chép `.streamlit/secrets.toml.example` thành `.streamlit/secrets.toml`; điền client ID/secret, metadata URL và cookie secret mạnh. Tệp thật đã được gitignore.
2. Đăng ký callback URI chính xác với nhà cung cấp danh tính. Chạy Streamlit trực tiếp mặc định dùng `http://localhost:8501/oauth2callback`; khi dùng launcher chọn cổng 8505 trở lên hoặc triển khai sau HTTPS proxy, cập nhật URI trong cả nhà cung cấp và `secrets.toml`.
3. Cấp cho ứng dụng ID token, đồng thời cấu hình claim `groups` hoặc `roles` trong token. Các nhóm mặc định là `homenest-admin` và `homenest-manager`; có thể dùng danh sách email đã xác minh để cấp role qua `[roles]`.
4. Cấu hình cùng `AUTH_OIDC_ISSUER`, `AUTH_OIDC_METADATA_URL` và `AUTH_OIDC_CLIENT_ID` cho FastAPI. API kiểm tra chữ ký bằng JWKS của provider, issuer, audience và thời hạn token; batch/model chỉ dành cho Manager. API URL được gọi server-to-server từ Streamlit nên không cần CORS cho luồng này.

Các biến OIDC API cần được đặt trong terminal trước khi chạy `run_local.py`; riêng `AUTH_JWT_SECRET` được nạp từ `.env` như hướng dẫn trên:

```powershell
$env:AUTH_OIDC_ISSUER = "https://accounts.google.com"
$env:AUTH_OIDC_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
$env:AUTH_OIDC_CLIENT_ID = "your-client-id.apps.googleusercontent.com"
.\venv\Scripts\python.exe run_local.py --server.port 8501
```

`AUTH_ADMIN_GROUPS`, `AUTH_MANAGER_GROUPS`, `AUTH_ADMIN_EMAILS` và `AUTH_MANAGER_EMAILS` là cấu hình tùy chọn cho luồng OIDC; nhóm/email phải khớp với `[roles]`. Với tài khoản nội bộ, chỉ người vận hành có quyền truy cập terminal máy chủ mới có thể tạo tài khoản Manager/Admin bằng `create_account.py`.

## Cấu trúc dự án

```text
gd_Streamlit/
├── app.py                 # Giao diện, điều hướng, trạng thái phiên và thao tác theo vai trò
├── admin_accounts.py      # Hồ sơ, hoạt động và kiểm soát tài khoản User dành cho Admin
├── landing.py             # Trang giới thiệu công khai trước đăng nhập
├── auth_store.py          # Tài khoản nội bộ, kiểm tra mật khẩu và SQLite
├── create_account.py      # Tiện ích terminal cấp tài khoản với role
├── internal_auth.py       # Bearer JWT cho tài khoản nội bộ
├── session_auth.py        # Phiên OIDC/nội bộ và token gọi API server-to-server
├── identity.py            # Ánh xạ claim/group đã xác minh sang User/Manager/Admin
├── oidc.py                # Xác minh chữ ký và claims ID token cho FastAPI
├── api.py                 # API định giá, readiness và phân quyền ở backend
├── api_client.py          # Gọi API, bearer token và xử lý lỗi mạng/HTTP
├── storage.py             # Đọc/ghi JSON, chuẩn hóa trường tùy chọn và phát hiện tin trùng
├── csv_export.py          # Xuất CSV UTF-8 an toàn với công thức bảng tính
├── forum_media.py         # Kiểm tra và chuẩn hóa ảnh đính kèm diễn đàn
├── forum_view.py          # Bố cục diễn đàn, tag, bài nổi bật và góc nhìn thị trường
├── services_view.py       # Trang Tiện ích, bộ lọc theo nhu cầu và số liệu cá nhân
├── design_view.py         # Bộ sưu tập không gian sống, loại hình và gợi ý thiết kế
├── valuation.py           # Tính và trình bày định giá từ tin chào bán so sánh
├── data.py                # Module dữ liệu cũ, không được app.py sử dụng
├── .streamlit/
│   └── config.toml        # Theme chung của ứng dụng
├── properties.json        # Tin đăng và đường dẫn ảnh
├── users.json             # Hồ sơ và dữ liệu người dùng, gồm tin yêu thích
├── messages.json          # Tin nhắn
├── images/                # Ảnh tải lên
├── assets/
│   ├── landing.css        # Giao diện trang giới thiệu, giới hạn phạm vi riêng
│   ├── forum.css          # Giao diện diễn đàn và bố cục trên điện thoại
│   ├── services.css       # Giao diện trung tâm tiện ích trên máy tính và điện thoại
│   ├── design.css         # Giao diện bộ sưu tập thiết kế và bố cục trên điện thoại
│   └── neighborhood.svg   # Minh họa trang đăng nhập và tin chưa có ảnh
├── tests/
│   ├── test_app.py        # Kiểm thử tương tác bằng Streamlit AppTest
│   ├── test_storage.py    # Kiểm thử đọc/ghi và chuẩn hóa dữ liệu
│   ├── test_valuation.py  # Kiểm thử thuật toán định giá
│   ├── test_prediction.py # Kiểm thử xác thực đầu vào và batch
│   ├── test_api.py        # Kiểm thử API và kiểm soát quyền
│   ├── test_auth.py       # Kiểm thử đăng ký, đăng nhập và role tài khoản nội bộ
│   ├── test_forum_media.py # Kiểm thử ảnh đính kèm, giới hạn và gỡ metadata
│   ├── test_identity.py   # Kiểm thử role mapping và xác minh OIDC
│   └── test_session_auth.py # Kiểm thử phiên OIDC và token API
├── requirements.txt
├── run_app.ps1
└── run_app.bat
```

### Luồng xử lý

Tài khoản nội bộ dùng JWT do API ký và mỗi request xác thực tra lại trạng thái, role và phiên bản phiên trong SQLite. `GET /api/v1/auth/me` xác thực lại mỗi lần chạy giao diện. Các endpoint `/api/v1/admin/users` chỉ dành cho Admin; định giá đơn lẻ cho User/Manager, batch/model cho Manager. Tài khoản OIDC (nếu bật) được xác minh và ánh xạ claim đã ký qua `identity.py`. `session_auth.py` chuyển token qua HTTP bearer chỉ từ server Streamlit sang FastAPI. API xác minh token và role trước khi xử lý.

Thao tác lưu gọi các hàm trong `storage.py`. `valuation.py` tách thuật toán `estimate()` và màn hình `render_valuation()` khỏi phần quản lý tin. Khi đăng xuất, trạng thái phiên được xóa; hồ sơ, tin đăng và yêu thích đã lưu vẫn nằm trong các tệp JSON.

`storage.py` lưu JSON bằng tệp tạm cùng thư mục rồi thay thế tệp đích sau khi ghi hoàn tất. Khi đọc tin cũ, các trường tùy chọn thiếu được bổ sung trong bộ nhớ; thao tác đọc không tự ghi lại các tin đó. Danh sách tin rỗng `[]` được giữ nguyên. Khi chưa có tệp dữ liệu, ứng dụng khởi tạo dữ liệu mẫu.

Nếu tệp JSON hiện có bị lỗi cú pháp, không đọc được hoặc sai cấu trúc, ứng dụng báo lỗi và giữ nguyên tệp đó; ứng dụng không thay dữ liệu hỏng bằng dữ liệu mẫu. Hãy sao lưu tệp lỗi rồi kiểm tra hoặc khôi phục bản sao lưu trước khi tiếp tục.

Các tin cũ thiếu `user_id`, `rating`, `reviews`, `images` nhận giá trị mặc định lần lượt là chuỗi rỗng, `0`, `0`, danh sách rỗng. Người đăng chưa xác định hiển thị là **Ẩn danh**; ứng dụng không tự gán quyền sở hữu những tin này cho Admin.

Dữ liệu tin đăng được giữ trong phiên sau khi nạp. Admin có thể chọn **Cài đặt dữ liệu → Tải lại dữ liệu** để nhận các cập nhật từ phiên khác; ứng dụng chưa tự đồng bộ danh mục liên tục giữa nhiều phiên.

### Vị trí chỉnh giao diện

| Thành phần | Vị trí |
|---|---|
| Font chữ, cỡ tiêu đề, màu sắc, bo góc, sidebar và thanh công cụ | `.streamlit/config.toml`, `assets/style.css` |
| Trang giới thiệu công khai | `render_landing()` trong `landing.py`, `assets/landing.css` |
| Đăng nhập và điều hướng theo vai trò | `render_login()`, `render_sidebar()` trong `app.py` |
| Chỉ số, biểu đồ và trang tổng quan | `render_metrics()`, `render_charts()`, `render_overview()` |
| Thẻ tin và trang chi tiết | `render_card()`, `render_grid()`, `render_detail()` |
| Tìm kiếm, quản lý tin và form đăng/sửa | `render_explore()`, `render_listing_management()`, `render_editor()` |
| Kết quả định giá và bảng đối chiếu | `render_valuation()` trong `valuation.py` |

Theme dùng font sans-serif thống nhất (Segoe UI/Arial fallback), thang cỡ tiêu đề giảm dần và màu cam đất trên nền kem ấm. Khu vực sau đăng nhập có thanh thương hiệu xanh đậm, điều hướng làm nổi mục đang chọn, đầu trang kèm ảnh minh họa phù hợp theo nội dung và thẻ/biểu mẫu đồng nhất. Tổng quan User và Admin có ảnh bất động sản lưu cục bộ; thẻ tin, thiết kế, diễn đàn và các trang nội dung tái sử dụng bộ ảnh HomeNest. `client.toolbarMode = "viewer"` ẩn các thao tác dành cho nhà phát triển như Deploy trên thanh công cụ.

Trang chủ tài khoản User mở đầu bằng ảnh hero bất động sản toàn chiều ngang, lớp phủ tương phản và phần giới thiệu/nút hành động đặt trực tiếp trên ảnh; số liệu danh mục nằm trong các thẻ kính mờ. Các lựa chọn nổi bật lấy trực tiếp từ tin đăng đang có; người dùng có thể mở chi tiết, lưu tin, liên hệ người đăng hoặc vào trang tìm kiếm. Dải hình ảnh minh bạch nêu rõ ảnh chỉ để tham khảo và giá là dữ liệu chào bán trong danh mục, không phải giá giao dịch hay định giá độc lập. Phần giới thiệu tính năng và lời mời khám phá tiếp tục dẫn tới các khu vực thật trong ứng dụng.

### Nguồn ảnh giao diện

Ảnh được tải về và lưu trong `assets/properties/` từ Unsplash CDN; app không phụ thuộc mạng ngoài để hiển thị ảnh:

| Tệp | Ảnh nguồn |
|---|---|
| `hero-residence.jpg` | [Unsplash image](https://images.unsplash.com/photo-1600607687939-ce8a6c25118c) |
| `modern-home.jpg` | [Unsplash image](https://images.unsplash.com/photo-1600585154340-be6161a56a0c) |
| `interior-living.jpg` | [Unsplash image](https://images.unsplash.com/photo-1600607687920-4e2a09cf159d) |
| `garden-home.jpg` | [Unsplash image](https://images.unsplash.com/photo-1600566753086-00f18fb6b3ea) |
| `city-residences.jpg` | [Unsplash image](https://images.unsplash.com/photo-1545324418-cc1a3fa10c00) |

## Kiểm thử

Chạy từ thư mục dự án:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

| Bộ test | Số ca | Nội dung |
|---|---:|---|
| `test_app.py` | 27 | Luồng giới thiệu, đăng nhập/đăng ký; tách giao diện Admin; tạo/sửa/khóa/mở khóa/reset tài khoản; đồng bộ hồ sơ; thu hồi phiên; CRUD tin; diễn đàn; hỗ trợ; định giá và CSV của Manager |
| `test_storage.py` | 8 | Danh sách rỗng; bổ sung trường còn thiếu; giữ dữ liệu gốc; đọc/ghi JSON; diễn đàn; xử lý lỗi ghi và thay thế tệp |
| `test_valuation.py` | 4 | Trung vị và khoảng giá; loại mẫu không phù hợp/trùng/lỗi; không có mẫu; diện tích đầu vào không hợp lệ |
| `test_prediction.py` | 3 | Kiểm tra trường nhập; batch giữ thứ tự/ID và lỗi từng dòng; trường tùy chọn trong CSV |
| `test_api.py` | 6 | Health, định giá, lỗi đầu vào, bearer auth, batch của Manager và từ chối Admin ở API định giá |
| `test_auth.py` | 17 | Băm mật khẩu, phân quyền API quản trị, hồ sơ, audit, hoạt động, migration, thu hồi phiên, lỗi lưu dữ liệu và email chứa ký tự đặc biệt |
| `test_forum_media.py` | 5 | Chuẩn hóa ảnh diễn đàn, giới hạn dung lượng/số lượng, định dạng và gỡ metadata |
| `test_identity.py` | 2 | Ánh xạ group/email và xác minh chữ ký, issuer, audience, hạn ID token OIDC |
| `test_session_auth.py` | 7 | Đọc danh tính, kiểm tra hạn session, xác thực lại nội bộ, mất kết nối, dữ liệu phiên không hợp lệ và token OIDC |

Tổng cộng **79 ca kiểm thử** trong các bộ được liệt kê. Kiểm thử tương tác và lưu trữ sử dụng thư mục tạm cùng các đường dẫn dữ liệu được thay thế trong test để không sửa JSON hoặc ảnh thật. AppTest kiểm tra luồng chạy và trạng thái; bố cục, màu sắc và hiển thị trên từng kích thước màn hình cần kiểm tra thêm trong trình duyệt.

## Bổ sung luồng định giá

Giữ nguyên sidebar, theme và các màn hình hiện có. Trang **Phân tích & định giá** bổ sung mục thu gọn cho tiêu đề, phòng ngủ/phòng tắm, số tầng, mặt tiền, độ rộng đường, pháp lý, hướng, dự án và mô tả. Trường tùy chọn có thể để trống; khi nhập, kích thước phải dương và số phòng/tầng phải là số nguyên không âm. Dữ liệu không hợp lệ sẽ ngăn tính kết quả.

Thuật toán vẫn chỉ dùng **khu vực, loại bất động sản và diện tích**. Thông tin bổ sung phục vụ đối chiếu thủ công, chưa điều chỉnh giá. Khu vực lấy trực tiếp từ danh mục JSON; chưa có danh mục hành chính tỉnh/huyện/phường hoặc API dự đoán bên ngoài.

Kết quả hiển thị ngày định giá theo UTC+7, thời gian phản hồi API, ngày đăng mới nhất trong mẫu, số mẫu hợp lệ/bị loại và khoảng giá. Khoảng giá là **ước lượng heuristic theo mẫu**, không phải khoảng tin cậy đã hiệu chuẩn, không cam kết sai số. Không tự tạo điểm `feature_quality_score` khi chưa có mô hình/API cung cấp.

Manager có thể mở **Định giá hàng loạt bằng CSV** ngay trong trang định giá, tải CSV mẫu và tải kết quả. Tệp UTF-8 có các cột bắt buộc `location`, `type`, `area`, cùng cột tùy chọn `id`; tối đa 500 dòng và 2 MB. Mỗi dòng kết quả có số dòng dữ liệu (bắt đầu từ 1, không tính header), ID gốc, trạng thái, lỗi, số mẫu, giá và ngày định giá. Một dòng lỗi không làm mất kết quả các dòng khác. Thay tệp đầu vào hoặc danh mục sẽ xóa kết quả cũ; kết quả chỉ lưu trong phiên. CSV xuất dùng UTF-8 BOM và bảo vệ ô văn bản khỏi công thức bảng tính.

API báo thời gian phản hồi và readiness của estimator/danh mục. Phương pháp hiện tại là trung vị đơn giá tin so sánh, không phải mô hình ML. UI không tự tạo confidence score hoặc feature quality score khi chưa có mô hình đã hiệu chuẩn; khoảng giá là min/max trong tập mẫu, không phải khoảng tin cậy. Đặc điểm pháp lý, hướng, phòng, mặt tiền, tỉnh/quận và mô tả được nhận ở form để đối chiếu thủ công nhưng hiện chưa điều chỉnh kết quả.
# Cập nhật API và Docker

Ứng dụng đã tách định giá đơn lẻ và CSV sang FastAPI, bổ sung Docker Compose,
giao diện mới và báo cáo HTML tải xuống. Xem [hướng dẫn API và Docker](DOCKER.md).
Hướng dẫn này thay thế thông tin cũ phía dưới về việc chưa có backend và cách khởi động định giá.
