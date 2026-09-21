import re
from urllib.parse import urlsplit
from parsel import Selector

from processing.source_contract import label, make_record
from .base import SourceAdapter, SourceShapeError, explicit_address, node_text


class AlonhadatAdapter(SourceAdapter):
    name = "alonhadat"
    category_url = "https://alonhadat.com.vn/can-ban-nha-dat"
    detail_pattern = re.compile(r"/[^/]+-(\d+)\.html")
    cards = "section.list-property-box article.property-item a.link::attr(href)"

    def parse(self, html, url):
        canonical = self.detail_url(url)
        if not canonical:
            raise SourceShapeError("unsupported_detail_url")
        root = Selector(text=html).css("article.property")
        title = node_text(root.css("h1"))
        if len(root) != 1 or not title:
            raise SourceShapeError("missing_alonhadat_detail_structure")
        attrs = {}
        for row in root.css(".moreinfor1 tr"):
            cells = row.css("td")
            for index in range(0, len(cells) - 1, 2):
                attrs[label(node_text(cells[index]))] = node_text(cells[index + 1])
        identifier = self.detail_pattern.fullmatch(urlsplit(canonical).path)[1]
        errors = []
        if attrs.get("ma tin") and attrs["ma tin"] != identifier:
            errors.append("source_listing_id_mismatch")
        price = node_text(root.css(".price data.value, .price .value"))
        # Prefer the explicitly scoped display value, not title/URL numbers.
        area = node_text(root.css(".area .value"))
        if area and not re.search(r"m(?:²|2)", area):
            unit = node_text(root.css(".area")) or ""
            if re.search(r"m(?:²|2)", unit):
                area += " m2"
        old = node_text(root.css(".old-address"))
        current = node_text(root.css(".current-address"))
        address = old or current
        kinds = {"nha mat tien": "house", "nha trong hem": "house", "nha rieng": "house",
             "nha pho": "house", "nha o": "house",
                 "can ho chung cu": "apartment", "can ho": "apartment", "dat tho cu": "land",
             "dat tho cu, dat o": "land", "dat o": "land", "dat nen": "land",
             "dat nong nghiep": "land", "biet thu": "villa_townhouse",
             "biet thu, nha lien ke": "villa_townhouse", "nha lien ke": "villa_townhouse",
                 "kho, nha xuong": "warehouse", "van phong": "office"}
        data = {
            "listing_id": identifier, "title": title,
            "description": node_text(root.css(".detail.text-content")),
            "price_text": price, "area_text": area,
            "transaction_type": "sell" if label(attrs.get("loai tin")) == "can ban" else None,
            "property_type": kinds.get(label(attrs.get("loai bds"))),
            "property_type_raw": attrs.get("loai bds"),
            "bedroom_text": attrs.get("so phong ngu"), "bathroom_text": None,
            # 'Số lầu' is not unambiguously the total number of floors.
            "floor_text": None, "source_floor_raw": attrs.get("so lau"),
            "front_width_text": attrs.get("chieu ngang"), "length_raw": attrs.get("chieu dai"),
            "road_width_text": attrs.get("duong truoc nha"),
            "legal_text": attrs.get("phap ly"), "direction_text": attrs.get("huong"),
            "address": address, "address_old": old, "address_current": current,
            "address_version": "legacy" if old else "current",
            "posted_date_text": node_text(root.css('time[itemprop="datePosted"]')),
            "source_errors": errors, **explicit_address(address),
        }
        return make_record(self.name, canonical, data)
