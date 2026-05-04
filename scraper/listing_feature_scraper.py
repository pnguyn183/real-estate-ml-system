from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List
from urllib.parse import urlencode, urljoin, urlparse

from curl_cffi import requests
from parsel import Selector


BASE_URL = "https://batdongsan.com.vn"
DEFAULT_LIST_PATH = "/nha-dat-ban"
DEFAULT_QUERY = {"vrs": "1"}
STATE_DIR = Path("runtime") / "scrape_state"


@dataclass
class ScrapeConfig:
    max_pages: int = 50
    start_page: int = 1
    max_items: int | None = None
    request_delay_seconds: float = 0.25
    detail_delay_seconds: float = 0.1
    max_retries: int = 4
    timeout_seconds: int = 30
    use_verified_filter: bool = True
    state_file: Path | None = None
    extra_query: Dict[str, str] | None = None


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


def make_session(timeout_seconds: int) -> requests.Session:
    return requests.Session(
        impersonate="chrome124",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=timeout_seconds,
    )


def fetch_html(session: requests.Session, url: str, config: ScrapeConfig) -> str:
    last_error: Exception | None = None
    for attempt in range(1, config.max_retries + 1):
        try:
            response = session.get(url)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt >= config.max_retries:
                break
            time.sleep(min(2.0 * attempt, 8.0))
    raise RuntimeError(f"Failed to fetch {url}") from last_error


def build_list_url(page: int, config: ScrapeConfig) -> str:
    query = {}
    if config.use_verified_filter:
        query.update(DEFAULT_QUERY)
    query["page"] = str(page)
    if config.extra_query:
        query.update({key: str(value) for key, value in config.extra_query.items() if value is not None})
    return f"{BASE_URL}{DEFAULT_LIST_PATH}?{urlencode(query)}"


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
        "listing_id": short_info.get("Mã tin"),
        "title": clean_text(selector.css("h1::text").get()),
        "price_text": short_info.get("Khoảng giá") or specs_info.get("Khoảng giá"),
        "area_text": short_info.get("Diện tích") or specs_info.get("Diện tích"),
        "bedroom_text": short_info.get("Phòng ngủ") or specs_info.get("Số phòng ngủ"),
        "bathroom_text": specs_info.get("Số phòng tắm, vệ sinh"),
        "floor_text": specs_info.get("Số tầng"),
        "front_width_text": specs_info.get("Mặt tiền"),
        "road_width_text": specs_info.get("Đường vào"),
        "legal_text": specs_info.get("Pháp lý"),
        "direction_text": specs_info.get("Hướng nhà"),
        "property_type": infer_property_type(url),
        "listing_type": short_info.get("Loại tin"),
        "posted_date_text": short_info.get("Ngày đăng"),
        "furniture_text": specs_info.get("Nội thất"),
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
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_listing_records(config: ScrapeConfig) -> Iterator[Dict[str, Any]]:
    session = make_session(timeout_seconds=config.timeout_seconds)
    state = load_state(config.state_file)
    current_page = max(config.start_page, int(state.get("next_page", config.start_page)))
    emitted = int(state.get("emitted_count", 0))
    seen_urls = set(state.get("seen_urls", []))

    stop_page = config.start_page + config.max_pages - 1
    for page in range(current_page, stop_page + 1):
        list_html = fetch_html(session, build_list_url(page, config), config)
        urls = extract_listing_links(list_html)
        if not urls:
            break

        for url in urls:
            if url in seen_urls:
                continue
            record = parse_listing_detail(session, url, config)
            seen_urls.add(url)
            emitted += 1
            save_state(
                config.state_file,
                {
                    "next_page": page,
                    "emitted_count": emitted,
                    "seen_urls": list(seen_urls)[-5000:],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "config": asdict(config) | {"state_file": str(config.state_file) if config.state_file else None},
                },
            )
            yield record
            if config.max_items is not None and emitted >= config.max_items:
                return
            if config.detail_delay_seconds:
                time.sleep(config.detail_delay_seconds)

        save_state(
            config.state_file,
            {
                "next_page": page + 1,
                "emitted_count": emitted,
                "seen_urls": list(seen_urls)[-5000:],
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "config": asdict(config) | {"state_file": str(config.state_file) if config.state_file else None},
            },
        )
        if config.request_delay_seconds:
            time.sleep(config.request_delay_seconds)


def scrape_listing_records(config: ScrapeConfig) -> List[Dict[str, Any]]:
    return list(iter_listing_records(config))
