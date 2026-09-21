"""One bounded HTTP crawler and one Kafka contract for the three adapters."""
import os
from pathlib import Path
import time

import requests

from processing.source_contract import make_record
from scraper.http_policy import PoliteHTTPClient, ScraperPolicyError
from scraper.listing_feature_scraper import load_state, save_state
from scraper.source_metrics import RunMetrics
from scraper.sources import get_adapter
from scraper.sources.base import SourceShapeError


class MeasuredSession(requests.Session):
    def __init__(self, metrics):
        super().__init__()
        self.metrics = metrics

    def get(self, *args, **kwargs):
        start = time.monotonic()
        try:
            return super().get(*args, **kwargs)
        finally:
            # Header/request latency; body consumption remains bounded by policy.
            self.metrics.inc("request_count")
            self.metrics.inc("request_seconds", time.monotonic() - start)


def selected_sources(value):
    names = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not names:
        raise ValueError("At least one enabled source is required")
    for name in names:
        get_adapter(name)
    return names


def iter_source_records(source, config, *, fresh_start=False):
    adapter = get_adapter(source)
    metrics = RunMetrics(source)
    metrics.inc("crawl_attempts")
    session = None
    try:
        if adapter.disabled_reason:
            raise ScraperPolicyError(source + ": " + adapter.disabled_reason)
        category = os.environ.get(source.upper() + "_CATEGORY_URL", adapter.category_url)
        # Current adapters support these tested categories, not arbitrary URLs.
        if category != adapter.category_url:
            raise ScraperPolicyError("untested_category_url_requires_adapter_review")
        if config.start_page != 1:
            raise ValueError("Start at page 1; pagination follows observed links only")
        path = config.state_file
        if path:
            path = Path(path).with_name(Path(path).stem + "-" + source + ".json")
        state = {} if fresh_start else load_state(path)
        seen_order = list(dict.fromkeys(state.get("seen_urls", [])))
        seen = set(seen_order)
        session = MeasuredSession(metrics)
        session.headers.update({"User-Agent": config.user_agent, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8"})
        origin = adapter.category_url.split("/", 3)[:3]
        client = PoliteHTTPClient(session, config, "/".join(origin))
        url = category
        emitted = 0
        for _ in range(config.max_pages):
            html = client.get_html(url)
            links = adapter.discover(html)
            metrics.inc("listings_discovered", len(links))
            for detail in links:
                if detail in seen:
                    metrics.inc("duplicate_records")
                    continue
                detail_html = client.get_html(detail)
                try:
                    record = adapter.parse(detail_html, detail)
                    metrics.inc("listings_parsed")
                except SourceShapeError as error:
                    # Persist a quarantined record through the existing Processor
                    # invalid collection; never turn selector failure into success.
                    metrics.inc("parsing_errors")
                    record = make_record(source, detail, {"source_errors": [str(error)]})
                metrics.inc("listings_invalid" if record["validation_errors"] else "listings_valid")
                yield record
                # Called only after publish_records receives the Kafka ACK.
                seen.add(detail)
                seen_order.append(detail)
                emitted += 1
                save_state(path, {"seen_urls": seen_order[-5000:], "source": source})
                metrics.save()
                if config.max_items is not None and emitted >= config.max_items:
                    metrics.success()
                    return
            url = adapter.next_page(html, url)
            if not url:
                break
        if emitted:
            metrics.success()
    except BaseException:
        # Generator close after an unacknowledged Kafka message is a failed run.
        metrics.inc("crawl_failure")
        raise
    finally:
        if session:
            session.close()
        metrics.save()
