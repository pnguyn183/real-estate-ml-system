"""Offline source-adapter orchestration: no website/Kafka needed for unit tests."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from scraper import multi_source
from scraper.http_policy import ScraperFetchError, ScraperPolicyError
from scraper.kafka_producer import KafkaDeliveryError, main, publish_records
from scraper.listing_feature_scraper import ScrapeConfig
from scraper.source_metrics import RunMetrics, SourceCollector
from scraper.sources import get_adapter
from scraper.sources.base import SourceShapeError


FIXTURES = Path(__file__).parent / "fixtures" / "sources"


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("CRAWL_METRICS_DIR", str(tmp_path / "metrics"))
    session = Mock()
    client = Mock()
    monkeypatch.setattr(multi_source, "MeasuredSession", Mock(return_value=session))
    monkeypatch.setattr(multi_source, "PoliteHTTPClient", Mock(return_value=client))
    return ScrapeConfig(max_items=1, state_file=tmp_path / "state.json"), client, session, tmp_path


def fixture(source, kind):
    return (FIXTURES / f"{source}-{kind}.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("source", ["alonhadat", "homedy"])
def test_adapter_checkpoint_follows_ack_and_resume_skips_seen(configured, source):
    config, client, session, temporary = configured
    client.get_html.side_effect = [fixture(source, "list"), fixture(source, "detail")]
    iterator = multi_source.iter_source_records(source, config)
    row = next(iterator)
    checkpoint = temporary / f"state-{source}.json"
    assert not checkpoint.exists()
    assert row["schema_version"] == 2 and row["source"] == source
    with pytest.raises(StopIteration):
        next(iterator)
    assert json.loads(checkpoint.read_text())["seen_urls"] == [row["url"]]
    session.close.assert_called_once()
    client.get_html.reset_mock(side_effect=True)
    client.get_html.return_value = fixture(source, "list")
    assert list(multi_source.iter_source_records(source, config)) == []
    assert client.get_html.call_count == 1  # no repeat detail request


def test_unacknowledged_delivery_leaves_source_replayable(configured):
    config, client, _, temporary = configured
    client.get_html.side_effect = [fixture("alonhadat", "list"), fixture("alonhadat", "detail")]
    producer = Mock()
    producer.flush.return_value = 1
    with pytest.raises(KafkaDeliveryError):
        publish_records(producer, multi_source.iter_source_records("alonhadat", config), "real_estate_raw", 1)
    assert not (temporary / "state-alonhadat.json").exists()
    counters = json.loads((temporary / "metrics" / "alonhadat.json").read_text())
    assert counters["crawl_failure"] == 1 and not counters.get("crawl_success")


def test_changed_detail_structure_emits_quarantined_record(configured):
    config, client, _, _ = configured
    client.get_html.side_effect = [fixture("alonhadat", "list"), "<html><h1>unrelated</h1></html>"]
    row, = list(multi_source.iter_source_records("alonhadat", config))
    assert "missing_alonhadat_detail_structure" in row["validation_errors"]
    assert row["price_raw"] is None and row["training_excluded"]
    assert row["source_url"].startswith("https://alonhadat.com.vn/")


def test_changed_listing_structure_fails_explicitly(configured):
    config, client, _, temporary = configured
    client.get_html.return_value = "<html>new layout</html>"
    with pytest.raises(SourceShapeError):
        list(multi_source.iter_source_records("homedy", config))
    counters = json.loads((temporary / "metrics" / "homedy.json").read_text())
    assert counters["crawl_failure"] == 1


@pytest.mark.parametrize("error", [ScraperFetchError("http_404"), ScraperPolicyError("access_denied")])
def test_http_failure_is_not_a_success_or_checkpoint(configured, error):
    config, client, _, temporary = configured
    client.get_html.side_effect = error
    with pytest.raises(type(error)):
        list(multi_source.iter_source_records("homedy", config))
    assert not (temporary / "state-homedy.json").exists()
    assert not RunMetrics("homedy").values.get("last_success")


def test_crawl_disabled_needs_neither_network_nor_kafka(monkeypatch):
    monkeypatch.setenv("CRAWL_ENABLED", "false")
    producer = Mock(side_effect=AssertionError("Kafka must not be created"))
    iterator = Mock(side_effect=AssertionError("network must not start"))
    monkeypatch.setattr("scraper.kafka_producer.Producer", producer)
    monkeypatch.setattr("scraper.kafka_producer.iter_source_records", iterator)
    assert main(["--sources", "alonhadat,guland,homedy"]) == 0
    producer.assert_not_called()
    iterator.assert_not_called()


def test_source_metrics_are_persistent_and_bounded(configured):
    metrics = RunMetrics("homedy")
    metrics.inc("listings_parsed", 2)
    metrics.inc("request_count", 3)
    metrics.inc("request_seconds", .5)
    metrics.success()
    metrics.save()
    assert RunMetrics("homedy").values["listings_parsed"] == 2
    registry = CollectorRegistry()
    registry.register(SourceCollector())
    exposition = generate_latest(registry).decode()
    assert 'listings_parsed_total{source="homedy"} 2.0' in exposition
    assert 'request_latency_seconds_count{source="homedy"} 3.0' in exposition
    assert 'source_metrics_available{source="guland"} 0.0' in exposition
    assert 'source_metrics_available{source="homedy"} 1.0' in exposition
    assert "https://" not in exposition
    with pytest.raises(ValueError):
        RunMetrics("unbounded-label")


def test_unreadable_metric_state_does_not_report_success(configured):
    _, _, _, temporary = configured
    metrics = RunMetrics("alonhadat")
    metrics.path.parent.mkdir(parents=True, exist_ok=True)
    metrics.path.write_text("invalid json", encoding="utf-8")
    registry = CollectorRegistry()
    registry.register(SourceCollector())
    exposition = generate_latest(registry).decode()
    assert 'source_metrics_available{source="alonhadat"} 0.0' in exposition
    assert 'crawl_success_total{source="alonhadat"}' not in exposition


@pytest.mark.parametrize("source", ["alonhadat", "homedy", "guland"])
def test_pagination_never_leaves_source_or_guesses(source):
    adapter = get_adapter(source)
    assert adapter.next_page('<a href="https://evil.invalid/p2">next</a>', adapter.category_url) is None
    assert adapter.next_page("<html></html>", adapter.category_url) is None
