"""Subject validation and batch processing for the valuation API."""

import math


def validate_subject(subject):
    errors = {}
    for field, label in (("location", "Khu vực"), ("type", "Loại bất động sản")):
        if not str(subject.get(field, "") or "").strip():
            errors[field] = f"{label}: vui lòng nhập thông tin."
    fields = {
        "area": ("Diện tích", True, True),
        "frontage": ("Mặt tiền", True, False),
        "road_width": ("Độ rộng đường", True, False),
        "bedrooms": ("Phòng ngủ", False, False),
        "bathrooms": ("Phòng tắm", False, False),
        "floors": ("Số tầng", False, False),
    }
    for field, (label, positive, required) in fields.items():
        value = subject.get(field)
        if value is None or value == "":
            if required:
                errors[field] = f"{label}: bắt buộc nhập."
            continue
        try:
            number = float(value)
            valid = not isinstance(value, bool) and math.isfinite(number)
            valid = valid and (number > 0 if positive else number >= 0)
            if field in {"bedrooms", "bathrooms", "floors"}:
                valid = valid and number.is_integer()
            if not valid:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            rule = "số dương" if positive else "số nguyên không âm"
            errors[field] = f"{label}: cần là {rule} hợp lệ."
    for field, label, limit in (("latitude", "Vĩ độ", 90), ("longitude", "Kinh độ", 180)):
        value = subject.get(field)
        if value is None or value == "":
            continue
        try:
            number = float(value)
            if isinstance(value, bool) or not math.isfinite(number) or abs(number) > limit:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            errors[field] = f"{label}: cần nằm trong khoảng {-limit} đến {limit}."
    return errors


def estimate_batch(properties, subjects):
    from estimator import estimate

    results = []
    for index, subject in enumerate(subjects, 1):
        row = {"Dòng dữ liệu": index, "ID": subject.get("id", ""),
               "Khu vực": subject.get("location", ""), "Loại": subject.get("type", ""),
               "Trạng thái": "", "Lỗi": "", "Giá tham khảo (₫)": None,
               "Giá thấp (₫)": None, "Giá cao (₫)": None, "Số mẫu": 0}
        errors = validate_subject(subject)
        if errors:
            row.update({"Trạng thái": "Dữ liệu không hợp lệ", "Lỗi": "; ".join(errors.values())})
        else:
            result = estimate(properties, subject["location"], subject["type"], float(subject["area"]))
            row.update({"Trạng thái": "Thành công" if result["samples"] else "Thiếu dữ liệu so sánh",
                        "Lỗi": "" if result["samples"] else "Không có tin bán hợp lệ cùng khu vực và loại.",
                        "Giá tham khảo (₫)": result["price"], "Giá thấp (₫)": result["low"],
                        "Giá cao (₫)": result["high"], "Số mẫu": len(result["samples"])})
        results.append(row)
    return results
