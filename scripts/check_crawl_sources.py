"""Small live source check. Reads robots/category/details; no Kafka or Mongo writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from scraper.http_policy import PoliteHTTPClient, ScraperFetchError, ScraperPolicyError, ScraperRateLimitError
from scraper.listing_feature_scraper import ScrapeConfig
from scraper.multi_source import selected_sources
from scraper.sources import get_adapter
from scraper.sources.base import SourceShapeError


def check_source(source: str, limit: int) -> dict:
    adapter = get_adapter(source)
    result = {"source": source, "status": "unavailable", "discovered": 0,
              "parsed": 0, "valid": 0, "quarantined": 0, "errors": []}
    if adapter.disabled_reason:
        result.update(status="disabled", reason=adapter.disabled_reason)
        return result
    config = ScrapeConfig(max_items=limit, timeout_seconds=15, max_retries=2,
                          request_delay_seconds=3, detail_delay_seconds=3,
                          delay_min_seconds=3, delay_max_seconds=5)
    origin = "/".join(adapter.category_url.split("/")[:3])
    started = time.monotonic()
    with requests.Session() as session:
        session.headers.update({"User-Agent": config.user_agent,
                                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
                                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"})
        client = PoliteHTTPClient(session, config, origin)
        try:
            urls = adapter.discover(client.get_html(adapter.category_url))
            result["discovered"] = len(urls)
            for url in urls[:limit]:
                try:
                    record = adapter.parse(client.get_html(url), url)
                    result["parsed"] += 1
                    result["quarantined" if record["validation_errors"] else "valid"] += 1
                except ScraperRateLimitError:
                    # Retry-After and exhausted 429 apply to the entire source.
                    raise
                except (ScraperFetchError, SourceShapeError) as exc:
                    result["errors"].append({"type": type(exc).__name__, "reason": str(exc)})
        except (ScraperPolicyError, ScraperFetchError, SourceShapeError) as exc:
            result["errors"].append({"type": type(exc).__name__, "reason": str(exc)})
    result["elapsed_seconds"] = time.monotonic() - started
    if result["valid"]:
        result["status"] = "degraded" if result["errors"] else "sample_passed"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default="alonhadat,homedy")
    parser.add_argument("--limit", type=int, default=2, help="Detail requests per source, 1..3")
    parser.add_argument("--output", type=Path, default=Path("runtime/crawl_review/latest.json"))
    args = parser.parse_args()
    if not 1 <= args.limit <= 3:
        parser.error("--limit must be 1..3")
    sources = selected_sources(args.sources)
    result = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "scope": "bounded live sample; no Kafka/Mongo writes; does not establish long-term availability",
              "sources": [check_source(source, args.limit) for source in sources]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return int(any(row["status"] != "sample_passed" for row in result["sources"]))


if __name__ == "__main__":
    raise SystemExit(main())
