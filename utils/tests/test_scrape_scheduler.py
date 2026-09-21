"""Failures remain visible without turning the scheduler into a rapid retry loop."""
from unittest.mock import Mock

import pytest

from scripts import auto_scrape


@pytest.fixture(autouse=True)
def enabled_without_metric_socket(monkeypatch):
    monkeypatch.setenv("CRAWL_ENABLED", "true")
    monkeypatch.setattr("scraper.source_metrics.start_metrics", Mock())


@pytest.mark.parametrize("failure", [RuntimeError("exit 20"), TimeoutError("timeout")])
def test_initial_failure_waits_full_interval_and_does_not_crash(monkeypatch, failure):
    run = Mock(side_effect=[failure, KeyboardInterrupt()])
    sleep = Mock()
    monkeypatch.setattr(auto_scrape, "run_scraper", run)
    monkeypatch.setattr(auto_scrape.time, "sleep", sleep)
    monkeypatch.setattr(auto_scrape, "SCRAPE_INTERVAL", 1800)
    auto_scrape.main()
    sleep.assert_called_once_with(1800)
    assert run.call_args_list[0].kwargs["run_label"] == "initial"
    assert run.call_args_list[1].kwargs["run_label"] == "periodic"


def test_periodic_failure_also_waits_full_interval(monkeypatch):
    monkeypatch.setattr(auto_scrape, "run_scraper", Mock(side_effect=[None, RuntimeError("exit 1"), KeyboardInterrupt()]))
    sleep = Mock()
    monkeypatch.setattr(auto_scrape.time, "sleep", sleep)
    monkeypatch.setattr(auto_scrape, "SCRAPE_INTERVAL", 1800)
    auto_scrape.main()
    assert [call.args[0] for call in sleep.call_args_list] == [1800, 1800]
