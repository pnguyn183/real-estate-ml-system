"""One bounded HTTP crawler and one Kafka contract for the three adapters."""
import logging
import math
import os
from pathlib import Path
import time

import requests

from processing.source_contract import make_record
from scraper.http_policy import PoliteHTTPClient, ScraperFetchError, ScraperPolicyError, ScraperRateLimitError
from scraper.listing_feature_scraper import load_state, save_state
from scraper.source_metrics import RunMetrics
from scraper.sources import get_adapter
from scraper.sources.base import SourceShapeError

logger = logging.getLogger(__name__)


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
            self.metrics.save()


def selected_sources(value):
    names = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not names:
        raise ValueError("At least one enabled source is required")
    for name in names:
        get_adapter(name)
    return names


def iter_source_records(source, config, *, fresh_start=False, revisit_seconds=86400, max_consecutive_failures=3):
    """Refresh acknowledged URLs after a bounded interval, prioritizing new URLs.

    Advancing this generator acknowledges its previous record. Kafka callers
    must wait for delivery before advancing; closing leaves that URL replayable.
    """
    adapter = get_adapter(source)
    metrics = RunMetrics(source)
    metrics.inc("crawl_attempts")
    metrics.values["last_attempt"] = time.time()
    metrics.save()
    session = None
    emitted = valid_emitted = detail_failures = invalid_emitted = 0
    consecutive_failures = 0
    try:
        if not math.isfinite(revisit_seconds) or revisit_seconds <= 0:
            raise ValueError("revisit_seconds must be positive and finite")
        if not isinstance(max_consecutive_failures, int) or not 1 <= max_consecutive_failures <= 100:
            raise ValueError("max_consecutive_failures must be 1..100")
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
        try:
            state = {} if fresh_start else load_state(path)
            if (not isinstance(state, dict) or not isinstance(state.get("seen_urls", []), list)
                    or not all(isinstance(value, str) for value in state.get("seen_urls", []))
                    or not isinstance(state.get("acknowledged_at", {}), dict)
                    or not isinstance(state.get("failed_urls", {}), dict)
                    or any(not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp < 0
                           for stamp in [*state.get("acknowledged_at", {}).values(), *state.get("failed_urls", {}).values()])):
                raise ValueError("invalid checkpoint shape")
        except (ValueError, UnicodeError):
            # Replay is safe because downstream storage upserts by source URL.
            # Preserve the damaged checkpoint for diagnosis before rebuilding it.
            if path and Path(path).exists():
                Path(path).replace(Path(str(path) + f".corrupt-{time.time_ns()}"))
            logger.warning("source=%s damaged_checkpoint_preserved; replaying discovered URLs", source)
            metrics.inc("checkpoint_resets")
            state = {}
        seen_order = list(dict.fromkeys(state.get("seen_urls", [])))
        now = time.time()
        acknowledged = state.get("acknowledged_at", {})
        # Migrate the previous URL-only state without permanently excluding it.
        legacy_timestamp = Path(path).stat().st_mtime if path and Path(path).exists() else now
        acknowledged = {url: acknowledged.get(url, legacy_timestamp) for url in seen_order}
        failed_urls = state.get("failed_urls", {})
        attempted = set()

        def checkpoint():
            # Failure timestamps only order retries. They never mark a URL
            # delivered, and thus cannot cause a failed record to be skipped.
            save_state(path, {"seen_urls": seen_order, "acknowledged_at": acknowledged,
                              "failed_urls": failed_urls, "source": source, "schema_version": 2})

        session = MeasuredSession(metrics)
        session.headers.update({"User-Agent": config.user_agent, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8"})
        origin = adapter.category_url.split("/", 3)[:3]
        client = PoliteHTTPClient(session, config, "/".join(origin))
        url = category
        for _ in range(config.max_pages):
            html = client.get_html(url)
            links = adapter.discover(html)
            metrics.inc("listings_discovered", len(links))
            metrics.values["last_checked"] = time.time()
            metrics.save()
            # Among eligible records, unseen URLs precede the oldest refreshes.
            # A small --limit therefore advances beyond the same first N cards.
            for detail in sorted(links, key=lambda value: (value in failed_urls,
                                                          failed_urls.get(value, acknowledged.get(value, -1)))):
                if (detail in attempted or detail in acknowledged
                        and now - acknowledged[detail] < revisit_seconds):
                    metrics.inc("duplicate_records")
                    continue
                attempted.add(detail)
                try:
                    detail_html = client.get_html(detail)
                except ScraperRateLimitError:
                    # A server-directed cooldown applies to the whole source.
                    raise
                except ScraperFetchError as error:
                    detail_failures += 1
                    consecutive_failures += 1
                    failed_urls[detail] = time.time()
                    failed_urls = dict(sorted(failed_urls.items(), key=lambda item: item[1])[-5000:])
                    checkpoint()
                    metrics.inc("detail_fetch_errors")
                    metrics.save()
                    logger.warning("source=%s detail_fetch_failed=%s; URL remains replayable", source, error)
                    if consecutive_failures >= max_consecutive_failures:
                        raise ScraperFetchError("consecutive_detail_failure_budget_exhausted") from error
                    continue
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
                if detail in seen_order:
                    seen_order.remove(detail)
                seen_order.append(detail)
                acknowledged[detail] = time.time()
                failed_urls.pop(detail, None)
                emitted += 1
                invalid = bool(record["validation_errors"])
                consecutive_failures = consecutive_failures + 1 if invalid else 0
                invalid_emitted += int(invalid)
                valid_emitted += int(not invalid)
                metrics.inc("listings_published")
                metrics.inc("valid_listings_published" if not invalid else "invalid_listings_published")
                metrics.values["last_publish"] = time.time()
                if not invalid:
                    metrics.values["last_success"] = time.time()
                seen_order = seen_order[-5000:]
                acknowledged = {url: acknowledged[url] for url in seen_order}
                checkpoint()
                metrics.save()
                if consecutive_failures >= max_consecutive_failures:
                    raise ScraperFetchError("consecutive_detail_failure_budget_exhausted")
                if config.max_items is not None and emitted >= config.max_items:
                    break
            if config.max_items is not None and emitted >= config.max_items:
                break
            url = adapter.next_page(html, url)
            if not url:
                break
        if detail_failures or invalid_emitted:
            raise ScraperFetchError(f"incomplete_source_run: detail_errors={detail_failures}, invalid_records={invalid_emitted}")
        metrics.inc("crawl_completed")
        metrics.values["last_completed"] = time.time()
        if valid_emitted:
            metrics.success()
        else:
            metrics.inc("crawl_no_change")
    except BaseException:
        # Generator close after an unacknowledged Kafka message is a failed run.
        metrics.inc("crawl_failure")
        metrics.values["last_failure"] = time.time()
        if valid_emitted:
            metrics.inc("crawl_partial")
        raise
    finally:
        if session:
            session.close()
        metrics.save()
