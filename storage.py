import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

DATA_DIR = Path(os.environ.get("HOMENEST_DATA_DIR", Path(__file__).parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "properties.json"
USERS_FILE = DATA_DIR / "users.json"
MESSAGES_FILE = DATA_DIR / "messages.json"
FORUM_FILE = DATA_DIR / "forum.json"
IMAGES_DIR = DATA_DIR / "images"
IMAGES_DIR.mkdir(exist_ok=True)


def normalize_text(value):
    return " ".join(str(value).lower().strip().split())


def default_properties():
    return [
        {
            "id": 1,
            "title": "Căn hộ cao cấp tại Quận 7",
            "type": "Căn hộ",
            "location": "Quận 7, TP.HCM",
            "price": 3200000000,
            "area": 85,
            "bedrooms": 2,
            "bathrooms": 2,
            "status": "Bán",
            "description": "Căn hộ mới xây, view đẹp, gần trung tâm thương mại, tiện ích đầy đủ.",
            "contact": "0901 234 567",
            "created_at": "2026-08-30 09:00:00",
            "user_id": "admin",
            "rating": 4.5,
            "reviews": 5,
            "images": []
        },
        {
            "id": 2,
            "title": "Nhà phố 3 tầng ở Bình Thạnh",
            "type": "Nhà phố",
            "location": "Bình Thạnh, TP.HCM",
            "price": 5600000000,
            "area": 120,
            "bedrooms": 4,
            "bathrooms": 3,
            "status": "Bán",
            "description": "Nhà phố tiện nghi, sân vườn, phù hợp gia đình đông người.",
            "contact": "0902 345 678",
            "created_at": "2026-08-29 15:40:00",
            "user_id": "admin",
            "rating": 4.8,
            "reviews": 8,
            "images": []
        },
        {
            "id": 3,
            "title": "Đất nền khu dân cư Long An",
            "type": "Đất nền",
            "location": "Long An",
            "price": 1800000000,
            "area": 150,
            "bedrooms": 0,
            "bathrooms": 0,
            "status": "Bán",
            "description": "Vị trí đẹp, mặt tiền rộng, tiềm năng đầu tư cao.",
            "contact": "0903 456 789",
            "created_at": "2026-08-28 11:20:00",
            "user_id": "admin",
            "rating": 4.2,
            "reviews": 3,
            "images": []
        },
        {
            "id": 4,
            "title": "Biệt thự view sông Thủ Đức",
            "type": "Biệt thự",
            "location": "Thủ Đức, TP.HCM",
            "price": 8900000000,
            "area": 220,
            "bedrooms": 5,
            "bathrooms": 4,
            "status": "Bán",
            "description": "Biệt thự 2 mặt tiền, hồ bơi, khu an ninh, view sông và cây xanh.",
            "contact": "0909 222 444",
            "created_at": "2026-09-02 08:30:00",
            "user_id": "minh",
            "rating": 4.9,
            "reviews": 12,
            "images": []
        },
        {
            "id": 5,
            "title": "Căn hộ studio gần trường đại học",
            "type": "Căn hộ",
            "location": "Quận 10, TP.HCM",
            "price": 2100000000,
            "area": 55,
            "bedrooms": 1,
            "bathrooms": 1,
            "status": "Cho thuê",
            "description": "Phù hợp sinh viên, nội thất cơ bản, khu vực yên tĩnh, giao thông thuận tiện.",
            "contact": "0905 110 220",
            "created_at": "2026-09-04 09:15:00",
            "user_id": "lan",
            "rating": 4.1,
            "reviews": 7,
            "images": []
        },
        {
            "id": 6,
            "title": "Nhà phố 2 tầng gần chợ Bình Tân",
            "type": "Nhà phố",
            "location": "Bình Tân, TP.HCM",
            "price": 4600000000,
            "area": 105,
            "bedrooms": 3,
            "bathrooms": 2,
            "status": "Bán",
            "description": "Dựng 2 tầng, mặt tiền 4m, gần chợ, trường học và trung tâm thương mại.",
            "contact": "0904 556 778",
            "created_at": "2026-09-05 14:20:00",
            "user_id": "nam",
            "rating": 4.4,
            "reviews": 9,
            "images": []
        },
        {
            "id": 7,
            "title": "Đất nền hợp lý khu mới phát triển",
            "type": "Đất nền",
            "location": "Đồng Nai",
            "price": 2400000000,
            "area": 180,
            "bedrooms": 0,
            "bathrooms": 0,
            "status": "Bán",
            "description": "Phù hợp đầu tư lâu dài, vị trí thuận tiện, đường nhựa, điện nước đầy đủ.",
            "contact": "0908 333 999",
            "created_at": "2026-09-06 13:00:00",
            "user_id": "hung",
            "rating": 4.6,
            "reviews": 6,
            "images": []
        },
        {
            "id": 8,
            "title": "Căn hộ 3 phòng ngủ gần trung tâm",
            "type": "Căn hộ",
            "location": "Quận 2, TP.HCM",
            "price": 5100000000,
            "area": 96,
            "bedrooms": 3,
            "bathrooms": 2,
            "status": "Bán",
            "description": "Căn đẹp, ban công rộng, nhiều ánh sáng tự nhiên, tiện ích nội khu.",
            "contact": "0907 890 111",
            "created_at": "2026-09-07 11:45:00",
            "user_id": "hoa",
            "rating": 4.7,
            "reviews": 11,
            "images": []
        },
        {
            "id": 9,
            "title": "Căn hộ căn góc view thành phố Gò Vấp",
            "type": "Căn hộ",
            "location": "Gò Vấp, TP.HCM",
            "price": 2750000000,
            "area": 78,
            "bedrooms": 2,
            "bathrooms": 2,
            "status": "Bán",
            "description": "Căn góc, ánh sáng 2 mặt, view thành phố, khu dân cư yên tĩnh, trực tiếp trung tâm thương mại.",
            "contact": "0911 443 332",
            "created_at": "2026-09-08 10:15:00",
            "user_id": "linh",
            "rating": 4.3,
            "reviews": 5,
            "images": []
        },
        {
            "id": 10,
            "title": "Nhà liền kề khu đô thị mới Bình Dương",
            "type": "Nhà phố",
            "location": "Bình Dương",
            "price": 4200000000,
            "area": 112,
            "bedrooms": 3,
            "bathrooms": 2,
            "status": "Bán",
            "description": "Khu dân cư mới, công viên, trường học, giao thông thuận tiện, phù hợp gia đình trẻ.",
            "contact": "0912 519 804",
            "created_at": "2026-09-09 08:40:00",
            "user_id": "quang",
            "rating": 4.6,
            "reviews": 10,
            "images": []
        },
        {
            "id": 11,
            "title": "Phòng trọ tiện nghi gần bệnh viện",
            "type": "Căn hộ",
            "location": "Quận 12, TP.HCM",
            "price": 900000000,
            "area": 30,
            "bedrooms": 1,
            "bathrooms": 1,
            "status": "Cho thuê",
            "description": "Phòng trọ tiện nghi, máy lạnh, vệ sinh khép kín, giữ xe miễn phí, an ninh 24/7.",
            "contact": "0908 777 110",
            "created_at": "2026-09-10 09:55:00",
            "user_id": "vy",
            "rating": 4.0,
            "reviews": 4,
            "images": []
        },
        {
            "id": 12,
            "title": "Biệt thự 5 phòng ngủ hồ bơi Đồng Nai",
            "type": "Biệt thự",
            "location": "Đồng Nai",
            "price": 12500000000,
            "area": 310,
            "bedrooms": 5,
            "bathrooms": 4,
            "status": "Bán",
            "description": "Biệt thự nghỉ dưỡng, khuôn viên rộng, hồ bơi, sân vườn, phòng gym và không gian gia đình.",
            "contact": "0918 990 101",
            "created_at": "2026-09-11 16:00:00",
            "user_id": "minh",
            "rating": 5.0,
            "reviews": 18,
            "images": []
        },
        {
            "id": 13,
            "title": "Đất nền mặt tiền đường lớn Long Thành",
            "type": "Đất nền",
            "location": "Long Thành, Đồng Nai",
            "price": 3600000000,
            "area": 200,
            "bedrooms": 0,
            "bathrooms": 0,
            "status": "Bán",
            "description": "Mặt tiền rộng, giao thông kết nối dễ dàng, phù hợp cho dự án kinh doanh hoặc đầu tư dài hạn.",
            "contact": "0913 666 222",
            "created_at": "2026-09-12 07:30:00",
            "user_id": "hung",
            "rating": 4.7,
            "reviews": 8,
            "images": []
        },
        {
            "id": 14,
            "title": "Căn hộ 2PN full nội thất Quận 9",
            "type": "Căn hộ",
            "location": "Quận 9, TP.HCM",
            "price": 2950000000,
            "area": 82,
            "bedrooms": 2,
            "bathrooms": 2,
            "status": "Cho thuê",
            "description": "Full nội thất, máy giặt, giường ngủ, ban công và không gian hẹn hò, phù hợp gia đình nhỏ.",
            "contact": "0901 765 432",
            "created_at": "2026-09-12 09:10:00",
            "user_id": "lan",
            "rating": 4.5,
            "reviews": 9,
            "images": []
        }
    ]


def normalize_property(property_data):
    """Supply display defaults without changing the original record or its owner."""
    return {
        "user_id": "",
        "rating": 0,
        "reviews": 0,
        "images": [],
        **property_data,
    }


def _save_json(path, data):
    """Replace a JSON file only after the complete new document has been written."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class DataStoreError(RuntimeError):
    """Raised when an existing JSON store cannot be read safely."""


def _read_json(path, expected_type, *, record_type=None):
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise DataStoreError(
            f"File dữ liệu {path.name} không phải JSON hợp lệ; dữ liệu gốc được giữ nguyên. "
            "Hãy kiểm tra file hoặc khôi phục từ bản sao lưu."
        ) from error
    except FileNotFoundError:
        raise
    except OSError as error:
        raise DataStoreError(
            f"Không thể đọc file dữ liệu {path.name}; dữ liệu gốc được giữ nguyên."
        ) from error

    if not isinstance(data, expected_type):
        raise DataStoreError(
            f"File dữ liệu {path.name} có cấu trúc không hợp lệ; dữ liệu gốc được giữ nguyên. "
            "Hãy kiểm tra file hoặc khôi phục từ bản sao lưu."
        )
    if record_type is not None and any(not isinstance(record, record_type) for record in data):
        raise DataStoreError(
            f"File dữ liệu {path.name} chứa bản ghi không hợp lệ; "
            "dữ liệu gốc được giữ nguyên."
        )
    return data


def _read_or_initialize(path, default_factory, save, expected_type, *, record_type=None):
    try:
        return _read_json(path, expected_type, record_type=record_type)
    except FileNotFoundError:
        data = default_factory()
        save(data)
        return data


def load_properties():
    return [normalize_property(item) for item in _read_or_initialize(
        DATA_FILE, default_properties, save_properties, list, record_type=Mapping,
    )]


def save_properties(properties):
    _save_json(DATA_FILE, properties)


def default_users():
    return {
        "admin": {
            "full_name": "Admin HomeNest",
            "email": "admin@homenest.vn",
            "phone": "0900 000 001",
            "bio": "Quản lý toàn bộ hệ thống và kiểm duyệt tin đăng."
        },
        "minh": {
            "full_name": "Minh Hoàng",
            "email": "minh@gmail.com",
            "phone": "0909 222 444",
            "bio": "Chủ đầu tư và người bán bất động sản lâu năm."
        },
        "lan": {
            "full_name": "Lan Anh",
            "email": "lananh@gmail.com",
            "phone": "0905 110 220",
            "bio": "Cho thuê căn hộ sinh viên và căn hộ nhỏ."
        },
        "nam": {
            "full_name": "Nam Trần",
            "email": "namtran@gmail.com",
            "phone": "0904 556 778",
            "bio": "Bán nhà phố và đất nền khu dân cư."
        },
        "hung": {
            "full_name": "Hưng Đặng",
            "email": "hung@gmail.com",
            "phone": "0908 333 999",
            "bio": "Đầu tư đất nền và bất động sản khu vực ngoại thành."
        },
        "hoa": {
            "full_name": "Hòa Nguyễn",
            "email": "hoa@gmail.com",
            "phone": "0907 890 111",
            "bio": "Chuyên bán căn hộ cao cấp và căn hộ gia đình."
        },
        "linh": {
            "full_name": "Linh Chi",
            "email": "linhchi@gmail.com",
            "phone": "0911 443 332",
            "bio": "Chuyên tư vấn căn hộ góc và nhà ở gia đình."
        },
        "quang": {
            "full_name": "Quang Sỹ",
            "email": "quangsy@gmail.com",
            "phone": "0912 519 804",
            "bio": "Mua bán nhà liền kề và căn hộ mới tại khu đô thị."
        },
        "vy": {
            "full_name": "Vy Nguyễn",
            "email": "vynguyen@gmail.com",
            "phone": "0908 777 110",
            "bio": "Tư vấn phòng trọ và căn hộ cho thuê ngắn hạn."
        }
    }


def default_messages():
    return [
        {
            "from": "admin",
            "to": "minh",
            "content": "Tin đăng của bạn đã được duyệt thành công.",
            "created_at": "2026-09-08 09:00:00"
        },
        {
            "from": "lan",
            "to": "admin",
            "content": "Mình muốn cập nhật thông tin cho căn hộ cho thuê.",
            "created_at": "2026-09-09 15:30:00"
        },
        {
            "from": "nam",
            "to": "minh",
            "content": "Mình muốn trao đổi về nhà phố gần chợ Bình Tân.",
            "created_at": "2026-09-10 11:10:00"
        },
        {
            "from": "linh",
            "to": "admin",
            "content": "Mình muốn xem các căn hộ view đẹp ở Quận 9 và Gò Vấp.",
            "created_at": "2026-09-11 10:10:00"
        },
        {
            "from": "vy",
            "to": "lan",
            "content": "Phòng trọ của mình vừa có người quan tâm, bạn có thể tư vấn thêm không?",
            "created_at": "2026-09-12 08:20:00"
        },
        {
            "from": "quang",
            "to": "minh",
            "content": "Khu nhà liền kề Bình Dương đang có nhu cầu rất cao, bạn có muốn trao đổi giá?",
            "created_at": "2026-09-12 15:45:00"
        }
    ]


def default_forum_posts():
    return [
        {
            "id": "welcome-home-nest",
            "category": "Bắt đầu",
            "title": "Chào mừng bạn đến với Diễn đàn HomeNest",
            "content": (
                "Đây là nơi cộng đồng cùng trao đổi kinh nghiệm mua, bán, thuê và "
                "tìm hiểu bất động sản. Hãy chia sẻ thông tin có ích, nêu rõ bối cảnh "
                "và tôn trọng các góc nhìn khác nhau.\n\n"
                "Để mọi người dễ tham gia, hãy chọn đúng chủ đề, đặt tiêu đề cụ thể "
                "và không đăng số điện thoại, giấy tờ hay dữ liệu cá nhân công khai."
            ),
            "author": "Đội ngũ HomeNest",
            "author_id": "homenest",
            "created_at": "2026-10-01T09:00:00+07:00",
            "pinned": True,
            "likes": [],
            "comments": [],
        },
        {
            "id": "home-viewing-checklist",
            "category": "Mua bán",
            "title": "Checklist khảo sát nhà trước khi xuống tiền",
            "content": (
                "Khi đi xem nhà, bạn thường kiểm tra những gì ngoài diện tích và nội thất?\n\n"
                "Một vài điểm nên ghi lại: tình trạng thực tế so với tin đăng; ánh sáng, "
                "tiếng ồn và lối đi; tiện ích quanh khu vực; các khoản phí phát sinh; "
                "và hồ sơ pháp lý cần được xác minh với cơ quan hoặc chuyên gia phù hợp.\n\n"
                "Mỗi căn nhà có bối cảnh riêng. Hãy đối chiếu nhiều nguồn và không chuyển "
                "tiền chỉ dựa trên thông tin trong một bài đăng."
            ),
            "author": "Đội ngũ HomeNest",
            "author_id": "homenest",
            "created_at": "2026-10-02T10:30:00+07:00",
            "pinned": True,
            "likes": [],
            "comments": [],
        },
        {
            "id": "reading-home-estimates",
            "category": "Định giá",
            "title": "Đọc mức giá tham khảo trên HomeNest sao cho đúng?",
            "content": (
                "Mức tham khảo được tính từ trung vị đơn giá của các tin rao bán tương đồng "
                "đang có trong danh mục. Đây là cách nhìn nhanh để bắt đầu so sánh, không phải "
                "giá giao dịch đã xác nhận hay kết quả thẩm định.\n\n"
                "Khi đối chiếu, hãy lưu ý số lượng mẫu, vị trí cụ thể, tình trạng tài sản, "
                "thời điểm đăng và các yếu tố pháp lý. Nếu dữ liệu còn ít, nên xem kết quả "
                "như một tín hiệu tham khảo và tìm thêm nguồn độc lập."
            ),
            "author": "Đội ngũ HomeNest",
            "author_id": "homenest",
            "created_at": "2026-10-03T08:45:00+07:00",
            "pinned": True,
            "likes": [],
            "comments": [],
        },
    ]


def load_users():
    users = _read_or_initialize(USERS_FILE, default_users, save_users, dict)
    if any(not isinstance(user, Mapping) for user in users.values()):
        raise DataStoreError(
            f"File dữ liệu {USERS_FILE.name} chứa hồ sơ không hợp lệ; "
            "dữ liệu gốc được giữ nguyên."
        )
    return users


def save_users(users):
    _save_json(USERS_FILE, users)


def load_messages():
    return _read_or_initialize(
        MESSAGES_FILE, default_messages, save_messages, list, record_type=Mapping,
    )


def save_messages(messages):
    _save_json(MESSAGES_FILE, messages)


def load_forum_posts():
    return _read_or_initialize(
        FORUM_FILE, default_forum_posts, save_forum_posts, list, record_type=Mapping,
    )


def save_forum_posts(posts):
    _save_json(FORUM_FILE, posts)


def detect_duplicate_properties(properties):
    groups = {}
    for item in properties:
        loc_key = normalize_text(item.get("location", "").split(",")[0])
        title_key = normalize_text(item.get("title", ""))
        compact_title = " ".join(title_key.split()[:4])
        price_bucket = round(float(item.get("price", 0)) / 500000000) * 500000000
        area_bucket = round(float(item.get("area", 0)) / 10) * 10
        signature = (item.get("type"), loc_key, compact_title, price_bucket, area_bucket)
        groups.setdefault(signature, []).append(item)

    duplicates = [group for group in groups.values() if len(group) > 1]
    duplicates.sort(key=lambda g: len(g), reverse=True)
    return duplicates
