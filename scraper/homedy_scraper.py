"""Bounded, sale-only Homedy source adapter.

The Kafka producer uses ``iter_homedy_records`` for the production raw-data
path. ``run_trial`` remains available for a local compatibility report. Both
paths are robots-aware, bounded and never bypass access controls.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

from parsel import Selector
import requests


BASE_URL = "https://homedy.com"
LIST_URL = BASE_URL + "/ban-nha-dat"
USER_AGENT = "RealEstatePipelineCrawler/1.0"
MAX_TRIAL_RECORDS = 10
# Bound decompressed HTML, not just the compressed transfer size. Do not remove
# this guard if the source returns oversized responses during sequential access.
MAX_RESPONSE_BYTES = 8_000_000
OUTPUT_ROOT = Path("runtime/source_trials")
DETAIL_PATH = re.compile(r"^/ban-[a-z0-9-]+/[a-z0-9-]+-es(\d+)$")
PROPERTY_TYPES = {
    "nha mat pho": "house", "nha rieng": "house", "nha pho": "house",
    "can ho": "apartment", "can ho chung cu": "apartment", "chung cu": "apartment",
    "dat": "land", "dat tho cu": "land", "dat nen du an": "land",
    "biet thu": "villa_townhouse", "biet thu lien ke": "villa_townhouse",
    "nha biet thu lien ke": "villa_townhouse", "shophouse": "shophouse",
    "van phong": "office", "kho nha xuong": "warehouse",
}
ATTRIBUTE_FIELDS = {
    "so phong ngu": "bedroom_text", "phong ngu": "bedroom_text",
    "so phong tam": "bathroom_text", "phong tam": "bathroom_text",
    "so phong ve sinh": "bathroom_text", "so tang": "floor_text",
    "mat tien": "front_width_text", "duong vao": "road_width_text",
    "tinh trang phap ly": "legal_text", "huong nha": "direction_text",
    "noi that": "furniture_text",
}


class SourceError(RuntimeError):
    """A source/access/shape failure, not a successful empty crawl."""


def _text(nodes) -> str | None:
    value = " ".join(nodes.xpath(".//text()").getall())
    return re.sub(r"\s+", " ", value).strip() or None


def _label(value: str | None) -> str:
    value = (value or "").replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFD", value).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", value).strip().lower()


def _slug(value: str) -> str:
    # Normalize explicit names; never infer administrative levels from a URL.
    value = _label(value)
    if value in {"tp ho chi minh", "tp. ho chi minh", "thanh pho ho chi minh"}:
        value = "ho chi minh"
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def canonical_listing_url(href: str) -> str | None:
    try:
        parts = urlsplit(urljoin(BASE_URL, href))
        if (parts.scheme != "https" or parts.netloc != "homedy.com"
                or parts.username or parts.password or not DETAIL_PATH.fullmatch(parts.path)):
            return None
        return urlunsplit(("https", "homedy.com", parts.path, "", ""))
    except ValueError:
        return None


def extract_listing_links(html: str) -> list[str]:
    return list(dict.fromkeys(
        url for href in Selector(text=html).css("a::attr(href)").getall()
        if (url := canonical_listing_url(href))
    ))


def _pairs(nodes, labels: str, values: str) -> dict[str, str | None]:
    result = {}
    for node in nodes:
        key = _label(_text(node.css(labels)))
        if key:
            result[key] = _text(node.css(values))
    return result


def parse_listing_html(html: str, url: str) -> dict:
    canonical = canonical_listing_url(url)
    if not canonical:
        raise SourceError("unsupported_listing_url")
    listing_id = DETAIL_PATH.fullmatch(urlsplit(canonical).path).group(1)
    page = Selector(text=html)
    top = page.css(".product-detail-top-left")
    title = _text(top.css("h1"))
    if len(top) != 1 or not title:
        raise SourceError("missing_listing_detail_structure")
    declared = page.css('link[rel="canonical"]::attr(href)').get()
    if declared:
        declared = canonical_listing_url(declared)
        if not declared or DETAIL_PATH.fullmatch(urlsplit(declared).path).group(1) != listing_id:
            raise SourceError("canonical_listing_id_mismatch")
        canonical = declared
    info = _pairs(page.css(".product-info > div"), "p.lb-code", "p.code")
    if _label(info.get("loai tin")) != "ban":
        raise SourceError("not_explicit_sale_listing")
    if info.get("id tin") and info["id tin"] != listing_id:
        raise SourceError("listing_id_mismatch")

    short = _pairs(top.css(".product-short-info .short-item"), ":scope > span", "strong")
    attrs = _pairs(page.css(".product-attributes--item"), ":scope > span:first-child", ":scope > span:nth-child(2)")
    area = short.get("dien tich")
    # Dimensions and usable/floor area are not interchangeable with the primary
    # advertised area. The old parser takes the first number, so fail closed.
    if area:
        area = re.sub(r"m\s*(?:2|²)", "m2", area, flags=re.IGNORECASE)
        if not re.fullmatch(r"[\d.,]+\s*m2", area):
            area = None
    record = {
        "url": canonical, "listing_id": listing_id, "source": "homedy",
        "source_type": "website", "is_synthetic": False, "verified": 0,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "title": title, "listing_type": "Bán",
        "property_type": PROPERTY_TYPES.get(_label(attrs.get("loai hinh"))),
        # Only the main advertised price: not a derived price in <em>, a
        # recommendation card or Organization/LocalBusiness JSON-LD metadata.
        "price_text": short.get("gia"), "area_text": area,
        "posted_date_text": info.get("ngay dang"),
        "address": _text(top.css(".address")),
        "description": _text(page.css(".description-content .description")),
        "province_slug": None, "district_slug": None, "ward_slug": None,
    }
    record.update({field: None for field in ATTRIBUTE_FIELDS.values()})
    for label, field in ATTRIBUTE_FIELDS.items():
        if attrs.get(label):
            record[field] = attrs[label]

    # Use only the source's explicit category -> province -> district breadcrumb
    # hierarchy. Unknown layouts stay null instead of guessing from address text.
    breadcrumbs = top.css(".breadcrumb li a")
    for index, node in enumerate(breadcrumbs):
        if _label(_text(node)) == _label(attrs.get("loai hinh")) and attrs.get("loai hinh"):
            category = urlsplit(urljoin(BASE_URL, node.attrib.get("href", ""))).path
            for field, location in zip(("province_slug", "district_slug", "ward_slug"), breadcrumbs[index + 1:]):
                link = urlsplit(urljoin(BASE_URL, location.attrib.get("href", "")))
                name = _text(location)
                if link.netloc != "homedy.com" or not link.path.startswith(category + "-") or not name:
                    break
                record[field] = _slug(name)
            break
    return record


class TrialClient:
    """Fixed-origin, robots-aware, bounded HTTP; no bypass or immediate retries."""

    def __init__(self, delay: float = 3.0):
        if not math.isfinite(delay) or not 2 <= delay <= 30:
            raise ValueError("delay must be between 2 and 30 seconds")
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.rules = None
        self.last_request = None
        self.request_count = 0

    def get(self, url: str, *, robots: bool = False) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "homedy.com" or parts.query or parts.fragment:
            raise SourceError("unexpected_source_url")
        if robots and url != BASE_URL + "/robots.txt":
            raise SourceError("unexpected_robots_url")
        if not robots and (self.rules is None or not self.rules.can_fetch(USER_AGENT, url)):
            raise SourceError("robots_disallowed")
        if self.last_request is not None:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        self.request_count += 1
        try:
            with self.session.get(url, timeout=(5, 15), allow_redirects=False, stream=True) as response:
                if response.headers.get("cf-mitigated", "").lower() == "challenge":
                    raise SourceError("source_access_challenge")
                if response.status_code != 200:
                    raise SourceError(f"source_http_{response.status_code}")
                expected = "text/plain" if robots else "text/html"
                if expected not in response.headers.get("Content-Type", "").lower():
                    raise SourceError("unexpected_content_type")
                body = bytearray()
                for chunk in response.iter_content(16384):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise SourceError("source_response_too_large")
                    if time.monotonic() - self.last_request > 30:
                        raise SourceError("source_response_deadline")
                return body.decode("utf-8-sig")
        except (requests.RequestException, UnicodeError) as error:
            raise SourceError(f"source_transport_{type(error).__name__}") from None

    def load_robots(self):
        text = self.get(BASE_URL + "/robots.txt", robots=True)
        if "<html" in text.lower():
            raise SourceError("unexpected_robots_html")
        self.rules = RobotFileParser()
        self.rules.parse(text.splitlines())
        # A generic crawl permission is not permission to reuse data for ML.
        # Persist policy evidence in the local trial report, not as a licence.
        if re.search(r"ai-train\s*=\s*no", text, re.IGNORECASE):
            raise SourceError("source_disallows_training_use")
        crawl_delay = self.rules.crawl_delay(USER_AGENT)
        if crawl_delay:
            if crawl_delay > 30:
                raise SourceError("source_crawl_delay_exceeds_trial_budget")
            self.delay = max(self.delay, crawl_delay)
        return text

    def close(self):
        self.session.close()


def iter_homedy_records(limit: int = 10, delay: float = 3.0):
    """Yield sale records from one bounded Homedy category page."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_TRIAL_RECORDS:
        raise ValueError(f"limit must be between 1 and {MAX_TRIAL_RECORDS}")
    client = TrialClient(delay)
    try:
        client.load_robots()
        links = extract_listing_links(client.get(LIST_URL))
        if not links:
            raise SourceError("no_sale_listing_links")
        emitted = 0
        for url in links[:limit]:
            try:
                record = parse_listing_html(client.get(url), url)
            except SourceError as error:
                if str(error) in {"source_response_too_large", "source_response_deadline"}:
                    continue
                raise
            yield record
            emitted += 1
        if emitted == 0:
            raise SourceError("no_usable_sale_records")
    finally:
        client.close()


