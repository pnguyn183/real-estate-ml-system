"""Source failures stay bounded without abandoning healthy detail pages."""
from unittest.mock import Mock

import pytest
import requests

from scraper import homedy_scraper, http_policy
from scraper.listing_feature_scraper import BASE_URL
from utils.tests.test_scraper_runtime import Response, client, clock, robots


@pytest.mark.parametrize("status", [404, 410])
def test_expired_detail_has_distinct_non_retryable_error(clock, status):
    policy = client([robots(), Response(status=status)])
    with pytest.raises(http_policy.ScraperMissingError, match=f"source_http_{status}_missing"):
        policy.get_html(BASE_URL + "/expired")
    assert policy.session.get.call_count == 2


@pytest.mark.parametrize("error_type", [
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
])
def test_interrupted_body_retries_whole_page_with_bounded_backoff(clock, error_type):
    broken = Response()
    broken.iter_content = Mock(side_effect=error_type("private source response"))
    policy = client([robots(), broken, Response(body="<html>complete</html>")], max_retries=2)
    assert policy.get_html(BASE_URL + "/listing") == "<html>complete</html>"
    assert policy.session.get.call_count == 3
    assert clock["sleeps"] == [2.0, 2.0]


@pytest.mark.parametrize("response,attempts", [
    (Response(status=429), 1),
    (Response(status=503, **{"Retry-After": "600"}), 1),
    (Response(status=503, **{"Retry-After": "600"}), 2),
])
def test_exhaustion_or_long_cooldown_must_stop_source(clock, response, attempts):
    policy = client([robots(), response], max_retries=attempts)
    with pytest.raises(http_policy.ScraperRateLimitError):
        policy.get_html(BASE_URL + "/listing")
    assert policy.session.get.call_count == 2


def test_invalid_robot_policy_is_not_cached_for_next_listing(clock):
    invalid = "User-agent: *\nAllow: /\nCrawl-delay: 600\n"
    policy = client([robots(invalid), robots(invalid)])
    for _ in range(2):
        with pytest.raises(http_policy.ScraperPolicyError, match="robots_delay_exceeds_run_budget"):
            policy.get_html(BASE_URL + "/listing")
        assert policy.rules is None
    assert all(call.args[0].endswith("/robots.txt") for call in policy.session.get.call_args_list)


def test_trial_observes_request_rate_as_well_as_crawl_delay(monkeypatch):
    trial = homedy_scraper.TrialClient(delay=2)
    monkeypatch.setattr(trial, "get", lambda *args, **kwargs:
                        "User-agent: *\nAllow: /\nCrawl-delay: 3\nRequest-rate: 1/10\n")
    try:
        trial.load_robots()
        assert trial.delay == 10
    finally:
        trial.close()


@pytest.mark.parametrize("policy", ["Crawl-delay: 31", "Request-rate: 0/10", "Request-rate: 1/40"])
def test_trial_rejects_unusable_policy_without_caching_it(monkeypatch, policy):
    trial = homedy_scraper.TrialClient()
    monkeypatch.setattr(trial, "get", lambda *args, **kwargs: "User-agent: *\nAllow: /\n" + policy)
    try:
        with pytest.raises(homedy_scraper.SourceError):
            trial.load_robots()
        assert trial.rules is None
    finally:
        trial.close()
