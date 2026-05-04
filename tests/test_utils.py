from processing.kafka_to_mongo import parse_price_to_vnd, parse_number, normalize_listing, validate_normalized_record

def test_parse_number():
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("3.5") == 3.5
    assert parse_number("1000") == 1000
    assert parse_number(None) is None
    assert parse_number(42) == 42

def test_parse_price_to_vnd():
    assert parse_price_to_vnd("2 tỷ", 100) == (2_000_000_000, 20_000_000)
    assert parse_price_to_vnd("3.5 triệu", 70) == (3_500_000, 50_000)
    assert parse_price_to_vnd("2 tỷ 500 triệu", 100) == (2_500_000_000, 25_000_000)
    assert parse_price_to_vnd("50 triệu/m2", 80) == (4_000_000_000, 50_000_000)
    assert parse_price_to_vnd(None, 50) == (None, None)

def test_normalize_listing():
    raw = {"url": "abc", "area_text": "100", "price_text": "2 tỷ", "title": "test"}
    norm = normalize_listing(raw)
    assert norm["area_m2"] == 100
    assert norm["price_vnd"] == 2_000_000_000
    assert norm["url"] == "abc"

def test_validate_normalized_record():
    norm = {"url": "abc", "price_vnd": 2_000_000_000, "area_m2": 100, "price_per_m2_vnd": 20_000_000}
    valid, errors = validate_normalized_record(norm)
    assert valid
    norm2 = {"url": None, "price_vnd": -1, "area_m2": 0, "price_per_m2_vnd": 0}
    valid2, errors2 = validate_normalized_record(norm2)
    assert not valid2
    assert "missing_url" in errors2
    assert "price_non_positive" in errors2
    assert "area_non_positive" in errors2
