from modeling.price_model import (
    PRICE_GROUP_COLUMN,
    RealEstatePriceModel,
    group_train_validation_test_split,
    prepare_price_dataset,
    property_group_key,
    sanitize_listing_text,
)

def test_train_and_predict(tmp_path):
    records = [
        {
            "url": f"url{i}",
            "area_m2": 100 + i,
            "bedroom_count": 2,
            "bathroom_count": 1,
            "floor_count": 1,
            "front_width_m": 5,
            "road_width_m": 6,
            "property_type": "apartment",
            "direction": "east",
            "legal": "redbook",
            "listing_type": "sell",
            "province_slug": "hanoi",
            "district_slug": "dongda",
            "ward_slug": "catlinh",
            "project_hint": "vinhome",
            "text_features": "vinhome apartment",
            "price_vnd": 2_000_000_000 + i * 1_000_000,
            "is_model_candidate": True,
        }
        for i in range(250)
    ]
    model_path = tmp_path / "model.joblib"
    metrics_path = tmp_path / "metrics.json"
    model = RealEstatePriceModel()
    result = model.train(records, str(model_path), str(metrics_path))
    assert result.sample_count == 250
    assert result.metrics["mae_vnd"] >= 0
    assert model_path.exists()
    assert metrics_path.exists()
    loaded = RealEstatePriceModel.load(str(model_path))
    evaluation = loaded.metadata["evaluation"]
    assert evaluation["split_strategy"] == "property_group_holdout_70_15_15"
    assert evaluation["random_seed"] == 42
    assert evaluation["test_fraction"] == 0.15
    assert evaluation["validation_fraction"] == 0.15
    assert evaluation["group_overlap_count"] == 0
    assert loaded.metadata["data_quality"]["duplicates_removed"] == 0
    assert 0 <= loaded.metadata["metrics"]["median_absolute_percentage_error"]
    pred = loaded.predict(records[0])
    assert "predicted_price_vnd" in pred
    assert pred["predicted_price_vnd"] > 0
    assert pred["confidence_low_vnd"] < pred["predicted_price_vnd"] < pred["confidence_high_vnd"]
    assert 0 <= pred["confidence_score"] <= 1
    assert pred["explanations"]


def test_price_text_is_redacted_before_feature_enrichment_and_duplicates_are_removed():
    records = [
        {
            "url": "https://example.test/one",
            "property_group_id": "property-one",
            "title": "Giá 2,5 tỷ - căn hộ hai phòng ngủ",
            "description": "Chốt 2.500.000.000 VND, nội thất đầy đủ",
            "price_vnd": 2_500_000_000,
            "area_m2": 70,
        },
        {
            "url": "https://example.test/one",
            "property_group_id": "property-one",
            "title": "Giá 2,5 tỷ - căn hộ hai phòng ngủ",
            "description": "Chốt 2.500.000.000 VND, nội thất đầy đủ",
            "price_vnd": 2_500_000_000,
            "area_m2": 70,
        },
        {
            "url": "https://example.test/alternate",
            "property_group_id": "property-one",
            "title": "Căn hộ hai phòng ngủ",
            "price_vnd": 2_600_000_000,
            "area_m2": 70,
        },
    ]
    frame, audit = prepare_price_dataset(records)
    assert len(frame) == 2
    assert audit["duplicates_removed"] == 1
    assert audit["property_group_count"] == 1
    assert audit["rows_with_price_text_redacted"] == 2
    assert "2.500.000.000" not in " ".join(frame["text_features"])
    assert "2,5 tỷ" not in " ".join(frame["text_features"])
    assert "[PRICE_REDACTED]" in sanitize_listing_text("Giá 2,5 tỷ", 2_500_000_000)


def test_property_group_split_is_disjoint_and_reproducible():
    records = []
    for group in range(20):
        for listing in range(2):
            records.append(
                {
                    "url": f"https://example.test/{group}/{listing}",
                    "property_group_id": f"property-{group}",
                    "title": "Căn hộ hai phòng ngủ",
                    "price_vnd": 2_000_000_000 + group * 10_000_000,
                    "area_m2": 70 + group,
                }
            )
    frame, _ = prepare_price_dataset(records)
    first = group_train_validation_test_split(frame)
    second = group_train_validation_test_split(frame)
    assert all((first[name] == second[name]).all() for name in first)
    groups = {
        name: set(frame.iloc[indexes][PRICE_GROUP_COLUMN])
        for name, indexes in first.items()
    }
    assert not groups["train"] & groups["validation"]
    assert not groups["train"] & groups["test"]
    assert not groups["validation"] & groups["test"]


def test_property_group_identity_does_not_depend_on_url_or_target():
    first = {
        "url": "https://source-a.test/listing-one",
        "address": "12 Nguyen Trai",
        "area_m2": 80,
        "property_type": "apartment",
        "district_slug": "district-one",
        "price_vnd": 4_000_000_000,
    }
    relisted = {
        **first,
        "url": "https://source-b.test/a-different-listing",
        "price_vnd": 4_500_000_000,
    }
    assert property_group_key(first) == property_group_key(relisted)


def test_price_shorthand_is_redacted_and_cannot_change_group_identity():
    assert sanitize_listing_text("Căn hộ 2 tỷ 5 cần bán") == "Căn hộ [PRICE_REDACTED] cần bán"
    assert sanitize_listing_text("Nhà phố 10.8T thương lượng") == "Nhà phố [PRICE_REDACTED] thương lượng"
    first = {
        "location_slug": "duong-nguyen-trai-phuong-1",
        "area_m2": 80,
        "property_type": "house",
        "bedroom_count": 3,
        "description": "Cần bán 2 tỷ 5",
        "price_vnd": 2_500_000_000,
    }
    relisted = {**first, "description": "Tin mới 2 tỷ 6", "price_vnd": 2_600_000_000}
    assert property_group_key(first) == property_group_key(relisted)
