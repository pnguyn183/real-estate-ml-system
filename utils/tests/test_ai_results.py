"""Result-topic integration through the real Processor with in-memory adapters."""
import copy
import json

import pytest

from agents.results import handle_ai_result, validate_result_envelope
from processing.kafka_to_mongo import agent_event_id
from utils.tests.test_agent_pipeline import make_pipeline, listing, synthetic_listing, Message


def result_envelope(pipeline, *, synthetic=False, status="success", fields=None):
    original = synthetic_listing(price_text=None) if synthetic else listing(price_text=None)
    selected = pipeline.stress_pipeline if synthetic else pipeline
    selected.ai_enabled = True
    selected.process_payload(original)
    envelope = {
        "schema_version": 1, "origin": "stress" if synthetic else "real",
        "event_id": agent_event_id(original), "record": original,
        "result": {
            "status": status, "record": {"price_text": "8 ty", **(fields or {})} if status == "success" else None,
            "attempts": 1, "confidence": .9, "provider": "test", "model": "fixture",
            "error_code": "provider_timeout" if status == "failed" else None,
        },
    }
    return envelope, selected


def consume_result(pipeline, envelope):
    pipeline.consumer.messages = [Message(json.dumps(envelope).encode(), "real_estate_ai_results")]
    pipeline.consume_forever(max_messages=1)


def test_ai_result_consumed_by_existing_group_and_shared_validator(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline)
    consume_result(pipeline, envelope)
    feature = selected.feature_collection.documents[0]
    assert feature["price_vnd"] == 8_000_000_000
    assert feature["is_model_candidate"] is True
    assert feature["processing_method"] == "ai_extraction"
    assert selected.raw_collection.documents[0]["price_text"] is None
    assert selected.db["ai_result_receipts"].documents[0]["outcome"] == "success"
    assert pipeline.consumer.commits == [("real_estate_ai_results", 0, 8)]
    assert len([event for event in selected.clean_producer.sent if event[0] == selected.ai_topic]) == 1


def test_completed_result_redelivery_does_not_repeat_processing(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline)
    handle_ai_result(envelope, selected)
    count = len(selected.clean_producer.sent)
    snapshot = copy.deepcopy(selected.feature_collection.documents)
    assert handle_ai_result(envelope, selected)["processing_method"] == "ai_result_replay"
    assert len(selected.clean_producer.sent) == count
    assert selected.feature_collection.documents == snapshot


def test_synthetic_provenance_cannot_be_changed_by_model(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline, synthetic=True, fields={
        "url": "https://real.example/forged", "is_synthetic": False,
        "source_type": "website", "is_model_candidate": True, "ai_status": "disabled",
        "area_text": "90 m2",  # known structured source is 80 and must win
    })
    consume_result(pipeline, envelope)
    feature = selected.feature_collection.documents[0]
    assert feature["url"] == envelope["record"]["url"]
    assert feature["is_synthetic"] is True
    assert feature["source_type"] == "synthetic"
    assert feature["is_model_candidate"] is False
    assert feature["area_m2"] == 80
    assert not pipeline.feature_collection.documents


def test_invalid_results_fail_business_validation_and_are_recoverable(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline, fields={"price_text": "900 ty"})
    consume_result(pipeline, envelope)
    assert not selected.feature_collection.documents
    assert selected.invalid_collection.documents[0]["is_model_candidate"] is False
    assert selected.db["ai_failures"].documents[0]["error_code"] == "post_extraction_validation"
    assert selected.db["ai_result_receipts"].documents[0]["outcome"] == "invalid"
    assert any(topic == selected.ai_dlq_topic for topic, _, _ in selected.clean_producer.sent)


def test_failed_provider_result_archived_before_acknowledged_commit(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline, status="failed")
    consume_result(pipeline, envelope)
    assert selected.db["ai_failures"].documents[0]["error_code"] == "provider_timeout"
    assert selected.invalid_collection.documents[0]["ai_status"] == "failed"
    assert selected.db["ai_result_receipts"].documents[0]["outcome"] == "failed"
    assert pipeline.consumer.commits


def test_result_dlq_failure_never_commits_or_marks_receipt_completed(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline, status="failed")
    selected.clean_producer.fail_delivery = True
    with pytest.raises(RuntimeError, match="DLQ not acknowledged"):
        consume_result(pipeline, envelope)
    assert not selected.db["ai_result_receipts"].documents
    assert not pipeline.consumer.commits


def test_result_mongo_failure_never_commits(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline)
    selected.feature_collection.fail = True
    with pytest.raises(RuntimeError, match="database unavailable"):
        consume_result(pipeline, envelope)
    assert not pipeline.consumer.commits
    assert not selected.db["ai_result_receipts"].documents


@pytest.mark.parametrize("change", [
    {"event_id": "forged"}, {"origin": "alien"}, {"result": {"status": "unknown"}},
])
def test_malformed_result_is_durably_preserved_before_commit(make_pipeline, change):
    pipeline = make_pipeline()
    envelope, _ = result_envelope(pipeline)
    envelope.update(change)
    consume_result(pipeline, envelope)
    assert pipeline.dlq_collection.documents[0]["topic"] == "real_estate_ai_results"
    assert not pipeline.feature_collection.documents
    assert pipeline.consumer.commits


def test_forged_synthetic_origin_cannot_select_live_database(make_pipeline):
    pipeline = make_pipeline()
    envelope, _ = result_envelope(pipeline, synthetic=True)
    envelope["origin"] = "real"
    with pytest.raises(ValueError, match="synthetic_boundary"):
        validate_result_envelope(envelope)
    consume_result(pipeline, envelope)
    assert not pipeline.feature_collection.documents
    assert not pipeline.raw_collection.documents


def test_stale_result_does_not_overwrite_a_newer_listing(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline)
    selected.process_payload(listing(price_text="9 ty"))
    assert handle_ai_result(envelope, selected)["ai_status"] == "stale"
    assert selected.feature_collection.documents[0]["price_vnd"] == 9_000_000_000
    assert selected.db["ai_result_receipts"].documents[0]["outcome"] == "stale"


def test_non_scalar_extraction_cannot_become_features(make_pipeline):
    pipeline = make_pipeline()
    envelope, selected = result_envelope(pipeline, fields={"price_text": {"amount": 8}})
    consume_result(pipeline, envelope)
    assert not selected.feature_collection.documents
    assert selected.db["ai_failures"].documents[0]["error_code"] == "invalid_extracted_field_type"


def test_commit_failure_stops_before_polling_later_offset(make_pipeline):
    pipeline = make_pipeline()
    pipeline.consumer.messages = [
        Message(json.dumps(listing()).encode(), offset=1),
        Message(json.dumps(listing(price_text="9 ty")).encode(), offset=2),
    ]

    def fail_commit(**kwargs):
        raise RuntimeError("commit unavailable")

    pipeline.consumer.commit = fail_commit
    with pytest.raises(RuntimeError, match="commit unavailable"):
        pipeline.consume_forever(max_messages=2)
    assert len(pipeline.consumer.messages) == 1
