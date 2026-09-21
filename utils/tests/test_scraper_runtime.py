"""Offline crawler policy and acknowledged Kafka handoff checks; no live requests."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import requests

from scraper import http_policy, kafka_producer, listing_feature_scraper as scraper


class Response:
    def __init__(self, status=200, body="<html>listing</html>", content_type="text/html", **headers):
        self.status_code = status
        self.body = body.encode("utf-8")
        self.headers = {"Content-Type": content_type, **headers}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.body


@pytest.fixture
def clock(monkeypatch):
    state = {"time": 0.0, "sleeps": []}

    def sleep(delay):
        state["sleeps"].append(delay)
        state["time"] += delay

    monkeypatch.setattr(http_policy.time, "monotonic", lambda: state["time"])
    monkeypatch.setattr(http_policy.time, "sleep", sleep)
    monkeypatch.setattr(http_policy.random, "uniform", lambda low, high: low)
    return state


def client(responses, **options):
    session = Mock()
    session.get.side_effect = responses
    return http_policy.PoliteHTTPClient(session, scraper.ScrapeConfig(**options), scraper.BASE_URL)


def robots(text="User-agent: *\nAllow: /\n"):
    return Response(body=text, content_type="text/plain; charset=utf-8")


def test_robots_checked_before_page_and_requests_are_polite(clock):
    policy = client([robots("User-agent: *\nCrawl-delay: 7\nRequest-rate: 1/10\n"), Response()])
    assert policy.get_html(scraper.BASE_URL + "/nha-dat-ban") == "<html>listing</html>"
    calls = policy.session.get.call_args_list
    assert calls[0].args[0].endswith("/robots.txt")
    assert calls[1].kwargs["allow_redirects"] is False
    assert calls[1].kwargs["stream"] is True
    assert clock["sleeps"] == [10.0]


def test_robots_disallow_stops_without_fetching_page(clock):
    policy = client([robots("User-agent: *\nDisallow: /\n")])
    with pytest.raises(http_policy.ScraperPolicyError, match="robots_disallowed"):
        policy.get_html(scraper.BASE_URL + "/nha-dat-ban")
    assert policy.session.get.call_count == 1


def test_missing_robots_does_not_prevent_public_page(clock):
    policy = client([Response(status=404), Response()])
    assert policy.get_html(scraper.BASE_URL + "/nha-dat-ban")


@pytest.mark.parametrize("response,error", [
    (Response(status=403), "access_denied"),
    (Response(status=401), "access_denied"),
    (Response(status=302), "not_followed"),
    (Response(body="<title>Just a moment...</title>"), "access_challenge"),
    (Response(**{"cf-mitigated": "challenge"}), "access_denied"),
])
def test_access_restrictions_are_not_retried(clock, response, error):
    policy = client([robots(), response])
    with pytest.raises(http_policy.ScraperPolicyError, match=error):
        policy.get_html(scraper.BASE_URL + "/nha-dat-ban")
    assert policy.session.get.call_count == 2


def test_transient_retry_uses_backoff_and_respects_retry_after(clock):
    policy = client([
        robots(), Response(status=503), Response(status=429, **{"Retry-After": "11"}), Response(),
    ])
    assert policy.get_html(scraper.BASE_URL + "/nha-dat-ban")
    assert clock["sleeps"] == [2.0, 2.0, 11.0]


def test_retry_after_over_budget_does_not_retry_early(clock):
    policy = client([robots(), Response(status=429, **{"Retry-After": "601"})])
    with pytest.raises(http_policy.ScraperFetchError, match="retry_after_exceeds_run_budget"):
        policy.get_html(scraper.BASE_URL + "/nha-dat-ban")
    assert policy.session.get.call_count == 2


def test_transport_failure_has_finite_attempts(clock):
    policy = client([robots(), requests.Timeout(), requests.Timeout()], max_retries=2)
    with pytest.raises(http_policy.ScraperFetchError, match="attempts_exhausted"):
        policy.get_html(scraper.BASE_URL + "/nha-dat-ban")
    assert policy.session.get.call_count == 3


def test_oversized_response_is_rejected(clock):
    policy = client([robots(), Response(body="x" * 101)], max_response_bytes=100)
    with pytest.raises(http_policy.ScraperFetchError, match="response_too_large"):
        policy.get_html(scraper.BASE_URL + "/nha-dat-ban")


def test_invalid_robots_rate_fails_closed(clock):
    policy = client([robots("User-agent: *\nRequest-rate: 0/10\n")])
    with pytest.raises(http_policy.ScraperPolicyError, match="invalid_robots_request_rate"):
        policy.get_html(scraper.BASE_URL + "/nha-dat-ban")
    assert policy.session.get.call_count == 1


@pytest.mark.parametrize("url", ["http://batdongsan.com.vn/a", "https://example.com/a", "https://batdongsan.com.vn/a#b"])
def test_foreign_or_non_https_url_not_requested(clock, url):
    policy = client([])
    with pytest.raises(http_policy.ScraperPolicyError, match="unexpected_source_origin"):
        policy.get_html(url)
    policy.session.get.assert_not_called()


def test_crawler_headers_identify_crawler_not_fake_browser():
    with scraper.make_session(30) as session:
        assert session.headers["User-Agent"] == "RealEstatePipelineCrawler/1.0"
        assert "vi-VN" in session.headers["Accept-Language"]
        assert "Referer" not in session.headers


@pytest.fixture
def crawl(monkeypatch, tmp_path):
    session = Mock()
    monkeypatch.setattr(scraper, "make_session", lambda **kwargs: session)
    monkeypatch.setattr(scraper, "fetch_html", lambda *args: "listing")
    monkeypatch.setattr(scraper, "extract_listing_links", lambda html: ["https://example.test/1", "https://example.test/2"])
    monkeypatch.setattr(scraper, "parse_listing_detail", lambda session, url, config: {"url": url, "title": "Fixture"})
    config = scraper.ScrapeConfig(max_items=2, state_file=tmp_path / "state.json")
    return config, session


class Producer:
    def __init__(self, mode="success"):
        self.mode = mode
        self.sent = []

    def produce(self, topic, **kwargs):
        self.sent.append((topic, kwargs))

    def flush(self, timeout):
        if self.mode == "pending":
            return 1
        if self.mode != "no_callback":
            self.sent[-1][1]["callback"](RuntimeError("broker failure") if self.mode == "error" else None, None)
        return 0


def test_successful_ack_precedes_checkpoint_and_resume_skips_urls(crawl):
    config, session = crawl
    producer = Producer()
    flush = producer.flush

    def assert_checkpoint_before_ack(timeout):
        state = scraper.load_state(config.state_file)
        assert state.get("emitted_count", 0) == len(producer.sent) - 1
        return flush(timeout)

    producer.flush = assert_checkpoint_before_ack
    assert kafka_producer.publish_records(producer, scraper.iter_listing_records(config), "raw", 30) == 2
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["emitted_count"] == 2
    assert len(state["seen_urls"]) == 2
    assert kafka_producer.publish_records(producer, scraper.iter_listing_records(config), "raw", 30) == 0
    assert len(producer.sent) == 2
    session.close.assert_called()


@pytest.mark.parametrize("mode", ["pending", "error", "no_callback"])
def test_failed_delivery_leaves_url_uncheckpointed_and_replayable(crawl, mode):
    config, session = crawl
    with pytest.raises(kafka_producer.KafkaDeliveryError):
        kafka_producer.publish_records(Producer(mode), scraper.iter_listing_records(config), "raw", 30)
    assert not config.state_file.exists()
    session.close.assert_called_once()
    retry = Producer()
    assert kafka_producer.publish_records(retry, scraper.iter_listing_records(config), "raw", 30) == 2
    assert retry.sent[0][1]["key"] == "https://example.test/1"


def test_synchronous_produce_failure_also_keeps_checkpoint(crawl):
    config, session = crawl
    producer = Mock()
    producer.produce.side_effect = BufferError("queue full")
    with pytest.raises(BufferError):
        kafka_producer.publish_records(producer, scraper.iter_listing_records(config), "raw", 30)
    assert not config.state_file.exists()
    session.close.assert_called_once()


def test_second_delivery_failure_preserves_first_ack_and_replays_only_second(crawl):
    config, _ = crawl
    producer = Producer()
    flush = producer.flush

    def fail_second(timeout):
        producer.mode = "error" if len(producer.sent) == 2 else "success"
        return flush(timeout)

    producer.flush = fail_second
    with pytest.raises(kafka_producer.KafkaDeliveryError):
        kafka_producer.publish_records(producer, scraper.iter_listing_records(config), "raw", 30)
    state = scraper.load_state(config.state_file)
    assert state["emitted_count"] == 1
    assert state["seen_urls"] == ["https://example.test/1"]
    retry = Producer()
    assert kafka_producer.publish_records(retry, scraper.iter_listing_records(config), "raw", 30) == 1
    assert retry.sent[0][1]["key"] == "https://example.test/2"


def test_partial_resume_budget_counts_new_records_not_historical_total(crawl):
    config, _ = crawl
    config.max_items = 1
    for _ in range(2):
        producer = Producer()
        assert kafka_producer.publish_records(producer, scraper.iter_listing_records(config), "raw", 30) == 1
    assert scraper.load_state(config.state_file)["emitted_count"] == 2


@pytest.mark.parametrize("error,code", [
    (http_policy.ScraperPolicyError("robots_disallowed"), 20),
    (http_policy.ScraperFetchError("source_timeout"), 1),
])
def test_cli_policy_exit_is_distinct_from_transient_failure(monkeypatch, tmp_path, error, code):
    make_producer = Mock(return_value=Producer())
    monkeypatch.setattr(kafka_producer, "Producer", make_producer)

    def records(source, config, **kwargs):
        raise error
        yield  # make this a lazy iterator like the real crawler

    monkeypatch.setattr(kafka_producer, "iter_source_records", records)
    assert kafka_producer.main(["--crawl-enabled", "--source", "alonhadat", "--state-file", str(tmp_path / "state.json")]) == code
    settings = make_producer.call_args.args[0]
    assert settings["acks"] == "all"
    assert settings["enable.idempotence"] is True


def test_cli_http_and_kafka_limits_are_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPE_HTTP_TIMEOUT", "17")
    monkeypatch.setenv("SCRAPE_HTTP_ATTEMPTS", "2")
    monkeypatch.setenv("SCRAPE_KAFKA_DELIVERY_TIMEOUT", "19")
    monkeypatch.setenv("SCRAPE_DELAY_MIN", "3")
    monkeypatch.setenv("SCRAPE_DELAY_MAX", "6")
    observed = []
    monkeypatch.setattr(kafka_producer, "Producer", Mock(return_value=Producer()))
    monkeypatch.setattr(kafka_producer, "iter_source_records", lambda source, config, **kwargs: observed.append(config) or iter([]))
    assert kafka_producer.main(["--crawl-enabled", "--source", "alonhadat", "--state-file", str(tmp_path / "state.json")]) == 0
    assert observed[0].timeout_seconds == 17
    assert observed[0].max_retries == 2
    assert observed[0].delay_min_seconds == 3
    assert observed[0].delay_max_seconds == 6
