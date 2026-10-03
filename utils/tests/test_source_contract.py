from pathlib import Path
import copy
import json
from unittest.mock import Mock

import pytest

from processing.source_contract import area_value, price_value, normalize_contract, make_record
from processing.kafka_to_mongo import normalize_listing, validate_normalized_record
from scraper.sources import ADAPTERS, get_adapter
from scraper.sources.base import SourceShapeError
from scraper.multi_source import iter_source_records, selected_sources
from scraper.listing_feature_scraper import ScrapeConfig
from scraper.http_policy import ScraperPolicyError
from scraper.kafka_producer import publish_records
from utils.tests.test_agent_pipeline import make_pipeline, Message
from agents.safety import real_records

FIXTURES = Path(__file__).parent / "fixtures/sources"
URLS = {"alonhadat": "https://alonhadat.com.vn/nha-kiem-thu-101.html",
        "homedy": "https://homedy.com/ban-nha-rieng-ha-noi/nha-kiem-thu-es101",
        "guland": "https://guland.vn/post/dat-kiem-thu-101"}


def fixture(source, kind):
    return (FIXTURES / f"{source}-{kind}.html").read_text(encoding="utf-8")


def record(source):
    return get_adapter(source).parse(fixture(source, "detail"), URLS[source])


@pytest.mark.parametrize("raw,value", [("2 tỷ", 2e9), ("2,5 tỷ", 2.5e9), ("2.5 tỷ", 2.5e9),
    ("850 triệu", 850e6), ("850 tr", 850e6), ("850.000.000", 850e6), ("850,000,000", 850e6),
    ("5000000000 VND", 5e9), ("5,000 triệu", 5e9)])
def test_price_units_and_separators(raw, value):
    result, error = price_value(raw, 80)
    assert error is None and result["price_vnd"] == value


@pytest.mark.parametrize("raw", ["5-7 tỷ", "-5 tỷ", "50 triệu/m ngang", "2,50,000", "khoảng năm tỷ", "2.5", "NaN", "0 tỷ", "501 tỷ"])
def test_ambiguous_prices_do_not_become_training_targets(raw):
    result, error = price_value(raw, 80)
    assert error and result["price_vnd"] is None


@pytest.mark.parametrize("raw", ["Thỏa thuận", "Liên hệ"])
def test_negotiable_is_null_not_zero(raw):
    result, error = price_value(raw)
    assert result["price_is_negotiable"] and result["price_vnd"] is None and error is None


def test_unit_price_requires_scalar_area_and_retains_unit():
    result, _ = price_value("50 triệu/m²", 80)
    assert result["price_vnd"] == 4e9 and result["price_unit"] == "vnd_per_m2"
    result, _ = price_value("50 triệu/m2")
    assert result["price_vnd"] is None and result["price_per_m2_vnd"] == 50e6


@pytest.mark.parametrize("raw,expected", [("80 m²",80), ("80m2",80), ("80,5 m²",80.5), ("1.200 m²",1200)])
def test_area(raw, expected):
    assert area_value(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["50–70 m²", "4 x 20 m", "1 ha", "-80 m²", "0 m²", "20000m2", "NaN m2"])
def test_unsafe_area(raw):
    value, error = area_value(raw)
    assert value is None and error


