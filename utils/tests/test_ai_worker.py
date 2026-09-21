import copy
import json
from unittest.mock import MagicMock

import pytest

from agents.extraction import ExtractionService
from agents.providers import DisabledProvider
from agents.worker import AIWorker, WorkerConfig
from processing.kafka_to_mongo import agent_event_id


class State:
    def __init__(self):
        self.items = {}

    def find_one(self, query):
        return copy.deepcopy(self.items.get(query["_id"]))

    def update_one(self, query, update, upsert=False):
        self.items.setdefault(query["_id"], {}).update(copy.deepcopy(update["$set"]))


@pytest.fixture
def worker_case():
    record = {"url": "https://synthetic.invalid/unit/listing", "is_synthetic": True,
              "source_type": "stress_agent", "description": "70 m2, 5 tỷ"}
    envelope = {"schema_version": 1, "origin": "stress", "record": record,
                "event_id": agent_event_id(record)}
    worker = AIWorker(WorkerConfig(stress_enabled=True), ExtractionService(DisabledProvider()))
    worker.consumer = MagicMock()
    worker.producer = MagicMock()
    pipeline = MagicMock()
    state, responses = State(), State()
    pipeline.db.__getitem__.side_effect = {"ai_extractions": state, "ai_response_cache": responses}.__getitem__
    pipeline.raw_collection.find_one.return_value = {"_agent_event_id": envelope["event_id"]}
    pipeline.process_payload.return_value = {
        "url": record["url"], "price_vnd": 5e9, "area_m2": 70, "is_model_candidate": False,
    }
    worker.pipelines = {"stress": pipeline}
    worker.publish_dlq = MagicMock()
    worker.publish_result = MagicMock()
    return worker, pipeline, state, envelope


def successful_result(envelope):
    return {"status": "success", "record": {**envelope["record"],
            "price_text": "5 tỷ", "area_text": "70 m2", "property_type": "apartment"}}


def test_success_uses_result_topic_not_direct_feature_writes(worker_case):
    worker, pipeline, state, envelope = worker_case
    worker.extraction.extract = MagicMock(return_value=successful_result(envelope))
    assert worker.handle(envelope) == "success"
    sent_envelope, result = worker.publish_result.call_args.args
    assert sent_envelope == envelope
    assert result["record"]["is_synthetic"] is True
    assert worker.handle(envelope) == "success"
    assert worker.extraction.extract.call_count == 1
    pipeline.process_payload.assert_not_called()
    assert worker.publish_result.call_count == 1


def test_output_failure_redelivery_reuses_cached_response(worker_case):
    worker, pipeline, state, envelope = worker_case
    worker.extraction.extract = MagicMock(return_value=successful_result(envelope))
    worker.publish_result.side_effect = RuntimeError("result_broker_down")
    with pytest.raises(RuntimeError):
        worker.handle(envelope)
    assert state.items[envelope["event_id"]]["status"] == "extracted"
    worker.publish_result.side_effect = None
    assert worker.handle(envelope) == "success"
    assert worker.extraction.extract.call_count == 1


def test_failure_result_delivery_failure_never_commits(worker_case):
    worker, pipeline, state, envelope = worker_case
    worker.extraction.extract = MagicMock(return_value={"status": "failed", "error_code": "provider_timeout"})
    worker.publish_result.side_effect = RuntimeError("result_down")
    message = MagicMock()
    message.value.return_value = json.dumps(envelope).encode()
    with pytest.raises(RuntimeError):
        worker.handle_message(message)
    worker.consumer.commit.assert_not_called()
    assert state.items[envelope["event_id"]]["status"] == "extracted"


def test_invalid_bytes_dlq_before_commit(worker_case):
    worker, _, _, _ = worker_case
    order = []
    worker.publish_dlq.side_effect = lambda *args: order.append("dlq")
    worker.consumer.commit.side_effect = lambda **kwargs: order.append("commit")
    message = MagicMock()
    message.value.return_value = b"\xff\x00"
    assert worker.handle_message(message) == "malformed"
    assert order == ["dlq", "commit"]


def test_stale_result_does_not_call_model(worker_case):
    worker, pipeline, _, envelope = worker_case
    pipeline.raw_collection.find_one.return_value = {"_agent_event_id": "newer"}
    worker.extraction.extract = MagicMock()
    assert worker.handle(envelope) == "stale"
    worker.extraction.extract.assert_not_called()
    pipeline.process_payload.assert_not_called()


def test_forged_origin_rejected(worker_case):
    _, _, _, envelope = worker_case
    envelope["origin"] = "real"
    with pytest.raises(ValueError, match="synthetic_boundary"):
        AIWorker.validate_envelope(envelope)


def test_missing_archive_is_not_silently_processed(worker_case):
    worker, pipeline, _, envelope = worker_case
    pipeline.raw_collection.find_one.return_value = None
    assert worker.handle(envelope) == "stale"
    pipeline.process_payload.assert_not_called()


def test_stress_gate_does_not_call_provider(worker_case):
    worker, _, _, envelope = worker_case
    worker.config = WorkerConfig(stress_enabled=False)
    worker.extraction.extract = MagicMock()
    assert worker.handle(envelope) == "failed"
    worker.extraction.extract.assert_not_called()
    assert worker.publish_result.call_args.args[1]["error_code"] == "stress_ai_disabled"


def test_changed_scrape_timestamp_reuses_exact_content_response(worker_case):
    worker, pipeline, _, envelope = worker_case
    worker.extraction.extract = MagicMock(return_value=successful_result(envelope))
    assert worker.handle(envelope) == "success"
    later = copy.deepcopy(envelope)
    later["record"]["scraped_at"] = "2026-09-08T01:00:00Z"
    later["event_id"] = agent_event_id(later["record"])
    pipeline.raw_collection.find_one.return_value = {"_agent_event_id": later["event_id"]}
    assert worker.handle(later) == "success"
    assert worker.extraction.extract.call_count == 1
    assert worker.publish_result.call_count == 2
    assert worker.publish_result.call_args.args[1]["record"]["scraped_at"] == later["record"]["scraped_at"]


def test_partial_extraction_sent_for_review_not_accepted(worker_case):
    worker, _, _, envelope = worker_case
    worker.extraction.extract = MagicMock(return_value={"status": "success", "record": envelope["record"]})
    assert worker.handle(envelope) == "failed"
    assert worker.publish_result.call_args.args[1]["error_code"] == "post_extraction_validation"


def test_no_commit_when_malformed_dlq_delivery_fails(worker_case):
    worker, _, _, _ = worker_case
    worker.publish_dlq.side_effect = RuntimeError("dlq_down")
    message = MagicMock()
    message.value.return_value = b"\xff"
    with pytest.raises(RuntimeError):
        worker.handle_message(message)
    worker.consumer.commit.assert_not_called()
