"""Processor boundaries: synthetic isolation, AI hand-off and durable offsets."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

import processing.kafka_to_mongo as module


def test_docker_script_entrypoint_can_reimport_shared_parsers():
    # Run in a fresh interpreter to reproduce Docker's __main__ import identity.
    # --help exits before any Kafka/Mongo connection; the module remains reusable.
    probe = '''
import runpy, sys
sys.argv = ["processing/kafka_to_mongo.py", "--help"]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as exc:
    assert exc.code == 0
import processing.kafka_to_mongo as pipeline
from agents.results import validate_result_envelope
record = {"url": "https://example.org/listing"}
envelope = {"schema_version": 1, "origin": "real", "record": record,
            "event_id": pipeline.agent_event_id(record),
            "result": {"status": "failed"}}
assert validate_result_envelope(envelope)[0] == "real"
'''
    result = subprocess.run([sys.executable, "-c", probe],
                            cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


class Collection:
    def __init__(self):
        self.documents = []
        self.fail = False

    def create_index(self, *args, **kwargs):
        return None

    def find_one(self, query, projection=None):
        for record in self.documents:
            if all(record.get(key) == value for key, value in query.items()):
                return copy.deepcopy(record)
        return None

    def update_one(self, query, update, upsert=False):
        if self.fail:
            raise RuntimeError("database unavailable")
        for record in self.documents:
            if all(record.get(key) == value for key, value in query.items()):
                record.update(copy.deepcopy(update["$set"]))
                return
        self.documents.append({**query, **copy.deepcopy(update["$set"])})

    def insert_one(self, record):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.documents.append(copy.deepcopy(record))


class Database(dict):
    def __missing__(self, key):
        self[key] = Collection()
        return self[key]


class Mongo(dict):
    def __missing__(self, key):
        self[key] = Database()
        return self[key]

    def close(self):
        pass


class Producer:
    def __init__(self, config):
        self.sent = []
        self.pending = []
        self.fail_delivery = False
        self.never_deliver = False

    def produce(self, topic, key, value, callback=None):
        self.sent.append((topic, key, json.loads(value)))
        if callback:
            self.pending.append(callback)

    def poll(self, timeout):
        if not self.never_deliver:
            while self.pending:
                self.pending.pop(0)("broker failed" if self.fail_delivery else None, None)

    def flush(self, timeout=None):
        self.poll(0)
        return len(self.pending)


class Message:
    def __init__(self, payload, topic="real_estate_raw", offset=7):
        self.payload, self.topic_name, self.position = payload, topic, offset

    def value(self):
        return self.payload

    def topic(self):
        return self.topic_name

    def partition(self):
        return 0

    def offset(self):
        return self.position

    def error(self):
        return None


class Consumer:
    def __init__(self, config):
        self.config = config
        self.messages = []
        self.commits = []
        self.closed = False

    def subscribe(self, topics, **callbacks):
        self.topics = topics

    def poll(self, timeout):
        if not self.messages:
            raise AssertionError("Test exhausted input without terminating")
        return self.messages.pop(0)

    def commit(self, message, asynchronous):
        assert asynchronous is False
        self.commits.append((message.topic(), message.partition(), message.offset() + 1))

    def get_watermark_offsets(self, partition, cached):
        assert cached is True
        return 0, 8

    def close(self):
        self.closed = True


@pytest.fixture
def make_pipeline(monkeypatch, tmp_path):
    mongo = Mongo()
    monkeypatch.setattr(module, "MongoClient", lambda uri: mongo)
    monkeypatch.setattr(module, "Consumer", Consumer)
    monkeypatch.setattr(module, "Producer", Producer)
    monkeypatch.setenv("TEXT_EMBEDDING_CACHE_PATH", str(tmp_path / "embeddings.sqlite"))
    monkeypatch.setenv("AI_FALLBACK_ENABLED", "false")
    monkeypatch.setenv("AI_STRESS_ENABLED", "false")
    monkeypatch.setenv("KAFKA_STRESS_TOPIC", "real_estate_stress_raw")
    monkeypatch.setenv("KAFKA_AI_TOPIC", "real_estate_ai_input")
    monkeypatch.setenv("KAFKA_AI_DELIVERY_TIMEOUT_SECONDS", "0.01")
    module._shutdown_event.clear()

    def create(**kwargs):
        pipeline = module.KafkaToMongoPipeline(
            "kafka:29092", "real_estate_raw", "real_estate_features",
            "real_estate_training_pipeline", "mongodb://mongo/",
            kwargs.pop("mongo_db", "real_estate_db"), **kwargs,
        )
        for item in (pipeline, pipeline.stress_pipeline):
            if item:
                item.price_anomaly_detector = Mock()
                item.price_anomaly_detector.refresh_if_needed.return_value = False
                item.price_anomaly_detector.metrics.return_value = {}
        return pipeline

    return create


def listing(**updates):
    return {
        "url": "https://batdongsan.com.vn/example-listing", "title": "Căn hộ 80m2 giá 8 tỷ",
        "price_text": "8 tỷ", "area_text": "80m2", "property_type": "apartment",
        "province_slug": "ho-chi-minh", "district_slug": "quan-1", **updates,
    }


def synthetic_listing(**updates):
    return listing(
        url="https://synthetic.invalid/run-1/record-1", is_synthetic=True,
        source_type="synthetic", generated_by="stress_agent", run_id="run-1",
        original_record_id="record-1", scenario="structured", **updates,
    )


def test_traffic_completion_latency_is_recorded_only_after_commit(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    handling, end_to_end, outcomes = Mock(), Mock(), Mock()
    monkeypatch.setattr(module, "processor_input_handling", handling)
    monkeypatch.setattr(module, "processor_input_end_to_end", end_to_end)
    monkeypatch.setattr(module, "processor_input_outcomes", outcomes)

    def assert_committed(_duration):
        assert pipeline.consumer.commits == [("real_estate_stress_raw", 0, 8)]

    end_to_end.labels.return_value.observe.side_effect = assert_committed
    payload = synthetic_listing(stress_sent_at=module.time.time() - 1)
    pipeline.consumer.messages = [Message(json.dumps(payload).encode(), topic="real_estate_stress_raw")]
    pipeline.consume_forever(max_messages=1)
    end_to_end.labels.return_value.observe.assert_called_once()
    handling.labels.return_value.observe.assert_called_once()
    outcomes.labels.assert_called_once_with("real_estate_stress_raw", "handled")


def test_traffic_commit_failure_is_not_counted_as_handled_or_latency(make_pipeline, monkeypatch):
    pipeline = make_pipeline()
    handling, end_to_end, outcomes = Mock(), Mock(), Mock()
    monkeypatch.setattr(module, "processor_input_handling", handling)
    monkeypatch.setattr(module, "processor_input_end_to_end", end_to_end)
    monkeypatch.setattr(module, "processor_input_outcomes", outcomes)
    pipeline.consumer.commit = Mock(side_effect=RuntimeError("commit rejected"))
    payload = synthetic_listing(stress_sent_at=module.time.time() - 1)
    pipeline.consumer.messages = [Message(json.dumps(payload).encode(), topic="real_estate_stress_raw")]
    with pytest.raises(RuntimeError, match="commit rejected"):
        pipeline.consume_forever(max_messages=1)
    end_to_end.labels.assert_not_called()
    handling.labels.assert_not_called()
    outcomes.labels.assert_called_once_with("real_estate_stress_raw", "failed")


@pytest.mark.parametrize("high,expected", [(12, 4), (7, 0), (-1001, None)])
def test_consumer_lag_uses_nonblocking_cache_and_skips_unknown_watermark(make_pipeline, monkeypatch, high, expected):
    pipeline = make_pipeline()
    pipeline.consumer.get_watermark_offsets = Mock(return_value=(0, high))
    lag = Mock()
    monkeypatch.setattr(module, "kafka_consumer_lag", lag)
    pipeline.update_consumer_lag(Message(b"{}"))
    call = pipeline.consumer.get_watermark_offsets.call_args
    assert call.kwargs == {"cached": True}
    assert len(call.args) == 1
    assert (call.args[0].topic, call.args[0].partition) == ("real_estate_raw", 0)
    if expected is None:
        lag.set.assert_not_called()
    else:
        lag.set.assert_called_once_with(expected)


@pytest.mark.parametrize("commit_fails", [False, True])
def test_malformed_input_metrics_respect_commit_boundary(make_pipeline, monkeypatch, commit_fails):
    pipeline = make_pipeline()
    handling, end_to_end, outcomes = Mock(), Mock(), Mock()
    monkeypatch.setattr(module, "processor_input_handling", handling)
    monkeypatch.setattr(module, "processor_input_end_to_end", end_to_end)
    monkeypatch.setattr(module, "processor_input_outcomes", outcomes)
    pipeline.consumer.messages = [Message(b"{broken")]
    if commit_fails:
        pipeline.consumer.commit = Mock(side_effect=RuntimeError("commit rejected"))
        with pytest.raises(RuntimeError, match="commit rejected"):
            pipeline.consume_forever(max_messages=1)
        handling.labels.assert_not_called()
        outcomes.labels.assert_called_once_with("real_estate_raw", "failed")
    else:
        def assert_committed(_duration):
            assert pipeline.consumer.commits == [("real_estate_raw", 0, 8)]

        handling.labels.return_value.observe.side_effect = assert_committed
        pipeline.consume_forever(max_messages=1)
        handling.labels.return_value.observe.assert_called_once()
        outcomes.labels.assert_called_once_with("real_estate_raw", "dlq")
    end_to_end.labels.assert_not_called()
    assert len(pipeline.dlq_collection.documents) == 1


def test_current_group_owns_ingestion_and_result_topics_without_extra_consumer(make_pipeline):
    pipeline = make_pipeline()
    assert pipeline.consumer.topics == ["real_estate_raw", "real_estate_stress_raw", "real_estate_ai_results"]
    assert pipeline.consumer.config["group.id"] == "real_estate_training_pipeline"
    assert pipeline.consumer.config["enable.auto.commit"] is False
    assert pipeline.stress_pipeline.consumer is None
    assert pipeline.stress_pipeline.allow_synthetic is True


def test_persistence_only_pipeline_does_not_join_a_consumer_group(make_pipeline):
    pipeline = make_pipeline(consumer_enabled=False)
    assert pipeline.consumer is None
    assert pipeline.stress_pipeline is None
    with pytest.raises(RuntimeError, match="no Kafka consumer"):
        pipeline.consume_forever()
    pipeline.close()


def test_synthetic_enable_cannot_target_live_database(make_pipeline):
    with pytest.raises(ValueError, match="isolated"):
        make_pipeline(allow_synthetic=True, origin="stress")


def test_normal_record_never_calls_ai_and_preserves_behavior(make_pipeline):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    result = pipeline.process_payload(listing())
    assert result["is_model_candidate"] is True
    assert len(pipeline.feature_collection.documents) == 1
    assert all(topic != pipeline.ai_topic for topic, _, _ in pipeline.clean_producer.sent)


def test_disabled_ai_retains_historical_missing_optional_behavior(make_pipeline):
    pipeline = make_pipeline()
    result = pipeline.process_payload(listing(price_text=None, area_text=None, bedroom_text=None))
    assert result["is_model_candidate"] is False
    assert len(pipeline.feature_collection.documents) == 1
    assert not pipeline.invalid_collection.documents


def test_incomplete_listing_archived_original_then_durably_queued(make_pipeline):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    original = listing(price_text=None, area_text=None, property_type=None)
    snapshot = copy.deepcopy(original)
    result = pipeline.process_payload(original)
    assert original == snapshot
    assert result["ai_status"] == "queued"
    assert result["is_model_candidate"] is False
    assert not pipeline.feature_collection.documents
    assert not pipeline.invalid_collection.documents
    pipeline.price_anomaly_detector.refresh_if_needed.assert_not_called()
    archived = pipeline.raw_collection.documents[0]
    assert archived["price_text"] is None
    assert archived["_agent_event_id"] == module.agent_event_id(original)
    topic, key, envelope = pipeline.clean_producer.sent[0]
    assert topic == "real_estate_ai_input"
    assert key == original["url"]
    assert envelope["record"] == snapshot
    assert envelope["origin"] == "real"
    assert envelope["event_id"] == archived["_agent_event_id"]


@pytest.mark.parametrize("mode", ["failed", "timeout"])
def test_ai_delivery_failure_leaves_offset_uncommitted(make_pipeline, mode):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    pipeline.clean_producer.fail_delivery = mode == "failed"
    pipeline.clean_producer.never_deliver = mode == "timeout"
    pipeline.consumer.messages = [Message(json.dumps(listing(price_text=None)).encode())]
    with pytest.raises((RuntimeError, TimeoutError)):
        pipeline.consume_forever(max_messages=1)
    assert not pipeline.consumer.commits
    assert not pipeline.feature_collection.documents


def test_successful_ai_handoff_commits_after_delivery(make_pipeline):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    pipeline.consumer.messages = [Message(json.dumps(listing(price_text=None)).encode())]
    pipeline.consume_forever(max_messages=1)
    assert pipeline.consumer.commits == [("real_estate_raw", 0, 8)]
    assert not pipeline.clean_producer.pending
    assert not pipeline.feature_collection.documents


@pytest.mark.parametrize("status", ["success", "failed", "disabled"])
def test_terminal_ai_result_does_not_loop(make_pipeline, status):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    result = pipeline.process_payload(listing(price_text=None, ai_status=status, ai_attempted=True))
    assert result["listing_review_status"] == "INVALID"
    assert result["ai_status"] == status
    assert len(pipeline.invalid_collection.documents) == 1
    assert all(topic != pipeline.ai_topic for topic, _, _ in pipeline.clean_producer.sent)


def test_ai_result_revalidated_without_overwriting_original_archive(make_pipeline):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    original = listing(price_text=None)
    event_id = module.agent_event_id(original)
    pipeline.process_payload(original)
    result = pipeline.process_payload(
        listing(ai_status="success", processing_method="ai_extraction", ai_provider="provider",
                ai_confidence=0.92, _agent_event_id=event_id),
        skip_ai=True, raw_already_saved=True,
    )
    assert result["price_vnd"] == 8_000_000_000
    assert result["processing_method"] == "ai_extraction"
    assert result["ai_confidence"] == 0.92
    assert result["_agent_event_id"] == event_id
    assert pipeline.raw_collection.documents[0]["price_text"] is None
    assert len(pipeline.feature_collection.documents) == 1


def test_invalid_ai_result_cannot_become_training_features(make_pipeline):
    pipeline = make_pipeline()
    result = pipeline.process_payload(listing(price_text="900 tỷ", ai_status="success"), skip_ai=True)
    assert result["listing_review_status"] == "INVALID"
    assert result["is_model_candidate"] is False
    assert not pipeline.feature_collection.documents


def test_live_pipeline_rejects_synthetic_before_any_live_data_or_baseline(make_pipeline):
    pipeline = make_pipeline()
    record = synthetic_listing()
    result = pipeline.process_payload(record)
    pipeline.process_payload(record)
    assert result["processing_method"] == "origin_rejected"
    assert len(pipeline.dlq_collection.documents) == 1
    assert not pipeline.raw_collection.documents
    assert not pipeline.feature_collection.documents
    pipeline.price_anomaly_detector.refresh_if_needed.assert_not_called()


def test_stress_topic_routes_only_to_sandbox_and_preserves_provenance(make_pipeline):
    pipeline = make_pipeline()
    pipeline.consumer.messages = [Message(json.dumps(synthetic_listing()).encode(), "real_estate_stress_raw")]
    pipeline.consume_forever(max_messages=1)
    assert not pipeline.raw_collection.documents
    assert not pipeline.feature_collection.documents
    result = pipeline.stress_pipeline.feature_collection.documents[0]
    for field in ("source_type", "is_synthetic", "generated_by", "run_id", "original_record_id", "scenario"):
        assert result[field] == synthetic_listing()[field]
    assert result["is_model_candidate"] is False
    assert pipeline.consumer.commits == [("real_estate_stress_raw", 0, 8)]
    assert pipeline.stress_pipeline.db["offset_checkpoint"].documents[0]["topic"] == "real_estate_stress_raw"


@pytest.mark.parametrize("payload", [listing(), listing(url="https://synthetic.invalid/unmarked")])
def test_stress_pipeline_rejects_real_or_unmarked_records(make_pipeline, payload):
    stress = make_pipeline().stress_pipeline
    result = stress.process_payload(payload)
    assert result["listing_review_status"] == "INVALID"
    assert not stress.feature_collection.documents
    assert not stress.raw_collection.documents
    assert stress.dlq_collection.documents


def test_stress_ai_has_separate_opt_in_and_envelope_origin(make_pipeline):
    pipeline = make_pipeline()
    pipeline.ai_enabled = True
    stress = pipeline.stress_pipeline
    assert stress.ai_enabled is False
    stress.ai_enabled = True
    result = stress.process_payload(synthetic_listing(price_text=None))
    assert result["ai_status"] == "queued"
    assert stress.clean_producer.sent[0][2]["origin"] == "stress"
    assert not pipeline.raw_collection.documents


@pytest.mark.parametrize("collection", ["raw_collection", "feature_collection", "invalid_collection"])
def test_db_write_failure_does_not_commit_even_when_dlq_succeeds(make_pipeline, collection):
    pipeline = make_pipeline()
    getattr(pipeline, collection).fail = True
    record = listing(area_text="11000") if collection == "invalid_collection" else listing()
    pipeline.consumer.messages = [Message(json.dumps(record).encode())]
    with pytest.raises(RuntimeError, match="database unavailable"):
        pipeline.consume_forever(max_messages=1)
    assert not pipeline.consumer.commits
    assert pipeline.dlq_collection.documents


@pytest.mark.parametrize("payload", [b"\xff\xfe", b"{broken", b"[1,2]", None])
def test_malformed_input_is_preserved_as_bytes_before_commit(make_pipeline, payload):
    pipeline = make_pipeline()
    pipeline.consumer.messages = [Message(payload)]
    pipeline.consume_forever(max_messages=1)
    record = pipeline.dlq_collection.documents[0]
    assert base64.b64decode(record["raw_base64"]) == (payload or b"")
    assert record["is_tombstone"] is (payload is None)
    assert pipeline.consumer.commits == [("real_estate_raw", 0, 8)]


def test_failed_malformed_dlq_write_leaves_offset_uncommitted(make_pipeline):
    pipeline = make_pipeline()
    pipeline.dlq_collection.fail = True
    pipeline.consumer.messages = [Message(b"\xff")]
    with pytest.raises(RuntimeError, match="database unavailable"):
        pipeline.consume_forever(max_messages=1)
    assert not pipeline.consumer.commits


def test_bad_scalar_values_route_to_invalid_without_restart_loop(make_pipeline):
    pipeline = make_pipeline()
    result = pipeline.process_payload(listing(verified="not-a-number"))
    assert result["listing_review_status"] == "INVALID"
    assert "normalization_ValueError" in result["validation_errors"]
    assert not pipeline.feature_collection.documents


def test_event_id_ignores_mongo_bookkeeping_but_changes_with_source(make_pipeline):
    original = listing()
    identity = module.agent_event_id(original)
    assert module.agent_event_id({**original, "_id": "mongo-id", "_agent_event_id": "old"}) == identity
    assert module.agent_event_id(listing(price_text="9 tỷ")) != identity
