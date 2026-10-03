"""Version 2 listing contract. Pure, strict, source-independent interpretation.

Old unversioned Kafka records remain supported by the legacy normalizer. Never
run first-number parsing on a versioned website amount or dimension.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 2
DOMAINS = {"alonhadat": "alonhadat.com.vn", "guland": "guland.vn", "homedy": "homedy.com"}
TYPES = {"house", "apartment", "land", "villa_townhouse", "shophouse", "warehouse", "office"}


def text(value):
    if not isinstance(value, str):
        return None
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip() or None


def label(value):
    value = (text(value) or "").lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def slug(value):
    value = label(value)
    value = re.sub(r"^(?:tinh|thanh pho|tp\.?|huyen)\s+", "", value)
    # Named districts align with the existing feature vocabulary; keep quan-1.
    value = re.sub(r"^quan\s+(?=[a-z])", "", value)
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-") or None


def canonical_url(source, value):
    try:
        parts = urlsplit(value)
        if (source not in DOMAINS or parts.scheme != "https" or parts.netloc != DOMAINS[source]
                or parts.username or parts.password or not parts.path or parts.path == "/"):
            return None
        return urlunsplit(("https", parts.netloc, parts.path, "", ""))
    except (TypeError, ValueError, AttributeError):
        return None


def decimal_token(value):
    """3-digit groups mean thousands; 1-2/4+ trailing digits mean decimals.

    Mixed separators require valid thousands groups followed by a decimal.
    Signs, ranges, dimensions and arbitrary text are rejected, never truncated.
    """
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)*", value):
        raise ValueError("invalid_number")
    if "." in value and "," in value:
        decimal = "." if value.rfind(".") > value.rfind(",") else ","
        grouping = "," if decimal == "." else "."
        whole, fraction = value.rsplit(decimal, 1)
        if not re.fullmatch(r"\d{1,3}(?:" + re.escape(grouping) + r"\d{3})+", whole):
            raise ValueError("ambiguous_number")
        normalized = whole.replace(grouping, "") + "." + fraction
    elif "." in value or "," in value:
        separator = "." if "." in value else ","
        pieces = value.split(separator)
        if len(pieces[0]) <= 3 and all(len(p) == 3 for p in pieces[1:]):
            normalized = "".join(pieces)
        elif len(pieces) == 2 and len(pieces[1]) != 3:
            normalized = ".".join(pieces)
        else:
            raise ValueError("ambiguous_number")
    else:
        normalized = value
    try:
        result = Decimal(normalized)
    except InvalidOperation:
        raise ValueError("invalid_number") from None
    if not result.is_finite() or result <= 0:
        raise ValueError("non_positive_number")
    return float(result)


def area_value(raw):
    if text(raw) is None:
        return None, None
    match = re.fullmatch(r"([\d.,]+)\s*(?:m2|m²)", text(raw), re.I)
    try:
        if not match:
            raise ValueError("area_unparseable")
        value = decimal_token(match[1])
        if value > 10000:
            raise ValueError("area_out_of_range")
        return value, None
    except ValueError:
        return None, "area_unparseable_or_out_of_range"


def price_value(raw, area=None):
    value = label(raw)
    result = {"price_value": None, "price_unit": None, "price_vnd": None,
              "price_per_m2_vnd": None, "price_is_negotiable": False}
    if not value:
        return result, None
    if value in {"thoa thuan", "lien he", "gia thoa thuan", "thuong luong"}:
        result["price_is_negotiable"] = True
        return result, None
    match = re.fullmatch(r"([\d.,]+)\s*(ty|trieu|tr|vnd|dong|d)?\s*(/\s*m(?:2|²))?", value)
    try:
        if not match:
            raise ValueError("price_unparseable")
        amount = decimal_token(match[1])
        unit = match[2] or "vnd"
        # All adapters read labelled Vietnamese asking prices. Unitless large
        # integer/grouped amounts are VND; a bare '2.5' is not assumed billions.
        if not match[2] and (amount < 100000 or amount != int(amount)):
            raise ValueError("price_ambiguous_unit")
        multiplier = {"ty": 1e9, "trieu": 1e6, "tr": 1e6}.get(unit, 1)
        total = amount * multiplier
        per_area = bool(match[3])
        result.update(price_value=amount, price_unit="vnd_per_m2" if per_area else "vnd_total")
        if per_area:
            result["price_per_m2_vnd"] = total
            total = total * area if area else None
        elif area:
            result["price_per_m2_vnd"] = total / area
        if total is not None and (total <= 0 or total > 500e9):
            raise ValueError("price_out_of_range")
        result["price_vnd"] = total
        return result, None
    except (ValueError, OverflowError):
        return {**result, "price_value": None, "price_unit": None, "price_vnd": None,
                "price_per_m2_vnd": None}, "price_unparseable_or_out_of_range"


def optional_number(raw, *, count=False):
    value = text(raw)
    if value in (None, "---", "-"):
        return None
    if count and value == "0":
        return 0
    match = re.fullmatch(r"([\d.,]+)\s*(?:m|phòng|tầng)?", value, re.I)
    if not match:
        return None
    try:
        number = decimal_token(match[1])
        if count and (number != int(number) or number > 100):
            return None
        return int(number) if count else number
    except ValueError:
        return None


def posted_date(raw):
    if not text(raw):
        return None
    for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text(raw), pattern).date().isoformat()
        except ValueError:
            pass
    return None  # Relative update times are not exact posting dates.


def normalize_contract(raw):
    """Revalidate at consumer boundary; never trust producer numeric fields."""
    result = dict(raw)
    errors = list(raw.get("source_errors") or [])
    source = raw.get("source")
    canonical = canonical_url(source, raw.get("source_url"))
    if not canonical or canonical != raw.get("canonical_url"):
        errors.append("invalid_source_url")
    from agents.safety import is_synthetic_record
    if not is_synthetic_record(raw) and raw.get("url") != canonical:
        errors.append("source_identity_mismatch")
    if raw.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported_schema_version")
    if not text(raw.get("source_listing_id")):
        errors.append("missing_source_listing_id")
    if not text(raw.get("title")):
        errors.append("missing_title")
    if raw.get("transaction_type") != "sell":
        errors.append("not_explicit_sale")
    if not text(raw.get("crawl_timestamp")):
        errors.append("missing_crawl_timestamp")
    else:
        try:
            if datetime.fromisoformat(raw["crawl_timestamp"]).tzinfo is None:
                raise ValueError("timezone_missing")
        except (TypeError, ValueError):
            errors.append("invalid_crawl_timestamp")
    ai = raw.get("processing_method") == "ai_extraction" and raw.get("ai_status") == "success"
    area_raw = raw.get("area_text") if ai else raw.get("area_raw")
    price_raw = raw.get("price_text") if ai else raw.get("price_raw")
    area, area_error = area_value(area_raw)
    price, price_error = price_value(price_raw, area)
    errors.extend(e for e in (area_error, price_error) if e)
    result.update(price)
    result.update(area_m2=area, validation_errors=list(dict.fromkeys(errors)))
    result["property_type"] = raw.get("property_type") if raw.get("property_type") in TYPES else None
    if raw.get("property_type_raw") and not result["property_type"]:
        errors.append("unsupported_property_type")
        result["validation_errors"] = list(dict.fromkeys(errors))
    result["price_text"] = (format(price["price_vnd"] / 1e9, ".8f") + " tỷ") if price["price_vnd"] else None
    result["area_text"] = (format(area, ".8f") + " m2") if area else None
    # Use canonical numeric values downstream, not legacy locale reparsing.
    for field, alias in (("bedroom_count", "bedroom_text"), ("bathroom_count", "bathroom_text"),
                         ("floor_count", "floor_text"), ("front_width_m", "front_width_text"),
                         ("road_width_m", "road_width_text")):
        result[field] = optional_number(raw.get(alias), count=field.endswith("_count"))
    missing = [key for key in ("price_vnd", "area_m2", "property_type", "province_slug") if result.get(key) is None]
    result["validation_warnings"] = ["missing_" + key for key in missing]
    result["extraction_status"] = "invalid" if errors else "partial" if missing else "valid"
    result["training_excluded"] = bool(errors or missing)
    return result


def make_record(source, url, extracted):
    canonical = canonical_url(source, url)
    data = {key: value for key, value in extracted.items() if value is not None}
    timestamp = datetime.now(timezone.utc).isoformat()
    identifier = text(data.get("listing_id")) or hashlib.sha256((canonical or str(url)).encode()).hexdigest()
    result = {
        **data, "schema_version": SCHEMA_VERSION, "source": source,
        "source_listing_id": identifier, "listing_id": identifier,
        "source_url": canonical or url, "canonical_url": canonical, "url": canonical or url,
        "crawl_timestamp": timestamp, "scraped_at": timestamp,
        "raw_payload_hash": hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "raw_data": data, "price_raw": data.get("price_text"), "area_raw": data.get("area_text"),
        "address_raw": data.get("address"), "source_type": "website", "is_synthetic": False,
        "verified": 0, "listing_type": data.get("transaction_type"),
        "posted_at": posted_date(data.get("posted_date_text")),
        "title": text(data.get("title")), "description": text(data.get("description")),
        "latitude": None, "longitude": None,
    }
    for field in ("province", "district", "ward"):
        result[field] = text(data.get(field))
        result[field + "_slug"] = slug(result[field]) if result[field] else data.get(field + "_slug")
    return normalize_contract(result)
