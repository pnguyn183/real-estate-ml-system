"""Persist bounded-source counters across short crawler subprocesses.

One scheduler owns crawling at a time. Files are local runtime state, not a
multi-writer metrics database; Airflow/legacy scheduler must not overlap.
"""
import json
import logging
import math
import os
from pathlib import Path
import time

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, SummaryMetricFamily
from processing.source_contract import DOMAINS

COUNTERS = ("crawl_attempts", "crawl_success", "crawl_failure", "listings_discovered",
            "crawl_completed", "crawl_no_change", "crawl_partial", "detail_fetch_errors",
            "listings_parsed", "listings_valid", "listings_invalid", "parsing_errors", "duplicate_records",
            "listings_published", "valid_listings_published", "invalid_listings_published",
            "scheduler_timeouts", "checkpoint_resets", "metrics_state_resets")


def read_values(path):
    values = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(values, dict) or any(
            not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
            for value in values.values())):
        raise ValueError("invalid metrics state")
    return values


def metrics_root():
    return Path(os.environ.get("CRAWL_METRICS_DIR", "runtime/crawl_metrics"))


class RunMetrics:
    def __init__(self, source):
        if source not in DOMAINS:
            raise ValueError("Unknown metrics source")
        self.source = source
        self.path = metrics_root() / (source + ".json")
        try:
            self.values = read_values(self.path)
        except FileNotFoundError:
            self.values = {}
        except (ValueError, UnicodeError):
            self.path.replace(Path(str(self.path) + f".corrupt-{time.time_ns()}"))
            logging.getLogger(__name__).warning("source=%s damaged_metrics_preserved; counters reset", source)
            self.values = {"metrics_state_resets": 1}

    def inc(self, name, count=1):
        self.values[name] = self.values.get(name, 0) + count

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.values, allow_nan=False), encoding="utf-8")
        temporary.replace(self.path)

    def success(self):
        self.inc("crawl_success")
        self.values["last_success"] = time.time()


class SourceCollector:
    def collect(self):
        from scraper.sources import get_adapter

        enabled = os.environ.get("CRAWL_ENABLED", "false").lower() in {"true", "1", "yes"}
        enabled_sources = {source.strip() for source in os.environ.get("ENABLED_SOURCES", "alonhadat,homedy").split(",")}
        configured = GaugeMetricFamily("source_crawl_enabled", "One if scheduled crawling is enabled for this supported source", labels=["source"])
        for source in DOMAINS:
            configured.add_metric([source], int(enabled and source in enabled_sources and not get_adapter(source).disabled_reason))
        yield configured
        rows = {}
        for source in DOMAINS:
            try:
                rows[source] = read_values(metrics_root() / (source + ".json"))
            except (FileNotFoundError, ValueError, OSError):
                rows[source] = None
        for name in COUNTERS:
            metric = CounterMetricFamily(name, "Crawler cumulative " + name, labels=["source"])
            for source, values in rows.items():
                if values is not None:
                    metric.add_metric([source], values.get(name, 0))
            yield metric
        available = GaugeMetricFamily("source_metrics_available", "One if source metrics state is readable", labels=["source"])
        last = GaugeMetricFamily("source_last_success_timestamp", "Last Kafka-acknowledged valid listing, including partial runs", labels=["source"])
        checked = GaugeMetricFamily("source_last_checked_timestamp", "Last successfully parsed category discovery, including no eligible URLs", labels=["source"])
        completed = GaugeMetricFamily("source_last_completed_timestamp", "Last error-free run, including no new eligible listings", labels=["source"])
        attempted = GaugeMetricFamily("source_last_attempt_timestamp", "Last source crawl attempt", labels=["source"])
        failed = GaugeMetricFamily("source_last_failure_timestamp", "Last failed or partially failed source run", labels=["source"])
        published = GaugeMetricFamily("source_last_publish_timestamp", "Last Kafka-acknowledged listing, valid or quarantined", labels=["source"])
        latency = SummaryMetricFamily("request_latency_seconds", "HTTP time to response headers, including robots and retries; excludes body download", labels=["source"])
        for source, values in rows.items():
            available.add_metric([source], int(values is not None))
            if values is not None:
                last.add_metric([source], values.get("last_success", 0))
                checked.add_metric([source], values.get("last_checked", 0))
                completed.add_metric([source], values.get("last_completed", 0))
                attempted.add_metric([source], values.get("last_attempt", 0))
                failed.add_metric([source], values.get("last_failure", 0))
                published.add_metric([source], values.get("last_publish", 0))
                latency.add_metric([source], values.get("request_count", 0), values.get("request_seconds", 0))
        yield available
        yield last
        yield checked
        yield completed
        yield attempted
        yield failed
        yield published
        yield latency


def start_metrics():
    registry = CollectorRegistry()
    registry.register(SourceCollector())
    return start_http_server(int(os.environ.get("CRAWL_METRICS_PORT", "8008")), registry=registry)
