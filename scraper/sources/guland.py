"""Offline parser for observed Guland markup; live access remains disabled."""
import re
from urllib.parse import urlsplit
from parsel import Selector

from processing.source_contract import label, make_record
from .base import SourceAdapter, SourceShapeError, node_text


class GulandAdapter(SourceAdapter):
    name = "guland"
    category_url = "https://guland.vn/mua-ban-bat-dong-san-tp-ho-chi-minh"
    detail_pattern = re.compile(r"/post/[^/]+-(\d+)")
    cards = ".c-sdb-card__tle a::attr(href)"
    disabled_reason = "collection_permission_and_live_revalidation_required"

    def parse(self, html, url):
        canonical = self.detail_url(url)
        root = Selector(text=html).css(".dtl-col-lft")
        if not canonical or len(root) != 1 or not node_text(root.css("h1")):
            raise SourceShapeError("missing_guland_detail_structure")
        attrs = {}
        for row in root.css(".s-dtl-inf"):
            key = label(node_text(row.css(".s-dtl-inf__lbl")))
            if key:
                attrs[key] = node_text(row.css(".s-dtl-inf__val"))
        # Do not read sibling per-m2/per-frontage prices, nor residential subarea.
        data = {
            "listing_id": self.detail_pattern.fullmatch(urlsplit(canonical).path)[1],
            "title": node_text(root.css("h1")),
            "description": node_text(root.css(".dtl-des")),
            "price_text": node_text(root.css(".dtl-prc__ttl")),
            "area_text": node_text(root.css(".dtl-prc__dtc")),
            "transaction_type": "sell" if label(attrs.get("loai tin")) in {"ban", "can ban"} else None,
            "property_type": {"dat": "land", "nha rieng": "house", "can ho": "apartment"}.get(label(attrs.get("loai bat dong san"))),
            "front_width_text": attrs.get("mat tien"), "road_width_text": attrs.get("duong vao"),
            "bedroom_text": attrs.get("phong ngu"), "bathroom_text": attrs.get("phong tam"),
            "residential_area_raw": attrs.get("dien tich tho cu"),
            "source_update_raw": node_text(root.css(".dtl-date")),
            "source_errors": ["guland_live_mapping_requires_review"],
        }
        # Conservative intentionally incomplete mapping. Historical old/new
        # geography and multiple category tags require permission + live review.
        return make_record(self.name, canonical, data)
