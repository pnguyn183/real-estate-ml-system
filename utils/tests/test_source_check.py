from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import check_crawl_sources as check
from scraper.http_policy import ScraperMissingError, ScraperPolicyError, ScraperRateLimitError


@pytest.fixture
def probe(monkeypatch):
    adapter = SimpleNamespace(disabled_reason=None, category_url="https://example.invalid/list",
                              discover=Mock(return_value=["first", "second"]),
                              parse=Mock(return_value={"validation_errors": []}))
    client = Mock()
    monkeypatch.setattr(check, "get_adapter", lambda _: adapter)
    monkeypatch.setattr(check, "PoliteHTTPClient", lambda *args: client)
    return client, adapter


@pytest.mark.parametrize("error", [ScraperRateLimitError("retry_after"), ScraperPolicyError("denied")])
def test_live_check_stops_source_on_server_cooldown_or_policy(probe, error):
    client, adapter = probe
    client.get_html.side_effect = ["category", error, "must not fetch"]
    result = check.check_source("fixture", 2)
    assert result["status"] == "unavailable"
    assert client.get_html.call_count == 2
    adapter.parse.assert_not_called()


def test_missing_detail_can_continue_but_never_reports_clean_sample(probe):
    client, _ = probe
    client.get_html.side_effect = ["category", ScraperMissingError("404"), "detail"]
    result = check.check_source("fixture", 2)
    assert result["status"] == "degraded" and result["valid"] == 1
    assert client.get_html.call_count == 3


def test_disabled_source_makes_no_requests(probe):
    client, adapter = probe
    adapter.disabled_reason = "not enabled"
    assert check.check_source("fixture", 2)["status"] == "disabled"
    client.get_html.assert_not_called()