@pytest.mark.parametrize("source", list(ADAPTERS))
def test_adapter_discovery_and_contract(source):
    adapter = get_adapter(source)
    assert adapter.discover(fixture(source, "list")) == [URLS[source]]
    row = record(source)
    assert row["schema_version"] == 2 and row["source"] == source
    assert row["source_listing_id"] == "101" and row["source_url"] == row["canonical_url"] == row["url"]
    assert len(row["raw_payload_hash"]) == 64 and row["crawl_timestamp"]
    assert row["latitude"] is None and row["longitude"] is None
    assert row["raw_data"] and row["price_raw"] and row["area_raw"]
    json.dumps(row, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("source", list(ADAPTERS))
def test_missing_html_fails_explicitly(source):
    with pytest.raises(SourceShapeError):
        get_adapter(source).parse("<html>changed layout</html>", URLS[source])
    with pytest.raises(SourceShapeError):
        get_adapter(source).discover("<html>changed layout</html>")


@pytest.mark.parametrize("source,ending", [("alonhadat", "/trang-2"), ("homedy", "/p2")])
def test_only_observed_pagination(source, ending):
    adapter = get_adapter(source)
    assert adapter.next_page(fixture(source, "list"), adapter.category_url) == adapter.category_url + ending
    assert adapter.next_page("<a href='https://evil.invalid/page2'>next</a>", adapter.category_url) is None


def test_old_current_addresses_not_combined_and_floors_not_guessed():
    row = record("alonhadat")
    assert row["ward"] == "Phường Cũ"
    assert row["district"] == "Quận Hai Bà Trưng"
    assert row["address_version"] == "legacy"
    assert row["floor_count"] is None and row["raw_data"]["source_floor_raw"] == "2"
    assert row["posted_at"] == "2026-09-17"


def test_homedy_range_preserved_and_invalid_not_first_number():
    row = get_adapter("homedy").parse(fixture("homedy", "detail").replace("80 m<sup>2</sup>", "70-90 m²"), URLS["homedy"])
    assert row["area_raw"] == "70-90 m²" and row["area_m2"] is None
    assert row["validation_errors"] and row["training_excluded"]


def test_guland_offline_and_disabled_no_http(monkeypatch, tmp_path):
    monkeypatch.setenv("CRAWL_METRICS_DIR", str(tmp_path))
    blocked = Mock(side_effect=AssertionError("network forbidden"))
    monkeypatch.setattr("scraper.multi_source.MeasuredSession", blocked)
    with pytest.raises(ScraperPolicyError):
        list(iter_source_records("guland", ScrapeConfig()))
    blocked.assert_not_called()
    row = record("guland")
    assert row["price_vnd"] == 520e6 and row["area_m2"] == 436
    assert row["training_excluded"] and row["posted_at"] is None


@pytest.mark.parametrize("source", ["alonhadat", "homedy"])
def test_processor_and_feature_contract_preserve_provenance(make_pipeline, source):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    raw = record(source)
    result = pipeline.process_payload(raw)
    assert result["price_vnd"] == 2.5e9 and result["area_m2"] == 80
    assert result["source_listing_id"] == "101" and result["source"] == source
    assert result["is_model_candidate"] is True
    assert not any(topic == pipeline.ai_topic for topic, _, _ in pipeline.clean_producer.sent)
    pipeline.process_payload(raw)
    assert len(pipeline.feature_collection.documents) == 1
    assert real_records([result]) == [result]


def test_equal_listing_ids_different_sources_are_not_merged(make_pipeline):
    pipeline = make_pipeline()
    for source in ("alonhadat", "homedy"):
        pipeline.process_payload(record(source))
    assert len(pipeline.feature_collection.documents) == 2


def test_invalid_new_version_excludes_previous_candidate(make_pipeline):
    pipeline = make_pipeline()
    raw = record("alonhadat")
    pipeline.process_payload(raw)
    changed = {**raw, "area_raw": "50-80 m2"}
    result = pipeline.process_payload(changed)
    assert result["validation_errors"]
    old = pipeline.feature_collection.documents[0]
    assert old["training_excluded"] and not old["is_model_candidate"]
    assert real_records([old]) == []
    assert len(pipeline.invalid_collection.documents) == 1


def test_numeric_payload_cannot_override_original_units():
    row = record("alonhadat")
    row.update(price_vnd=1, area_m2=1)
    validated = normalize_contract(row)
    assert validated["price_vnd"] == 2.5e9 and validated["area_m2"] == 80


def test_no_batdongsan_source_is_registered():
    with pytest.raises(ValueError):
        selected_sources("batdongsan")
    assert selected_sources("alonhadat,homedy,alonhadat") == ["alonhadat", "homedy"]


def test_unrecognized_schema_cannot_enter_manual_training():
    assert real_records([{"schema_version":3, "is_model_candidate":True, "price_vnd":2e9}]) == []


def test_ai_can_read_original_ambiguous_values_but_does_not_trust_them():
    from agents.extraction import source_text, known_raw_fields
    row = record("alonhadat")
    row["area_raw"] = "70-90 m2"
    row = normalize_contract(row)
    assert "70-90 m2" in source_text(row)
    assert "area_text" not in known_raw_fields(row)


@pytest.mark.parametrize("source", list(ADAPTERS))
def test_synthetic_versioned_data_never_primary_training(make_pipeline, source):
    pipeline = make_pipeline()
    raw = record(source)
    raw.update(is_synthetic=True, source_type="synthetic", url=f"https://synthetic.invalid/contract/{source}/101")
    rejected = pipeline.process_payload(raw)
    assert not pipeline.feature_collection.documents
    normalized = pipeline.stress_pipeline.process_payload(raw)
    assert normalized["is_synthetic"] and not normalized["is_model_candidate"]
    assert real_records([normalized]) == []


def test_ai_result_revalidates_canonical_fields_and_preserves_raw(make_pipeline):
    from agents.results import result_payload
    pipeline = make_pipeline()
    raw = record("alonhadat")
    raw.update(price_raw=None, price_text=None)
    merged = result_payload(raw, {"status":"success", "record":{"price_text":"2.5 tỷ"}, "confidence":.9, "attempts":1}, "test")
    result = pipeline.process_payload(merged, skip_ai=True)
    assert result["price_vnd"] == 2.5e9 and result["price_raw"] is None
    assert result["ai_confidence"] == .9 and result["processing_method"] == "ai_extraction"
