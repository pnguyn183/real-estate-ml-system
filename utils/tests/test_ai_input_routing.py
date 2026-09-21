"""The Processor must route the same source facts the extraction service reads."""

import pytest

from agents.extraction import source_text
from utils.tests.test_agent_pipeline import make_pipeline, listing, synthetic_listing


@pytest.mark.parametrize("facts", [
    {"price": 5_000_000_000, "area": 80, "bedrooms": 3},
    {"location": "Binh Thanh, Ho Chi Minh"},
    {"phong_ngu": "3", "phong_tam": "2"},
    {"property_description": "Apartment 80 m2, total price 5 billion VND"},
    {"price_text": "5 tỷ", "area_text": "80 m2"},
])
def test_supported_source_facts_are_queued_without_title_or_description(make_pipeline, facts):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    record = {"url": "https://example.org/semi-structured/listing", **facts}
    assert source_text(record)
    result = pipeline.process_payload(record)
    assert result["ai_status"] == "queued"
    assert not pipeline.feature_collection.documents
    assert not pipeline.invalid_collection.documents
    assert pipeline.clean_producer.sent[0][0] == pipeline.ai_topic
    assert pipeline.clean_producer.sent[0][2]["record"] == record


@pytest.mark.parametrize("facts", [
    {}, {"location": "   "}, {"title": ["not a supported scalar"]},
    {"description": {"unexpected": "object"}}, {"price": True},
])
def test_absent_or_unsupported_source_text_is_not_sent_to_ai(make_pipeline, facts):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    record = {"url": "https://example.org/empty/listing", **facts}
    assert not source_text(record)
    result = pipeline.process_payload(record)
    assert result["ai_error_code"] == "no_extractable_text_or_url"
    assert result["listing_review_status"] == "INVALID"
    assert all(topic != pipeline.ai_topic for topic, _, _ in pipeline.clean_producer.sent)


def test_canonical_record_with_additional_source_fields_stays_deterministic(make_pipeline):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    result = pipeline.process_payload(listing(location="Ho Chi Minh", bedrooms=2))
    assert result["processing_method"] == "deterministic"
    assert result["is_model_candidate"] is True
    assert all(topic != pipeline.ai_topic for topic, _, _ in pipeline.clean_producer.sent)


def test_disabled_ai_keeps_existing_optional_field_behavior(make_pipeline):
    pipeline = make_pipeline()
    result = pipeline.process_payload({"url": "https://example.org/numeric/listing", "price": 5e9, "area": 80})
    assert result["processing_method"] == "deterministic"
    assert result["is_model_candidate"] is False
    assert all(topic != pipeline.ai_topic for topic, _, _ in pipeline.clean_producer.sent)


def test_supported_facts_do_not_cross_synthetic_boundary(make_pipeline):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    result = pipeline.process_payload(synthetic_listing(
        title=None, price_text=None, area_text=None, property_type=None,
        price=5e9, area=80, location="Ho Chi Minh",
    ))
    assert result["processing_method"] == "origin_rejected"
    assert not pipeline.raw_collection.documents
    assert not pipeline.clean_producer.sent
