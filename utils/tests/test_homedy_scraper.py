"""Offline Homedy adapter checks using fabricated markup, never live listings.

These examples intentionally contain no seller names, telephone numbers or copied
listing descriptions. They test the source boundary without Kafka or MongoDB.
"""
from __future__ import annotations

from html import escape
import json
import re
from unittest.mock import Mock
from urllib.robotparser import RobotFileParser

import pytest

import scraper.homedy_scraper as module
from scraper.homedy_scraper import (
    SourceError,
    canonical_listing_url,
    extract_listing_links,
    parse_listing_html,
)


LISTING_PATH = "/ban-nha-mat-pho-quan-1-tp-ho-chi-minh/nha-minh-hoa-es123"
LISTING_URL = "https://homedy.com" + LISTING_PATH


def listing_html(
    *,
    canonical: str = LISTING_URL,
    listing_id: str = "123",
    listing_type: str = "Bán",
    price: str | None = "<span>10,8</span> Tỷ",
    area: str | None = "33 m<sup>2</sup>",
    property_type: str = "Nhà mặt phố",
    extra: str = "",
) -> str:
    """Keep main-listing fields distinct from distracting recommendation data."""
    price_item = (
        '<div class="short-item"><span>Giá</span><strong>'
        + price
        + '</strong><em>327,27 Triệu/m2</em></div>'
        if price is not None
        else ""
    )
    area_item = (
        '<div class="short-item"><span>Diện tích</span><strong>'
        + area
        + "</strong></div>"
        if area is not None
        else ""
    )
    return f"""<!doctype html><html><head>
      <link rel="canonical" href="{escape(canonical, quote=True)}">
    </head><body>
      <div class="product-detail-top-left">
        <h1>Nhà minh họa cho kiểm thử bộ đọc</h1>
        <ol class="breadcrumb">
          <li><a href="/">Homedy</a></li>
          <li><a href="/ban-nha-dat">Bán nhà đất</a></li>
          <li><a href="/ban-nha-mat-pho">Nhà mặt phố</a></li>
          <li><a href="/ban-nha-mat-pho-tp-ho-chi-minh">TP Hồ Chí Minh</a></li>
          <li><a href="/ban-nha-mat-pho-quan-1-tp-ho-chi-minh">Quận 1</a></li>
        </ol>
        <div class="product-short-info">{price_item}{area_item}</div>
        <div class="product-attributes">
          <div class="product-attributes--item"><span>Loại hình</span><span>{escape(property_type)}</span></div>
          <div class="product-attributes--item"><span>Tình trạng pháp lý</span><span>Sổ hồng riêng</span></div>
        </div>
        <div class="description-content"><div class="description">
          <p>Dữ liệu mô phỏng cấu trúc HTML, không phải một tin rao thật.</p>
        </div></div>
        <div class="product-info">
          <div><p class="lb-code">Ngày đăng</p><p class="code">10/09/2026</p></div>
          <div><p class="lb-code">Loại tin</p><p class="code">{escape(listing_type)}</p></div>
          <div><p class="lb-code">ID tin</p><p class="code">{escape(listing_id)}</p></div>
        </div>
      </div>
      {extra}
    </body></html>"""


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


@pytest.mark.parametrize("href", [LISTING_PATH, LISTING_URL, LISTING_URL + "?utm_source=test#details"])
def test_canonicalizes_same_site_sale_detail_url(href):
    assert canonical_listing_url(href) == LISTING_URL


@pytest.mark.parametrize(
    "href",
    [
        None,
        "",
        "/ban-nha-dat",
        "/ban-nha-mat-pho-quan-1-tp-ho-chi-minh",
        "/cho-thue-can-ho/nha-minh-hoa-es123",
        "https://example.org" + LISTING_PATH,
        "https://homedy.com.example.org" + LISTING_PATH,
        "https://user:password@homedy.com" + LISTING_PATH,
        "http://homedy.com" + LISTING_PATH,
        "javascript:alert(1)",
        "https://homedy.com/ban-nha-dat/nha-minh-hoa-esnotanumber",
    ],
)
def test_rejects_non_listing_or_untrusted_urls(href):
    assert canonical_listing_url(href) is None


