import re
from parsel import Selector

from processing.source_contract import make_record, label
from scraper.homedy_scraper import parse_listing_html, SourceError, _pairs
from .base import SourceAdapter, SourceShapeError, node_text


class HomedyAdapter(SourceAdapter):
    name = "homedy"
    category_url = "https://homedy.com/ban-nha-dat"
    detail_pattern = re.compile(r"/ban-[a-z0-9-]+/[a-z0-9-]+-es(\d+)")
    cards = ".product-item h3 a::attr(href)"

    def parse(self, html, url):
        try:
            data = parse_listing_html(html, url)
        except SourceError as error:
            raise SourceShapeError(str(error)) from None
        # Retain the original range/dimensions even though the historical trial
        # adapter intentionally nulls them. Strict common normalization rejects.
        top = Selector(text=html).css(".product-detail-top-left")
        short = _pairs(top.css(".product-short-info .short-item"), ":scope > span", "strong")
        data["area_text"] = short.get("dien tich")
        if data["area_text"]:
            data["area_text"] = re.sub(r"m\s+(2|²)", r"m\1", data["area_text"], flags=re.I)
        data["transaction_type"] = "sell"
        attrs = _pairs(Selector(text=html).css(".product-attributes--item"), ":scope > span:first-child", ":scope > span:nth-child(2)")
        data["property_type_raw"] = attrs.get("loai hinh")
        breadcrumbs = list(top.css(".breadcrumb li a"))
        for index, node in enumerate(breadcrumbs):
            if attrs.get("loai hinh") and label(node_text(node)) == label(attrs["loai hinh"]):
                for key, location in zip(("province", "district", "ward"), breadcrumbs[index + 1:]):
                    if data.get(key + "_slug"):
                        data[key] = node_text(location)
                break
        data["address_version"] = "source_breadcrumb"
        # These slugs come from explicit breadcrumb hierarchy, never URL tokens.
        return make_record(self.name, data["url"], data)
