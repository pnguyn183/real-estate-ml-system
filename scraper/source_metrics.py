"""Persist bounded-source counters across short crawler subprocesses.

One scheduler owns crawling at a time. Files are local runtime state, not a
multi-writer metrics database; Airflow/legacy scheduler must not overlap.
"""
import json
import os
from pathlib import Path
import time

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, SummaryMetricFamily
from processing.source_contract import DOMAINS

COUNTERS = ("crawl_attempts", "crawl_success", "crawl_failure", "listings_discovered",
            "listings_parsed", "listings_valid", "listings_invalid", "parsing_errors", "duplicate_records")


def metrics_root():
    return Path(os.environ.get("CRAWL_METRICS_DIR", "runtime/crawl_metrics"))


class RunMetrics:
    def __init__(self, source):
        if source not in DOMAINS:
            raise ValueError("Unknown metrics source")
        self.source = source
        self.path = metrics_root() / (source + ".json")
        try:
            self.values = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.values = {}

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
        rows = {}
        for source in DOMAINS:
            try:
                rows[source] = json.loads((metrics_root() / (source + ".json")).read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError, OSError):
                rows[source] = None
        for name in COUNTERS:
            metric = CounterMetricFamily(name, "Crawler cumulative " + name, labels=["source"])
            for source, values in rows.items():
                if values is not None:
                    metric.add_metric([source], values.get(name, 0))
            yield metric
        available = GaugeMetricFamily("source_metrics_available", "One if source metrics state is readable", labels=["source"])
        last = GaugeMetricFamily("source_last_success_timestamp", "Last acknowledged successful crawl run", labels=["source"])
        latency = SummaryMetricFamily("request_latency_seconds", "All source HTTP requests, including robots and retries", labels=["source"])
        for source, values in rows.items():
            available.add_metric([source], int(values is not None))
            if values is not None:
                last.add_metric([source], values.get("last_success", 0))
                latency.add_metric([source], values.get("request_count", 0), values.get("request_seconds", 0))
        yield available
        yield last
        yield latency


def start_metrics():
    registry = CollectorRegistry()
    registry.register(SourceCollector())
    return start_http_server(int(os.environ.get("CRAWL_METRICS_PORT", "8008")), registry=registry)