def test_link_extraction_deduplicates_and_rejects_unrelated_links():
    second = "/ban-can-ho-quan-1-tp-ho-chi-minh/can-ho-minh-hoa-es456"
    html = f"""<a href="{LISTING_PATH}">Tin một</a>
    <a href="{LISTING_URL}?tracking=1#summary">Tin một lần nữa</a>
    <a href="{second}">Tin hai</a>
    <a href="/ban-nha-dat">Danh mục</a>
    <a href="/cho-thue-can-ho/can-ho-es789">Cho thuê</a>
    <a href="https://example.org{LISTING_PATH}">Ngoài nguồn</a>"""
    assert extract_listing_links(html) == [LISTING_URL, "https://homedy.com" + second]


def test_sale_detail_maps_to_existing_raw_schema():
    record = parse_listing_html(listing_html(), LISTING_URL)
    assert record["url"] == LISTING_URL
    assert str(record["listing_id"]) == "123"
    assert record["source"] == "homedy"
    assert record["source_type"] == "website"
    assert record["is_synthetic"] is False
    assert record["verified"] == 0
    assert record["listing_type"] == "Bán"
    assert compact(record["price_text"]) == "10,8tỷ"
    assert compact(record["area_text"]) == "33m2"
    assert record["property_type"] == "house"
    assert record["province_slug"] == "ho-chi-minh"
    assert record["district_slug"] == "quan-1"
    assert record["legal_text"] == "Sổ hồng riêng"
    assert record["posted_date_text"] == "10/09/2026"
    assert record["scraped_at"]


def test_unknown_optional_attributes_are_not_invented():
    record = parse_listing_html(listing_html(), LISTING_URL)
    for key in (
        "bedroom_text",
        "bathroom_text",
        "floor_text",
        "front_width_text",
        "road_width_text",
        "direction_text",
        "furniture_text",
        "ward_slug",
    ):
        assert record.get(key) is None, key


def test_derived_price_and_recommendation_values_do_not_replace_main_price():
    sidebar = """<aside class="recommendations">
      <div class="product-short-info"><div class="short-item">
        <span>Giá</span><strong>999 Tỷ</strong></div>
        <div class="short-item"><span>Diện tích</span><strong>999 m2</strong></div>
      </div></aside>"""
    record = parse_listing_html(listing_html(extra=sidebar), LISTING_URL)
    assert compact(record["price_text"]) == "10,8tỷ"
    assert "327" not in record["price_text"]
    assert compact(record["area_text"]) == "33m2"


def test_missing_main_price_is_not_filled_from_a_sidebar():
    sidebar = """<aside><div class="product-short-info"><div class="short-item">
      <span>Giá</span><strong>999 Tỷ</strong></div></div></aside>"""
    record = parse_listing_html(listing_html(price=None, extra=sidebar), LISTING_URL)
    assert record["price_text"] is None


def test_missing_area_remains_missing():
    record = parse_listing_html(listing_html(area=None), LISTING_URL)
    assert record["area_text"] is None


def test_per_square_metre_price_keeps_its_unit_for_downstream_parser():
    record = parse_listing_html(listing_html(price="100 Triệu/m2"), LISTING_URL)
    assert compact(record["price_text"]) == "100triệu/m2"


def test_dimensions_are_not_misrepresented_as_floor_area():
    record = parse_listing_html(listing_html(area="10 x 8 m"), LISTING_URL)
    assert record["area_text"] is None


def test_unknown_property_category_remains_unknown():
    url = "https://homedy.com/ban-bat-dong-san-khac/minh-hoa-es123"
    record = parse_listing_html(listing_html(canonical=url, property_type="Loại khác"), url)
    assert record["property_type"] is None


def test_organization_jsonld_geo_does_not_become_listing_coordinates():
    organization = """<script type="application/ld+json">{
      "@context":"https://schema.org", "@type":"Organization",
      "name":"Tổ chức minh họa", "geo":{
        "@type":"GeoCoordinates", "latitude":21.0123, "longitude":105.0123
      }
    }</script>"""
    record = parse_listing_html(listing_html(extra=organization), LISTING_URL)
    assert record.get("latitude") is None
    assert record.get("longitude") is None


