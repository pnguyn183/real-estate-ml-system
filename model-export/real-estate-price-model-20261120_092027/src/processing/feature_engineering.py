"""Deterministic geographic feature helpers shared by ingestion and serving.

The scraper currently does not collect coordinates.  These helpers intentionally
preserve that fact: they only derive a grid cell when a source actually supplies
valid latitude and longitude; they never infer a point from a district slug.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Any, Mapping


GEO_NUMERIC_FEATURES = ("latitude", "longitude")
GEO_CATEGORICAL_FEATURES = ("geo_grid_2dp", "geo_coordinate_status")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def normalized_coordinates(record: Mapping[str, Any]) -> tuple[float | None, float | None, str]:
    """Read optional coordinates and return ``(lat, lon, status)`` safely.

    Accept common upstream aliases to keep the raw Kafka contract backward
    compatible. A coordinate pair is only valid inside the global WGS84 bounds.
    Country-specific bounds are deliberately not imposed because the project can
    ingest listings outside Vietnam in the future.
    """
    latitude = _finite_number(record.get("latitude", record.get("lat")))
    longitude = _finite_number(record.get("longitude", record.get("lng", record.get("lon"))))
    if latitude is None and longitude is None:
        return None, None, "MISSING"
    if latitude is None or longitude is None:
        return None, None, "INVALID"
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None, None, "INVALID"
    return latitude, longitude, "VALID"


def geo_grid(latitude: float | None, longitude: float | None, precision: int = 2) -> str | None:
    """Return a stable approximately kilometre-scale grid key without geocoding.

    A two-decimal-degree grid gives a useful non-target-derived locality signal
    while avoiding a high-cardinality exact-coordinate identifier.
    """
    if latitude is None or longitude is None:
        return None
    return f"{latitude:.{precision}f}:{longitude:.{precision}f}"


def enrich_geographic_features(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return schema-stable geographic fields for a raw or API record."""
    latitude, longitude, status = normalized_coordinates(record)
    return {
        "latitude": latitude,
        "longitude": longitude,
        "geo_grid_2dp": geo_grid(latitude, longitude),
        "geo_coordinate_status": status,
    }
