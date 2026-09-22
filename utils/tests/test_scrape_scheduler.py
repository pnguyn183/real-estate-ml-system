"""Failures remain visible without turning the scheduler into a rapid retry loop."""
from unittest.mock import Mock
import subprocess

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


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("crawler", 30), Mock(returncode=20), OSError("process unavailable")])
def test_source_failure_does_not_consume_next_sources_budget(monkeypatch, tmp_path, failure):
    monkeypatch.setenv("CRAWL_METRICS_DIR", str(tmp_path / "metrics"))
    monkeypatch.setattr(auto_scrape, "ENABLED_SOURCES", "alonhadat,homedy")
    run = Mock(side_effect=[failure, Mock(returncode=0)])
    monkeypatch.setattr(auto_scrape.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="alonhadat"):
        auto_scrape.run_scraper(limit=10, max_pages=1, start_page=1, timeout=30,
                               fresh_start=False, state_file=tmp_path / "state.json", run_label="periodic")
    assert run.call_count == 2
    for call, source in zip(run.call_args_list, ("alonhadat", "homedy")):
        args = call.args[0]
        assert args[args.index("--source") + 1] == source
        assert "--fresh-start" not in args
        assert call.kwargs == {"timeout": 30, "cwd": auto_scrape.ROOT}
    if isinstance(failure, subprocess.TimeoutExpired):
        from scraper.source_metrics import RunMetrics
        assert RunMetrics("alonhadat").values["scheduler_timeouts"] == 1


def test_successful_sources_share_checkpoint_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_scrape, "ENABLED_SOURCES", "alonhadat,homedy")
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(auto_scrape.subprocess, "run", run)
    auto_scrape.run_scraper(limit=10, max_pages=1, start_page=1, timeout=30,
                           fresh_start=False, state_file=tmp_path / "state.json", run_label="periodic")
    assert run.call_count == 2
    for call in run.call_args_list:
        args = call.args[0]
        assert args[args.index("--state-file") + 1] == str(tmp_path / "state.json")
