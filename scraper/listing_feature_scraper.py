from __future__ import annotations

"""
Module: scraper/listing_feature_scraper.py
Purpose: Historical Batdongsan parser retained for compatibility and offline tests.
NOT registered in the active crawler. No CLI entrypoint. Shared ScrapeConfig and
atomic checkpoint helpers are still used by the three-source crawler.
Key behaviors: retry/backoff, persist resume state to `runtime/scrape_state`, basic dedup of recently seen URLs.
Inputs: list pages and detail pages of the source website.
Outputs: Python dict records describing a single listing per yield.
"""

import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List
from urllib.parse import urlencode, urljoin, urlparse

import requests
from parsel import Selector

if __package__:
    from .http_policy import PoliteHTTPClient, ScraperPolicyError
else:
    from http_policy import PoliteHTTPClient, ScraperPolicyError


BASE_URL = "https://batdongsan.com.vn"
DEFAULT_LIST_PATH = "/nha-dat-ban"
DEFAULT_QUERY = {"vrs": "1"}
STATE_DIR = Path("runtime") / "scrape_state"


@dataclass
class ScrapeConfig:
    max_pages: int = 1
    start_page: int = 1
    max_items: int | None = 10
    request_delay_seconds: float = 0.0
    detail_delay_seconds: float = 0.0
    max_retries: int = 4
    timeout_seconds: int = 30
    use_verified_filter: bool = True
    state_file: Path | None = None
    extra_query: Dict[str, str] | None = None
    delay_min_seconds: float = 2.0
    delay_max_seconds: float = 5.0
    user_agent: str = "RealEstatePipelineCrawler/1.0"
    retry_backoff_seconds: float = 2.0
    max_retry_wait_seconds: float = 120.0
    max_response_bytes: int = 8_000_000

    def __post_init__(self):
        if not 1 <= self.max_pages <= 1000 or self.start_page < 1:
            raise ValueError("max_pages must be 1..1000 and start_page must be positive")
        if self.max_items is not None and (isinstance(self.max_items, bool) or self.max_items < 1):
            raise ValueError("max_items must be positive or None")
        if not 1 <= self.max_retries <= 8 or not 1 <= self.timeout_seconds <= 120:
            raise ValueError("max_retries must be 1..8; timeout_seconds must be 1..120")
        for value in (self.delay_min_seconds, self.delay_max_seconds, self.request_delay_seconds,
                      self.detail_delay_seconds, self.retry_backoff_seconds, self.max_retry_wait_seconds):
            if not math.isfinite(value):
                raise ValueError("HTTP delays must be finite")
        if not 2 <= self.delay_min_seconds <= self.delay_max_seconds <= 60:
            raise ValueError("random delay must satisfy 2 <= minimum <= maximum <= 60")
        if not all(0 <= value <= 60 for value in (self.request_delay_seconds, self.detail_delay_seconds)):
            raise ValueError("legacy delay floors must be between 0 and 60 seconds")
        if not 0 < self.retry_backoff_seconds <= 60 or not 1 <= self.max_retry_wait_seconds <= 600:
            raise ValueError("retry backoff must be 0..60; maximum wait must be 1..600")
        if not 1 <= self.max_response_bytes <= 32_000_000:
            raise ValueError("max_response_bytes must be 1..32000000")
        if not self.user_agent.strip() or any(char in self.user_agent for char in "\r\n"):
            raise ValueError("user_agent must be a nonempty single-line crawler identity")


DETAIL_LABELS = {
    "listing_id": "Mã tin",
    "price": "Khoảng giá",
    "area": "Diện tích",
    "bedroom_short": "Phòng ngủ",
    "bedroom_specs": "Số phòng ngủ",
    "bathroom": "Số phòng tắm, vệ sinh",
    "floor": "Số tầng",
    "front_width": "Mặt tiền",
    "road_width": "Đường vào",
    "legal": "Pháp lý",
    "direction": "Hướng nhà",
    "listing_type": "Loại tin",
    "posted_date": "Ngày đăng",
    "furniture": "Nội thất",
}


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def collect_texts(selector: Selector, query: str) -> List[str]:
    values = [clean_text(value) for value in selector.css(query).getall()]
    return [value for value in values if value]


def pair_map(selector: Selector, title_query: str, value_query: str) -> Dict[str, str]:
    titles = collect_texts(selector, title_query)
    values = collect_texts(selector, value_query)
    return dict(zip(titles, values))


def infer_property_type(url: str) -> str | None:
    patterns = {
        "/ban-nha-rieng": "house",
        "/ban-can-ho-chung-cu": "apartment",
        "/ban-dat": "land",
        "/ban-nha-biet-thu-lien-ke": "villa_townhouse",
        "/ban-shophouse-nha-pho-thuong-mai": "shophouse",
        "/ban-kho-nha-xuong": "warehouse",
    }
    for prefix, label in patterns.items():
        if prefix in url:
            return label
    return None


def parse_location_parts(url: str) -> Dict[str, str | None]:
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[0] if path else None
    parts = slug.split("-") if slug else []

    province = None
    district = None
    ward = None
    if "tai" in parts:
        pivot = parts.index("tai")
        tail = parts[pivot + 1 :]
        if tail:
            province = "-".join(tail[-2:]) if len(tail) >= 2 else tail[-1]
            district = tail[-3] if len(tail) >= 3 else None
            ward = tail[-4] if len(tail) >= 4 else None

    return {
        "location_slug": slug,
        "province_slug": province,
        "district_slug": district,
        "ward_slug": ward,
    }


