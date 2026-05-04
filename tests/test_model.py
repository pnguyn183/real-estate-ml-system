from modeling.price_model import RealEstatePriceModel

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
    pred = loaded.predict(records[0])
    assert "predicted_price_vnd" in pred
    assert pred["predicted_price_vnd"] > 0
