import math

import pytest

from processing.price_anomaly import (
    PriceAnomalyConfig,
    PriceAnomalyDetector,
    add_anomaly_training_filter,
    safe_price_per_m2,
)


def listing(price_per_m2: float, district: str = "dong-da", property_type: str = "apartment", **extra):
    area = extra.pop("area_m2", 100.0)
    price = extra.pop("price_vnd") if "price_vnd" in extra else price_per_m2 * area
    return {
        "url": extra.pop("url", f"https://example.test/{district}/{property_type}/{price_per_m2}"),
        "price_vnd": price,
        "area_m2": area,
        "province_slug": extra.pop("province_slug", "ha-noi"),
        "district_slug": district,
        "property_type": property_type,
        **extra,
    }


def detector(min_group_size: int = 4) -> PriceAnomalyDetector:
    return PriceAnomalyDetector(
        PriceAnomalyConfig(
            min_group_size=min_group_size,
            primary_group_columns=("province_slug", "district_slug", "property_type"),
            fallback_group_columns=("province_slug", "property_type"),
            refresh_seconds=300,
        )
    )


def baseline_values() -> list[dict]:
    # Q1=100, Q3=103, IQR=3, bounds [95.5, 107.5] with linear interpolation.
    return [listing(value, url=f"https://example.test/baseline/{index}") for index, value in enumerate([99, 100, 101, 102, 103, 104])]


def test_normal_high_low_and_explainable_iqr_metadata():
    subject = detector()
    subject.fit(baseline_values())

    normal = subject.annotate(listing(102, url="normal"))
    high = subject.annotate(listing(250, url="high"))
    low = subject.annotate(listing(10, url="low"))

    assert normal["price_anomaly_status"] == "NORMAL"
    assert not normal["is_price_anomaly"]
    assert normal["price_anomaly_q1"] == pytest.approx(100.25)
    assert normal["price_anomaly_q3"] == pytest.approx(102.75)
    assert high["is_price_anomaly"] and high["price_anomaly_type"] == "HIGH"
    assert low["is_price_anomaly"] and low["price_anomaly_type"] == "LOW"
    assert high["price_anomaly_upper_bound"] == pytest.approx(106.5)
    assert high["price_anomaly_score"] > 0


@pytest.mark.parametrize(
    "price,area",
    [(None, 100), (1_000_000_000, None), (1_000_000_000, 0), (-1, 100), (float("nan"), 100), (float("inf"), 100)],
)
def test_invalid_price_or_area_is_not_silently_made_valid(price, area):
    subject = detector()
    record = subject.annotate(listing(100, price_vnd=price, area_m2=area, url=f"invalid-{price}-{area}"))
    assert record["price_per_m2_vnd"] is None
    assert record["price_anomaly_status"] == "UNAVAILABLE"
    assert record["price_anomaly_reason"] == "invalid_price_or_area"


def test_small_group_is_unavailable_but_fallback_group_can_be_used():
    subject = detector(min_group_size=4)
    reference = [listing(value, district="cau-giay", url=f"fallback-{value}") for value in [99, 100, 101, 102, 103, 104]]
    reference.extend([listing(100, district="dong-da", url="small-1"), listing(101, district="dong-da", url="small-2")])
    subject.fit(reference)

    record = subject.annotate(listing(250, district="dong-da", url="fallback-subject"))
    assert record["is_price_anomaly"]
    assert record["price_anomaly_group_columns"] == ["province_slug", "property_type"]

    no_fallback = PriceAnomalyDetector(
        PriceAnomalyConfig(min_group_size=4, primary_group_columns=("province_slug", "district_slug", "property_type"), fallback_group_columns=("ward_slug",))
    )
    no_fallback.fit([listing(100, url="tiny-1"), listing(101, url="tiny-2")])
    unavailable = no_fallback.annotate(listing(250, url="tiny-subject"))
    assert unavailable["price_anomaly_status"] == "UNAVAILABLE"


def test_zero_iqr_is_review_unavailable_not_automatic_anomaly():
    subject = detector()
    subject.fit([listing(100, url=f"same-{index}") for index in range(6)])
    record = subject.annotate(listing(1000, url="luxury-but-zero-iqr"))

    assert not record["is_price_anomaly"]
    assert record["price_anomaly_status"] == "UNAVAILABLE"
    assert record["price_anomaly_reason"] == "zero_iqr_baseline"
    assert record["price_anomaly_iqr"] == 0


def test_luxury_listing_is_flagged_but_not_removed_from_the_record():
    subject = detector()
    subject.fit(baseline_values())
    luxury = listing(300, url="luxury-listing", is_model_candidate=True, title="Luxury penthouse")

    annotated = subject.annotate(luxury)

    assert annotated["is_price_anomaly"]
    assert annotated["price_anomaly_type"] == "HIGH"
    assert annotated["is_model_candidate"] is True
    assert annotated["title"] == "Luxury penthouse"


def test_missing_group_multiple_districts_types_new_record_and_no_data():
    subject = detector()
    reference = baseline_values()
    reference.extend([listing(value, district="cau-giay", property_type="house", url=f"house-{value}") for value in [200, 201, 202, 203, 204, 205]])
    subject.fit(reference)

    district_high = subject.annotate(listing(250, district="dong-da", property_type="apartment", url="district-high"))
    house_normal = subject.annotate(listing(202, district="cau-giay", property_type="house", url="house-normal"))
    missing_group = subject.annotate(listing(100, district=None, property_type=None, url="missing-group"))

    assert district_high["price_anomaly_type"] == "HIGH"
    assert house_normal["price_anomaly_status"] == "NORMAL"
    assert missing_group["price_anomaly_reason"] == "missing_group_values"

    empty = detector()
    empty.fit([])
    new_record = empty.annotate(listing(100, url="new-no-history"))
    assert new_record["price_anomaly_status"] == "UNAVAILABLE"
    assert empty.metrics()["groups"] == 0


def test_safe_price_per_m2_rejects_non_finite_and_computes_valid_value():
    assert safe_price_per_m2(2_000_000_000, 100) == 20_000_000
    assert safe_price_per_m2(float("nan"), 100) is None
    assert safe_price_per_m2(1, float("inf")) is None
    assert safe_price_per_m2(1, -1) is None
    assert math.isfinite(safe_price_per_m2(1_000_000_000, 50))


class FakeCollection:
    def __init__(self, records=None):
        self.records = list(records or [])
        self.writes = []

    def find(self, query, projection):
        return [
            record
            for record in self.records
            if record.get("price_vnd", 0) > 0 and record.get("area_m2", 0) > 0
        ]

    def update_one(self, selector, update, upsert=False):
        self.writes.append((selector, update, upsert))


def test_new_listing_uses_historical_mongo_baseline_and_persists_thresholds():
    subject = detector()
    historical = FakeCollection(baseline_values())
    threshold_store = FakeCollection()
    incoming = listing(300, url="incoming-new-listing")

    assert subject.refresh_if_needed(historical, threshold_store, exclude_url=incoming["url"])
    result = subject.annotate(incoming)

    assert result["is_price_anomaly"]
    assert result["price_anomaly_type"] == "HIGH"
    assert result["price_anomaly_baseline_size"] == 6
    assert threshold_store.writes


def test_exclude_training_policy_filters_only_flagged_records():
    query = {"price_vnd": {"$gt": 0}, "is_model_candidate": True}
    assert add_anomaly_training_filter(query, "FLAG") == query
    assert add_anomaly_training_filter(query, "EXCLUDE")["is_price_anomaly"] == {"$ne": True}
    with pytest.raises(ValueError):
        add_anomaly_training_filter(query, "DROP")
