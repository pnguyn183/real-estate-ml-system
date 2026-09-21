"""Deterministic synthetic listing generation; never contacts a website or LLM."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import random
import re
from dataclasses import dataclass
from typing import Any

SCENARIOS = ("normal", "volume", "burst", "duplicate", "variation", "missing", "malformed", "unstructured", "semi_structured", "mixed")
SEEDS = (
    {"area_m2": 80, "price_vnd": 8_000_000_000, "bedroom_count": 2, "bathroom_count": 2, "property_type": "apartment", "province_slug": "ho-chi-minh", "district_slug": "quan-1"},
    {"area_m2": 65, "price_vnd": 3_250_000_000, "bedroom_count": 2, "bathroom_count": 1, "property_type": "apartment", "province_slug": "ha-noi", "district_slug": "dong-da"},
    {"area_m2": 120, "price_vnd": 6_000_000_000, "bedroom_count": 3, "bathroom_count": 2, "property_type": "house", "province_slug": "da-nang", "district_slug": "hai-chau"},
    {"area_m2": 70, "price_vnd": 5_000_000_000, "bedroom_count": 2, "bathroom_count": 1, "property_type": "apartment", "province_slug": "ha-noi", "district_slug": "cau-giay"},
)


def decimal_text(value: float) -> str:
    text = f"{value:g}"
    # The existing raw parser interprets exactly three decimals as a thousands group.
    return text + "0" if "." in text and len(text.split(".")[1]) == 3 else text


@dataclass(frozen=True)
class GeneratedListing:
    payload: dict[str, Any]
    ground_truth: dict[str, Any]
    duplicate: bool = False
    unstructured: bool = False


class ListingGenerator:
    def __init__(self, run_id: str, scenario: str = "normal", seed: int = 42,
                 duplicate_ratio: float = 0.2, unstructured_ratio: float = 0.3,
                 templates: list[dict[str, Any]] | None = None, generated_at: str | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError("run_id must contain 1-80 letters, digits, underscore or hyphen")
        if scenario not in SCENARIOS:
            raise ValueError("unsupported stress scenario")
        if not 0 <= duplicate_ratio <= 1 or not 0 <= unstructured_ratio <= 1:
            raise ValueError("ratios must be in [0,1]")
        self.run_id, self.scenario = run_id, scenario
        self.random = random.Random(seed)
        self.duplicate_ratio, self.unstructured_ratio = duplicate_ratio, unstructured_ratio
        self.templates = copy.deepcopy(templates or list(SEEDS))
        self.previous: GeneratedListing | None = None
        self.generated_at = generated_at or datetime.now(timezone.utc).isoformat()

    def next(self, index: int) -> GeneratedListing:
        rng = self.random
        duplicate = self.scenario in {"duplicate", "mixed"} and self.previous is not None and rng.random() < self.duplicate_ratio
        if duplicate:
            payload = copy.deepcopy(self.previous.payload)
            truth = copy.deepcopy(self.previous.ground_truth)
            # Repeat URL, exact replay, or same facts under a different URL.
            duplicate_kind = ("alternate_url", "near_same_url", "exact")[index % 3]
            if duplicate_kind == "near_same_url":
                payload["description"] = str(payload.get("description", "")) + " Tin đăng cập nhật."
                if "raw_text" in payload:
                    payload["raw_text"] = payload["description"]
            elif duplicate_kind == "alternate_url":
                payload["url"] = f"https://synthetic.invalid/{self.run_id}/listing-{index:07d}"
                payload["listing_id"] = f"listing-{index:07d}"
            return GeneratedListing(payload, {**truth, "url": payload["url"], "sequence": index,
                "duplicate": True, "duplicate_kind": duplicate_kind}, True, self.previous.unstructured)

        values = copy.deepcopy(rng.choice(self.templates))
        template_id = "template-" + hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:20]
        kind = self.scenario
        if kind == "mixed":
            kind = "unstructured" if rng.random() < self.unstructured_ratio else rng.choice(("normal", "variation", "missing", "semi_structured", "malformed"))
        if kind in {"normal", "volume", "burst", "variation"}:
            # Correlated size/price noise avoids copying only a handful of fixed fixtures.
            unit_price = values["price_vnd"] / values["area_m2"]
            values["area_m2"] = max(20, min(10000, round(values["area_m2"] * rng.uniform(.85, 1.15))))
            values["price_vnd"] = max(10_000_000, min(500_000_000_000,
                round(unit_price * values["area_m2"] * rng.uniform(.9, 1.1) / 10_000_000) * 10_000_000))
        identity = f"listing-{index:07d}"
        raw = {
            "url": f"https://synthetic.invalid/{self.run_id}/{identity}",
            "listing_id": identity, "original_record_id": template_id,
            "source": "stress_agent", "source_type": "stress_agent",
            "is_synthetic": True, "generated_by": "stress_agent",
            "run_id": self.run_id, "scenario": self.scenario,
            "generated_at": self.generated_at,
            "format": "structured", "listing_type": "Ban", "verified": 0,
            "title": "Tin bất động sản mô phỏng", "property_type": values["property_type"],
            "province_slug": values["province_slug"], "district_slug": values["district_slug"],
            "area_text": f"{values['area_m2']:g} m2", "price_text": f"{decimal_text(values['price_vnd'] / 1e9)} tỷ",
            "bedroom_text": str(values["bedroom_count"]), "bathroom_text": str(values["bathroom_count"]),
            "legal_text": "sổ hồng", "description": "Dữ liệu mô phỏng, không phải tin bán thật.",
        }
        missing_fields: list[str] = []
        if kind == "variation":
            raw["price_text"] = f"{values['price_vnd'] / 1e6:g} triệu"
            raw["area_text"] = f"  {values['area_m2']:g} m²  "
            raw["title"] = "CĂN HỘ / Nhà bán - nội dung mô phỏng"
        elif kind == "missing":
            for key in ("price_text", "bathroom_text"):
                raw.pop(key, None)
            missing_fields = ["price_vnd", "bathroom_count"]
        elif kind == "malformed":
            raw["price_text"], raw["area_text"] = "giá ??? liên hệ", "diện tích chưa xác định"
            missing_fields = ["price_vnd", "area_m2"]
        unstructured = kind in {"unstructured", "semi_structured"}
        if unstructured:
            area, price, beds, baths = values["area_m2"], values["price_vnd"] / 1e9, values["bedroom_count"], values["bathroom_count"]
            location = f"{values['district_slug']}, {values['province_slug']}"
            sentences = [
                f"Bán {values['property_type']} tại {location}. DT {area:g}m2, {beds} PN, {baths} WC, giá {price:g} tỷ. Sổ hồng.",
                f"For sale: {values['property_type']}, {location}. Area {area:g} m2; {beds} bedrooms, {baths} bathrooms. Price {price:g} billion VND.",
                f"can ban {values['property_type']} o {location}, dt {area:g} m2, {beds} phong ngu, {baths} wc; gia {price:g} ty. tin mo phong",
            ]
            if (area, values["price_vnd"], beds, baths) == (70, 5_000_000_000, 2, 1):
                sentences.append(f"Bán căn hộ ở {location}. Diện tích bảy mươi mét vuông, hai phòng ngủ, một phòng tắm, giá năm tỷ đồng.")
            text = rng.choice(sentences)
            if kind == "semi_structured":
                text = f"Loại: {values['property_type']}\nKhu vực: {location}\nDT={area:g}m² | Giá={price:g} tỷ | {beds}PN/{baths}WC"
            for key in ("price_text", "area_text", "bedroom_text", "bathroom_text", "property_type", "province_slug", "district_slug", "legal_text"):
                raw.pop(key, None)
            raw.update({"format": kind, "raw_text": text, "description": text})
            if kind == "semi_structured":
                raw.update({"gia": f"{price:g} tỷ", "dien_tich": f"{area:g} m²", "phong_ngu": str(beds),
                            "loai_bds": values["property_type"], "khu_vuc": location})
        for field in missing_fields:
            values[field] = None
        truth = {"run_id": self.run_id, "original_record_id": template_id, "url": raw["url"], "sequence": index,
                 "scenario": kind, "duplicate": False, "duplicate_kind": None, "missing_fields": missing_fields, "expected": values}
        result = GeneratedListing(raw, truth, False, unstructured)
        self.previous = result
        return result
