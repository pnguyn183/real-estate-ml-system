import pytest

from agents.safety import is_synthetic_record, real_data_query, real_records


@pytest.mark.parametrize("marker", [
    {"is_synthetic": True}, {"is_synthetic": "true"},
    {"source_type": "stress_agent"}, {"source_type": "synthetic"},
    {"generated_by": "stress_agent"}, {"url": "https://synthetic.invalid/run/item"},
])
def test_all_entry_points_identify_synthetic(marker):
    assert is_synthetic_record(marker)
    assert real_records([marker, {"url": "https://real.example/listing"}]) == [
        {"url": "https://real.example/listing"}
    ]


def test_existing_real_records_are_preserved():
    records = [{"price_vnd": 1}, {"source_type": "website", "is_synthetic": False}]
    assert real_records(iter(records)) == records


def test_mongo_query_preserves_existing_filters():
    original = {"$or": [{"is_price_anomaly": False}, {"is_price_anomaly": None}]}
    query = real_data_query(original)
    assert query["$and"][0] == original
    assert query["$and"][1]["source_type"]["$nin"] == ["synthetic", "stress_agent", "stress", "test"]


def test_training_rejects_all_synthetic_before_feature_build(monkeypatch, tmp_path):
    from modeling.price_model import RealEstatePriceModel, evaluate_feature_variants
    monkeypatch.setenv("MIN_RECORDS_FOR_TRAINING", "2")
    records = [{"is_synthetic": True, "price_vnd": 5e9, "area_m2": 70}] * 10
    with pytest.raises(ValueError, match="price_vnd"):
        RealEstatePriceModel().train(records, str(tmp_path / "model.joblib"))
    with pytest.raises(ValueError, match="price_vnd"):
        evaluate_feature_variants(records)
    assert not list(tmp_path.iterdir())