def make_session(timeout_seconds: int, user_agent: str = "RealEstatePipelineCrawler/1.0") -> requests.Session:
    # Timeout is enforced per request by the policy helper. Never impersonate a
    # browser or fabricate a search-engine referrer to work around HTTP 403.
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    })
    return session


def fetch_html(session: requests.Session, url: str, config: ScrapeConfig) -> str:
    client = getattr(session, "_scrape_policy", None)
    if client is None:
        client = PoliteHTTPClient(session, config, BASE_URL)
        session._scrape_policy = client
    return client.get_html(url)


def build_list_url(page: int, config: ScrapeConfig) -> str:
    query = {}
    if config.use_verified_filter:
        query.update(DEFAULT_QUERY)
    if config.extra_query:
        query.update({key: str(value) for key, value in config.extra_query.items() if value is not None})
    page_path = DEFAULT_LIST_PATH if page <= 1 else f"{DEFAULT_LIST_PATH}/p{page}"
    query_string = urlencode(query)
    return f"{BASE_URL}{page_path}" + (f"?{query_string}" if query_string else "")


def extract_listing_links(list_html: str) -> List[str]:
    selector = Selector(text=list_html)
    cards = selector.css("div.re__srp-list div.re__card-full, div.re__srp-list div.re__card-full-label-verified")

    links: List[str] = []
    seen = set()
    for card in cards:
        href = card.css("a.js__product-link-for-product-id::attr(href)").get()
        if not href:
            continue
        absolute_url = urljoin(BASE_URL, href)
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        links.append(absolute_url)
    return links


def parse_listing_detail(session: requests.Session, url: str, config: ScrapeConfig) -> Dict[str, Any]:
    html = fetch_html(session, url, config)
    selector = Selector(text=html)

    short_info = pair_map(
        selector,
        ".re__pr-short-info-item .title::text",
        ".re__pr-short-info-item .value::text",
    )
    specs_info = pair_map(
        selector,
        ".re__pr-specs-content-item-title::text",
        ".re__pr-specs-content-item-value::text",
    )
    description_parts = collect_texts(selector, ".re__section-body *::text")
    breadcrumb_text = collect_texts(selector, ".re__breadcrumb li *::text")
    location_parts = parse_location_parts(url)

    record: Dict[str, Any] = {
        "url": url,
        "listing_id": short_info.get(DETAIL_LABELS["listing_id"]),
        "title": clean_text(selector.css("h1::text").get()),
        "price_text": short_info.get(DETAIL_LABELS["price"]) or specs_info.get(DETAIL_LABELS["price"]),
        "area_text": short_info.get(DETAIL_LABELS["area"]) or specs_info.get(DETAIL_LABELS["area"]),
        "bedroom_text": short_info.get(DETAIL_LABELS["bedroom_short"])
        or specs_info.get(DETAIL_LABELS["bedroom_specs"]),
        "bathroom_text": specs_info.get(DETAIL_LABELS["bathroom"]),
        "floor_text": specs_info.get(DETAIL_LABELS["floor"]),
        "front_width_text": specs_info.get(DETAIL_LABELS["front_width"]),
        "road_width_text": specs_info.get(DETAIL_LABELS["road_width"]),
        "legal_text": specs_info.get(DETAIL_LABELS["legal"]),
        "direction_text": specs_info.get(DETAIL_LABELS["direction"]),
        "property_type": infer_property_type(url),
        "listing_type": short_info.get(DETAIL_LABELS["listing_type"]),
        "posted_date_text": short_info.get(DETAIL_LABELS["posted_date"]),
        "furniture_text": specs_info.get(DETAIL_LABELS["furniture"]),
        "project_hint": breadcrumb_text[-2] if len(breadcrumb_text) >= 2 else None,
        "verified": 1 if "vrs=1" in build_list_url(1, config) else 0,
        "source": "batdongsan",
        "description": " ".join(description_parts[:18]) if description_parts else None,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    record.update(location_parts)
    return record


def load_state(path: Path | None) -> Dict[str, Any]:
    if not path:
        return {}
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path | None, state: Dict[str, Any]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # A killed scheduler must not leave a half-written checkpoint.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def iter_listing_records(config: ScrapeConfig) -> Iterator[Dict[str, Any]]:
    state = load_state(config.state_file)
    current_page = max(config.start_page, int(state.get("next_page", config.start_page)))
    emitted = int(state.get("emitted_count", 0))
    seen_order = list(dict.fromkeys(state.get("seen_urls", [])))
    seen_urls = set(seen_order)
    run_emitted = 0
    session = make_session(timeout_seconds=config.timeout_seconds, user_agent=config.user_agent)

    def checkpoint(next_page):
        save_state(config.state_file, {
            "next_page": next_page, "emitted_count": emitted,
            "seen_urls": seen_order[-5000:],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "config": asdict(config) | {"state_file": str(config.state_file) if config.state_file else None},
        })

    try:
        # max_pages is a per-run budget, including when resuming an older run.
        for page in range(current_page, current_page + config.max_pages):
            list_html = fetch_html(session, build_list_url(page, config), config)
            urls = extract_listing_links(list_html)
            if not urls:
                break
            for url in urls:
                if url in seen_urls:
                    continue
                record = parse_listing_detail(session, url, config)
                # The consumer must acknowledge its destination before asking
                # for the next record. Closing/raising leaves this URL replayable.
                yield record
                seen_urls.add(url)
                seen_order.append(url)
                emitted += 1
                run_emitted += 1
                checkpoint(page)
                if config.max_items is not None and run_emitted >= config.max_items:
                    return
            checkpoint(page + 1)
    finally:
        session.close()


def scrape_listing_records(config: ScrapeConfig) -> List[Dict[str, Any]]:
    return list(iter_listing_records(config))