def validate_trial_record(record: dict) -> dict:
    # Same deterministic parsing as the running Processor, no DB connection.
    from processing.kafka_to_mongo import normalize_listing, validate_normalized_record
    normalized = normalize_listing(record)
    _, errors = validate_normalized_record(normalized)
    missing = [name for name in ("price_vnd", "area_m2", "property_type", "province_slug") if not normalized.get(name)]
    return {
        "listing_id": record["listing_id"], "url": record["url"],
        "price_vnd": normalized["price_vnd"], "area_m2": normalized["area_m2"],
        "property_type": normalized["property_type"],
        "province_slug": normalized["province_slug"], "district_slug": normalized["district_slug"],
        "validation_errors": errors, "missing_required": missing,
        "usable_for_source_trial": not errors and not missing,
        "training_approved": False,
    }


def run_trial(limit: int = 5, delay: float = 3.0) -> tuple[dict, Path]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_TRIAL_RECORDS:
        raise ValueError(f"limit must be between 1 and {MAX_TRIAL_RECORDS}")
    client = TrialClient(delay)
    run_id = "homedy-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    output = OUTPUT_ROOT / run_id
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "run_id": run_id, "source": "homedy", "status": "failed", "limit": limit,
        "records": 0, "usable_records": 0, "rejected": [],
        "kafka_published": 0, "mongo_writes": 0, "training_approved": False,
        "scope": "local source compatibility trial; not bulk access or a licence",
    }
    active_url = BASE_URL + "/robots.txt"
    try:
        robots = client.load_robots()
        (output / "robots.txt").write_text(robots, encoding="utf-8")
        active_url = LIST_URL
        links = extract_listing_links(client.get(active_url))
        report["discovered_urls"] = len(links)
        if not links:
            raise SourceError("no_sale_listing_links")
        with (output / "records.jsonl").open("x", encoding="utf-8") as raw_file, (output / "validation.jsonl").open("x", encoding="utf-8") as checks:
            for url in links[:limit]:
                active_url = url
                try:
                    html = client.get(active_url)
                except SourceError as error:
                    # A single oversized/slow page is an explicit rejection,
                    # not a reason to remove bounds or abandon every other URL.
                    # Access failures (403, 429, challenge, robots) still stop.
                    if str(error) not in {"source_response_too_large", "source_response_deadline"}:
                        raise
                    report["rejected"].append({"url": url, "reason": str(error)})
                    continue
                try:
                    record = parse_listing_html(html, url)
                    validation = validate_trial_record(record)
                except (SourceError, ValueError, TypeError) as error:
                    report["rejected"].append({"url": url, "reason": str(error) if isinstance(error, SourceError) else type(error).__name__})
                    continue
                raw_file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                checks.write(json.dumps(validation, ensure_ascii=False, allow_nan=False) + "\n")
                report["records"] += 1
                report["usable_records"] += int(validation["usable_for_source_trial"])
        report["status"] = "passed" if report["records"] == limit and report["usable_records"] == limit else "needs_review"
    except SourceError as error:
        report["error"] = str(error)
        report["failed_url"] = active_url
    finally:
        report["http_requests"] = client.request_count
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        client.close()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()
    try:
        report, output = run_trial(args.limit, args.delay)
    except ValueError as error:
        parser.error(str(error))
    # No source descriptions, seller/contact details or HTTP response bodies in logs.
    summary = {key: value for key, value in report.items() if key != "rejected"}
    summary["rejection_reasons"] = dict(Counter(item["reason"] for item in report["rejected"]))
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    print(f"Local trial output: {output.as_posix()}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