@pytest.mark.parametrize("html", ["", "<html><h1>Thông báo</h1></html>", "<html><h1>Just a moment...</h1></html>"])
def test_missing_detail_structure_is_not_a_listing(html):
    with pytest.raises(SourceError):
        parse_listing_html(html, LISTING_URL)


def test_canonical_listing_id_must_match_requested_detail():
    other = LISTING_URL.replace("es123", "es456")
    with pytest.raises(SourceError):
        parse_listing_html(listing_html(canonical=other), LISTING_URL)


def test_supplied_listing_id_must_match_url_id():
    with pytest.raises(SourceError):
        parse_listing_html(listing_html(listing_id="456"), LISTING_URL)


@pytest.mark.parametrize("listing_type", ["Cho thuê", "Thuê", "Mua"])
def test_non_sale_detail_is_rejected_even_with_sale_url(listing_type):
    with pytest.raises(SourceError):
        parse_listing_html(listing_html(listing_type=listing_type), LISTING_URL)


class FakeResponse:
    def __init__(self, body="", status=200, content_type="text/html; charset=utf-8", headers=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.body = body.encode("utf-8") if isinstance(body, str) else body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]


@pytest.fixture
def fake_http(monkeypatch):
    """All trial network and sleep operations are stubs, not public requests."""
    session = Mock()
    session.headers = {}
    session_factory = Mock(return_value=session)
    monkeypatch.setattr(module.requests, "Session", session_factory)
    monkeypatch.setattr(module.time, "sleep", Mock())
    return session


def allow_all(client):
    client.rules = RobotFileParser()
    client.rules.parse(["User-agent: *", "Allow: /"])
    return client


@pytest.mark.parametrize("delay", [-1, 0, 1.9, 30.1, float("inf"), float("nan")])
def test_delay_bounds_fail_before_creating_http_session(delay, monkeypatch):
    session_factory = Mock(side_effect=AssertionError("must not start HTTP"))
    monkeypatch.setattr(module.requests, "Session", session_factory)
    with pytest.raises(ValueError, match="delay"):
        module.TrialClient(delay)
    session_factory.assert_not_called()


@pytest.mark.parametrize("delay", [2, 30])
def test_delay_boundary_values_are_allowed(delay, fake_http):
    client = module.TrialClient(delay)
    assert client.delay == delay
    assert fake_http.headers["User-Agent"] == module.USER_AGENT
    client.close()
    fake_http.close.assert_called_once()


@pytest.mark.parametrize("limit", [False, True, 0, -1, 11, 1.5, "5"])
def test_record_limit_bounds_fail_before_http_or_output(limit, monkeypatch, tmp_path):
    client_factory = Mock(side_effect=AssertionError("must not start trial"))
    monkeypatch.setattr(module, "TrialClient", client_factory)
    monkeypatch.setattr(module, "OUTPUT_ROOT", tmp_path / "trial")
    with pytest.raises(ValueError, match="limit"):
        module.run_trial(limit=limit)
    client_factory.assert_not_called()
    assert not (tmp_path / "trial").exists()


def test_client_requires_robots_before_listing_request(fake_http):
    client = module.TrialClient()
    with pytest.raises(SourceError, match="robots_disallowed"):
        client.get(module.LIST_URL)
    assert client.request_count == 0
    fake_http.get.assert_not_called()


