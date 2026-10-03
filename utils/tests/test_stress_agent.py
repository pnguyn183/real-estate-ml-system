from dataclasses import replace
import json
from threading import Event
from types import SimpleNamespace
from urllib.request import urlopen

import pytest
from prometheus_client import generate_latest

from agents.generator import ListingGenerator, SCENARIOS
from agents.stress import StressConfig, run_once, stress_topic
from agents.stress_metrics import StressMetrics


class FakeProducer:
    def __init__(self, config, fail=False, backpressure=False):
        self.config, self.fail, self.backpressure = config, fail, backpressure
        self.messages, self.pending = [], []

    def list_topics(self, topic, timeout):
        return SimpleNamespace(topics={topic: SimpleNamespace(error=None, partitions={0: None, 1: None, 2: None})})

    def produce(self, topic, key, value, callback):
        if self.backpressure:
            raise BufferError("full")
        self.messages.append((topic, key, json.loads(value)))
        self.pending.append(callback)

    def poll(self, timeout):
        for callback in self.pending:
            callback(RuntimeError("delivery") if self.fail else None, SimpleNamespace(partition=lambda: 1))
        self.pending.clear()

    def flush(self, timeout):
        self.poll(0)
        return 0


def test_stress_disabled_is_default_and_does_not_connect_or_write(tmp_path):
    config = StressConfig.from_env({})
    assert not config.enabled and config.rate == 1
    report = run_once(replace(config, state_root=str(tmp_path / "runs")), StressMetrics(), Event(), lambda _: pytest.fail("Kafka not allowed"))
    assert report["status"] == "disabled"
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("environment", [
    {"STRESS_ENABLED": "yes"}, {"STRESS_RATE_PER_SECOND": "nan"},
    {"STRESS_DURATION_SECONDS": "0"}, {"STRESS_MAX_RECORDS": "0"},
    {"STRESS_RUN_ID": "../escape"}, {"STRESS_SCENARIO": "unlimited"},
    {"STRESS_DUPLICATE_RATIO": "1.1"}, {"STRESS_RATE_PER_SECOND": "101"},
    {"STRESS_LOAD_PROFILE": "high", "STRESS_RATE_PER_SECOND": "11"},
])
def test_config_rejects_unbounded_or_invalid_runs(environment):
    with pytest.raises(ValueError):
        StressConfig.from_env(environment)


@pytest.mark.parametrize("topic", ["real_estate_raw", "real_estate_features", "orders", "stress/../../raw"])
def test_live_or_unscoped_topic_rejected(topic):
    with pytest.raises(ValueError):
        stress_topic(topic)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_generator_is_seeded_synthetic_and_truth_never_leaks(scenario):
    left = ListingGenerator("test-run", scenario, generated_at="2026-09-08T00:00:00+00:00")
    right = ListingGenerator("test-run", scenario, generated_at="2026-09-08T00:00:00+00:00")
    for index in range(5):
        sample = left.next(index)
        assert sample == right.next(index)
        payload = sample.payload
        assert payload["url"].startswith("https://synthetic.invalid/test-run/")
        assert payload["is_synthetic"] is True and payload["source_type"] == "stress_agent"
        assert payload["generated_by"] == "stress_agent"
        assert payload["generated_at"] and payload["original_record_id"].startswith("template-")
        assert "expected" not in payload and "ground_truth" not in payload
        assert sample.ground_truth["url"] == payload["url"]


def test_exact_and_near_duplicate_share_identity_and_price():
    generator = ListingGenerator("replay", "duplicate", duplicate_ratio=1)
    first = generator.next(0)
    near, exact = generator.next(1), generator.next(2)
    assert near.duplicate and exact.duplicate
    assert near.payload["url"] == first.payload["url"]
    assert near.payload["description"] != first.payload["description"]
    assert exact.payload == first.payload
    assert near.ground_truth["expected"] == first.ground_truth["expected"]
    alternate = generator.next(3)
    assert alternate.payload["url"] != first.payload["url"]
    assert alternate.payload["original_record_id"] == first.payload["original_record_id"]
    assert alternate.ground_truth["duplicate_kind"] == "alternate_url"


