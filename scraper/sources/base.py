"""Static HTML adapter interface; HTTP policy is owned by the common crawler."""
from abc import ABC, abstractmethod
import re
from urllib.parse import urljoin, urlsplit

from parsel import Selector
from processing.source_contract import canonical_url, label, text


class SourceShapeError(ValueError):
    pass


def node_text(nodes):
    return text(" ".join(nodes.xpath(".//text()").getall()))


def explicit_address(value):
    """Labelled hierarchy only. Never combine old district with new ward."""
    result = {"province": None, "district": None, "ward": None, "street": None}
    parts = [text(p) for p in (value or "").split(",") if text(p)]
    normalized_parts = [label(part) for part in parts]
    for part in parts:
        normalized = label(part)
        if re.match(r"^(phuong|xa|thi tran)\s", normalized):
            result["ward"] = part
        elif re.match(r"^(quan|huyen|thi xa|thanh pho)\s", normalized):
            result["district"] = part
        elif re.match(r"^(duong|pho)\s", normalized):
            result["street"] = part
    if parts:
        last = label(parts[-1])
        has_explicit_city_or_district = any(
            re.match(r"^(quan|huyen|thi xa|thanh pho)\s", item)
            for item in normalized_parts[:-1]
        )
        if (re.match(r"^(tinh|tp\.?|thanh pho)\s", last)
                or last in {
            "ha noi", "ho chi minh", "da nang", "hai phong", "can tho", "hue",
            "binh duong", "long an", "dong nai", "ba ria vung tau", "binh phuoc",
                }
                or has_explicit_city_or_district):
            result["province"] = parts[-1]
    return result


class SourceAdapter(ABC):
    name: str
    category_url: str
    detail_pattern: re.Pattern
    cards: str
    disabled_reason = None

    def detail_url(self, href):
        url = canonical_url(self.name, urljoin(self.category_url, href))
        return url if url and self.detail_pattern.fullmatch(urlsplit(url).path) else None

    def discover(self, html):
        urls = []
        for href in Selector(text=html).css(self.cards).getall():
            url = self.detail_url(href)
            if url and url not in urls:
                urls.append(url)
        if not urls:
            raise SourceShapeError("listing_structure_missing_or_empty")
        return urls

    def next_page(self, html, current_url):
        """Only follow actual same-category pagination anchors, never guess URLs."""
        current = urlsplit(current_url)
        page_match = re.search(r"/(?:trang-|p)(\d+)$", current.path)
        wanted = (int(page_match[1]) if page_match else 1) + 1
        prefix = urlsplit(self.category_url).path
        expected = prefix + (f"/trang-{wanted}" if self.name == "alonhadat" else f"/p{wanted}")
        for href in Selector(text=html).css("a::attr(href)").getall():
            parts = urlsplit(urljoin(current_url, href))
            if parts.scheme == "https" and parts.netloc == current.netloc and parts.path == expected and not parts.query:
                return "https://" + parts.netloc + parts.path
        return None

    @abstractmethod
    def parse(self, html, url):
        """Return one canonical v2 record, never a fabricated empty listing."""