def test_robots_disallow_blocks_listing_without_another_http_request(fake_http):
    fake_http.get.return_value = FakeResponse("User-agent: *\nDisallow: /ban-", content_type="text/plain")
    client = module.TrialClient()
    client.load_robots()
    with pytest.raises(SourceError, match="robots_disallowed"):
        client.get(module.LIST_URL)
    assert client.request_count == 1
    assert fake_http.get.call_count == 1


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("<html>Not robots policy</html>", "unexpected_robots_html"),
        ("User-agent: *\nAllow: /\nContent-signal: ai-train=no", "source_disallows_training_use"),
        ("User-agent: *\nAllow: /\nCrawl-delay: 31", "source_crawl_delay_exceeds_trial_budget"),
    ],
)
def test_robots_failures_stop_trial_access(body, error, fake_http):
    fake_http.get.return_value = FakeResponse(body, content_type="text/plain")
    client = module.TrialClient()
    with pytest.raises(SourceError, match=error):
        client.load_robots()
    assert fake_http.get.call_count == 1


def test_robots_crawl_delay_is_respected(fake_http):
    fake_http.get.return_value = FakeResponse("User-agent: *\nAllow: /\nCrawl-delay: 7", content_type="text/plain")
    client = module.TrialClient(delay=2)
    client.load_robots()
    assert client.delay == 7


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(status=403), "source_http_403"),
        (FakeResponse(status=429), "source_http_429"),
        (FakeResponse(status=302, headers={"Location": "https://example.org"}), "source_http_302"),
        (FakeResponse(headers={"cf-mitigated": "challenge"}), "source_access_challenge"),
        (FakeResponse(content_type="application/json"), "unexpected_content_type"),
        (FakeResponse(body=b"\xff\xfe"), "source_transport_UnicodeDecodeError"),
    ],
)
def test_http_rejection_stops_without_retry_or_redirect(response, error, fake_http):
    fake_http.get.return_value = response
    client = allow_all(module.TrialClient())
    with pytest.raises(SourceError, match=error):
        client.get(module.LIST_URL)
    assert fake_http.get.call_count == 1
    assert client.request_count == 1
    assert fake_http.get.call_args.kwargs == {
        "timeout": (5, 15), "allow_redirects": False, "stream": True,
    }


def test_transport_timeout_is_sanitized_and_not_retried(fake_http):
    fake_http.get.side_effect = module.requests.Timeout("private response detail must not be logged")
    client = allow_all(module.TrialClient())
    with pytest.raises(SourceError, match="^source_transport_Timeout$"):
        client.get(module.LIST_URL)
    assert fake_http.get.call_count == 1


@pytest.mark.parametrize(
    "url",
    ["http://homedy.com/ban-nha-dat", "https://example.org/ban-nha-dat", "https://homedy.com/ban-nha-dat?page=2", "https://homedy.com/ban-nha-dat#x", "https://user:password@homedy.com/ban-nha-dat"],
)
def test_client_rejects_unexpected_origin_or_url_before_network(url, fake_http):
    client = allow_all(module.TrialClient())
    with pytest.raises(SourceError, match="unexpected_source_url"):
        client.get(url)
    fake_http.get.assert_not_called()


def test_response_size_is_bounded(fake_http, monkeypatch):
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 64)
    fake_http.get.return_value = FakeResponse(body="x" * 65)
    client = allow_all(module.TrialClient())
    with pytest.raises(SourceError, match="source_response_too_large"):
        client.get(module.LIST_URL)


def trial_responses(count, *, bad_detail=None, missing_price=False):
    urls = [LISTING_URL.replace("es123", f"es{100 + index}") for index in range(count)]
    responses = [
        FakeResponse("User-agent: *\nAllow: /", content_type="text/plain"),
        FakeResponse("".join(f'<a href="{url}">Fabricated listing</a>' for url in urls)),
    ]
    for index, url in enumerate(urls):
        html = listing_html(canonical=url, listing_id=str(100 + index), area="33m2", price=None if missing_price else "10,8 Tỷ")
        responses.append(FakeResponse(html if bad_detail != index else "<h1>Not a listing</h1>"))
    return responses


