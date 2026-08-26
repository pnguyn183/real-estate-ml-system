from __future__ import annotations

from processing.feature_engineering import enrich_geographic_features
from processing.llm_review import OptionalLLMReviewer, parse_llm_review_response
from processing.price_anomaly import annotate_listing_review
from processing.text_enrichment import SQLiteTextEmbeddingCache
from modeling.price_model import CATEGORICAL_FEATURES, NUMERIC_FEATURES, build_feature_frame


def test_geographic_features_preserve_missing_and_reject_invalid_coordinates():
    assert enrich_geographic_features({}) == {
        "latitude": None,
        "longitude": None,
        "geo_grid_2dp": None,
        "geo_coordinate_status": "MISSING",
    }
    assert enrich_geographic_features({"latitude": 100, "longitude": 106})["geo_coordinate_status"] == "INVALID"
    valid = enrich_geographic_features({"lat": "21.0285", "lng": "105.8542"})
    assert valid["geo_coordinate_status"] == "VALID"
    assert valid["geo_grid_2dp"] == "21.03:105.85"


class CountingProvider:
    name = "test-provider"

    def __init__(self):
        self.calls = 0

    def embed_batch(self, texts):
        self.calls += 1
        return [[float(index) for index in range(32)] for _ in texts]


def test_embedding_cache_batches_and_reuses_unchanged_text(tmp_path):
    cache = SQLiteTextEmbeddingCache(tmp_path / "embedding.sqlite")
    provider = CountingProvider()
    records = [
        {"title": "Căn hộ 2 phòng ngủ", "description": "Sổ đỏ, nội thất đầy đủ, thang máy"},
        {"title": "Căn hộ 2 phòng ngủ", "description": "Sổ đỏ, nội thất đầy đủ, thang máy"},
    ]
    first = cache.enrich_many(records, provider)
    second = cache.enrich_many(records, provider)
    assert provider.calls == 1
    assert first == second
    assert first[0]["extracted_bedrooms"] == 2
    assert first[0]["extracted_legal_status"] == "redbook"
    assert first[0]["text_embedding_dimension"] == 32


def test_llm_response_is_strict_and_provider_failure_falls_back():
    valid = parse_llm_review_response(
        '{"classification":"suspicious","confidence":0.87,"reason":"price differs from peers",'
        '"suggested_action":"review","suggested_adjustment":null}'
    )
    assert valid.classification == "suspicious"
    try:
        parse_llm_review_response('{"classification":"suspicious"}')
    except ValueError:
        pass
    else:
        raise AssertionError("Malformed LLM JSON must not be accepted")

    class BrokenProvider:
        def review_listing(self, payload):
            raise TimeoutError("unavailable")

    result = OptionalLLMReviewer(BrokenProvider(), enabled=True).review({"listing_review_status": "SUSPICIOUS"})
    assert result["llm_review_status"] == "UNAVAILABLE"
    assert result["llm_review"] is None


def test_review_status_combines_data_quality_price_and_duplicate_without_deletion():
    invalid = annotate_listing_review({"url": "x"}, validation_errors=["price_non_positive"])
    assert invalid["listing_review_status"] == "INVALID"
    assert invalid["anomaly_type"] == "DATA_QUALITY"

    suspicious = annotate_listing_review(
        {"url": "x", "is_price_anomaly": True, "price_anomaly_score": 3.0, "price_anomaly_reason": "high"},
        is_duplicate=True,
    )
    assert suspicious["listing_review_status"] == "SUSPICIOUS"
    assert suspicious["anomaly_types"] == ["DUPLICATE", "PRICE"]
    assert suspicious["anomaly_score"] == 3.0


def test_training_and_inference_use_the_same_enriched_feature_schema():
    records = [
        {
            "area_m2": 80,
            "property_type": "apartment",
            "province_slug": "ha-noi",
            "district_slug": "dong-da",
            "title": "Căn hộ 2 phòng ngủ",
            "description": "Sổ đỏ, nội thất đầy đủ",
            "latitude": 21.0285,
            "longitude": 105.8542,
        },
        {
            "area_m2": 90,
            "property_type": "house",
            "province_slug": "ha-noi",
            "district_slug": "cau-giay",
            "title": "Nhà 3 phòng ngủ",
            "description": "Gara, sổ hồng",
        },
    ]
    frame = build_feature_frame(records)
    assert set(NUMERIC_FEATURES + CATEGORICAL_FEATURES).issubset(frame.columns)
    assert frame.loc[0, "geo_coordinate_status"] == "VALID"
    assert frame.loc[1, "geo_coordinate_status"] == "MISSING"
    assert frame.loc[0, "text_embedding_000"] is not None