def test_volume_varies_correlated_area_and_price_and_semi_structured_uses_aliases():
    generator = ListingGenerator("volume", "volume")
    samples = [generator.next(i) for i in range(40)]
    assert len({(s.payload["area_text"], s.payload["price_text"]) for s in samples}) > 20
    assert all(20 <= s.ground_truth["expected"]["area_m2"] <= 140 for s in samples)
    semi = ListingGenerator("semi", "semi_structured").next(0)
    assert all(key in semi.payload for key in ("gia", "dien_tich", "phong_ngu"))
    assert "price_text" not in semi.payload


def test_vietnamese_number_words_have_matching_ground_truth():
    generator = ListingGenerator("words", "unstructured")
    samples = [generator.next(i) for i in range(100)]
    written = [s for s in samples if "bảy mươi" in s.payload["description"]]
    assert written
    assert all(s.ground_truth["expected"]["area_m2"] == 70 and s.ground_truth["expected"]["price_vnd"] == 5_000_000_000 for s in written)


def test_unstructured_input_hides_structured_answers():
    sample = ListingGenerator("text", "unstructured").next(0)
    assert sample.unstructured and sample.payload["raw_text"]
    for field in ("price_text", "area_text", "property_type", "province_slug", "bedroom_text"):
        assert field not in sample.payload
    assert sample.ground_truth["expected"]["price_vnd"] > 0


def test_bounded_run_counts_acknowledgments_persists_truth_and_never_replays(tmp_path):
    factory_calls = []

    def factory(settings):
        producer = FakeProducer(settings)
        factory_calls.append(producer)
        return producer

    config = StressConfig(enabled=True, state_root=str(tmp_path), rate=100, duration=1, max_records=3)
    metrics = StressMetrics()
    result = run_once(config, metrics, Event(), factory)
    assert result["status"] == "completed"
    assert result["generated"] == result["delivered"] == 3 and result["failed"] == 0
    assert result["partitions"] == {"1": 3}
    assert factory_calls[0].config["enable.idempotence"] is True
    truth = [json.loads(line) for line in (tmp_path / "default" / "ground_truth.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(truth) == 3 and truth[0]["expected"]["price_vnd"] > 0
    assert run_once(config, metrics, Event(), factory)["status"] == "already_claimed"
    assert len(factory_calls) == 1


@pytest.mark.parametrize("failure", ["delivery", "backpressure"])
def test_failure_stops_finite_run_without_reporting_delivery(tmp_path, failure):
    config = StressConfig(enabled=True, state_root=str(tmp_path), max_records=10)
    producer = FakeProducer({}, fail=failure == "delivery", backpressure=failure == "backpressure")
    result = run_once(config, StressMetrics(), Event(), lambda _: producer)
    assert result["status"] == "failed" and result["failed"] == 1
    assert result["generated"] == 1 and result["delivered"] == 0


def test_stop_signal_prevents_new_publications(tmp_path):
    stopped = Event()
    stopped.set()
    config = StressConfig(enabled=True, state_root=str(tmp_path), max_records=3)
    result = run_once(config, StressMetrics(), stopped, FakeProducer)
    assert result["status"] == "interrupted" and result["generated"] == 0


def test_health_metrics_are_real_http_and_labels_have_no_run_identity():
    metrics = StressMetrics()
    metrics.delivered.labels("normal").inc()
    server = metrics.serve(0)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/health", timeout=2) as response:
            assert json.load(response) == {"status": "ok"}
        with urlopen(base + "/metrics", timeout=2) as response:
            text = response.read().decode()
        assert 'stress_delivered_messages_total{scenario="normal"} 1.0' in text
        assert "run_id" not in generate_latest(metrics.registry).decode()
    finally:
        server.shutdown()
        server.server_close()