@pytest.mark.parametrize("limit", [1, 10])
def test_trial_respects_limit_and_only_writes_local_output(limit, fake_http, monkeypatch, tmp_path):
    import processing.kafka_to_mongo as processor

    forbidden = Mock(side_effect=AssertionError("source trial must not connect to Kafka or MongoDB"))
    for name in ("Consumer", "Producer", "MongoClient", "KafkaToMongoPipeline"):
        monkeypatch.setattr(processor, name, forbidden)
    monkeypatch.setattr(module, "OUTPUT_ROOT", tmp_path / "trial")
    fake_http.get.side_effect = trial_responses(limit + 1)
    report, output = module.run_trial(limit=limit, delay=2)
    assert report["status"] == "passed"
    assert report["records"] == report["usable_records"] == limit
    assert report["http_requests"] == limit + 2  # robots + one index + bounded details
    assert report["discovered_urls"] == limit + 1
    assert report["kafka_published"] == report["mongo_writes"] == 0
    assert report["training_approved"] is False
    assert output.is_relative_to(tmp_path / "trial")
    assert {path.name for path in output.iterdir()} == {"records.jsonl", "validation.jsonl", "robots.txt", "report.json"}
    records = [json.loads(line) for line in (output / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    checks = [json.loads(line) for line in (output / "validation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == len(checks) == limit
    assert all(row["source"] == "homedy" and row["is_synthetic"] is False for row in records)
    assert all(row["price_vnd"] == 10_800_000_000 and row["area_m2"] == 33 for row in checks)
    assert all(row["training_approved"] is False for row in checks)
    assert json.loads((output / "report.json").read_text(encoding="utf-8")) == report
    forbidden.assert_not_called()
    fake_http.close.assert_called_once()


def test_failed_later_request_preserves_partial_records_and_failure_report(fake_http, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "OUTPUT_ROOT", tmp_path)
    responses = trial_responses(3)
    responses[3] = FakeResponse(status=429)  # second detail after one successful record
    fake_http.get.side_effect = responses
    report, output = module.run_trial(limit=3)
    assert report["status"] == "failed"
    assert report["error"] == "source_http_429"
    assert report["records"] == 1
    assert report["http_requests"] == fake_http.get.call_count == 4
    assert len((output / "records.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert json.loads((output / "report.json").read_text(encoding="utf-8"))["status"] == "failed"
    fake_http.close.assert_called_once()


def test_malformed_detail_is_reported_not_counted_as_success(fake_http, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "OUTPUT_ROOT", tmp_path)
    fake_http.get.side_effect = trial_responses(2, bad_detail=0)
    report, output = module.run_trial(limit=2)
    assert report["status"] == "needs_review"
    assert report["records"] == 1
    assert report["rejected"][0]["reason"] == "missing_listing_detail_structure"
    assert len((output / "records.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_oversized_detail_is_rejected_but_remaining_requested_detail_is_checked(fake_http, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 10_000)
    responses = trial_responses(2)
    responses[2] = FakeResponse(body="x" * 10_001)
    fake_http.get.side_effect = responses
    report, output = module.run_trial(limit=2)
    assert report["status"] == "needs_review"
    assert report["records"] == report["usable_records"] == 1
    assert report["http_requests"] == fake_http.get.call_count == 4
    assert report["rejected"] == [{
        "url": LISTING_URL.replace("es123", "es100"),
        "reason": "source_response_too_large",
    }]
    records = [json.loads(line) for line in (output / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["listing_id"] == "101"
    assert "error" not in report
    assert report["kafka_published"] == report["mongo_writes"] == 0
    assert report["training_approved"] is False


def test_missing_price_is_not_reported_as_usable_or_training_approved(fake_http, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "OUTPUT_ROOT", tmp_path)
    fake_http.get.side_effect = trial_responses(1, missing_price=True)
    report, output = module.run_trial(limit=1)
    check = json.loads((output / "validation.jsonl").read_text(encoding="utf-8"))
    assert report["status"] == "needs_review"
    assert report["records"] == 1
    assert report["usable_records"] == 0
    assert "price_vnd" in check["missing_required"]
    assert check["training_approved"] is False


def test_downstream_validation_converts_per_square_metre_price_once():
    raw = parse_listing_html(listing_html(price="100 Triệu/m2", area="33m2"), LISTING_URL)
    check = module.validate_trial_record(raw)
    assert check["price_vnd"] == 3_300_000_000
    assert check["area_m2"] == 33
    assert check["training_approved"] is False
