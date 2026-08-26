"""Offline-friendly text enrichment with deterministic local embeddings.

The project has no configured embedding or LLM provider.  This module therefore
uses a small local hashing embedding by default. It is deterministic, batchable,
requires no secret and is safe to run during inference.  The SQLite cache is used
by ingestion jobs so unchanged text does not need to be embedded again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer


TEXT_EMBEDDING_DIMENSIONS = 32
_VECTOR_PATTERN = re.compile(r"\s+")
_DIRECTION_PATTERN = re.compile(r"\b(?:huong|hướng)\s+(bac|bắc|nam|dong|đông|tay|tây|dong bac|đông bắc|dong nam|đông nam|tay bac|tây bắc|tay nam|tây nam)\b", re.IGNORECASE)
_BEDROOM_PATTERN = re.compile(r"\b(\d{1,2})\s*(?:phong|phòng)\s*(?:ngu|ngủ)\b|\b(\d{1,2})\s*pn\b", re.IGNORECASE)
_BATHROOM_PATTERN = re.compile(r"\b(\d{1,2})\s*(?:phong|phòng)\s*(?:tam|tắm|ve sinh|vệ sinh|vệ\s*sinh)\b|\b(\d{1,2})\s*(?:wc|toilet)\b", re.IGNORECASE)
_AMENITIES = ("thang máy", "elevator", "ban công", "ban cong", "gara", "garage", "bãi đỗ", "cho de", "hồ bơi", "ho boi", "bảo vệ", "bao ve")


class EmbeddingProvider(Protocol):
    """Provider boundary for future offline embedding services.

    Implementations must batch requests, apply their own retry/timeout/rate-limit
    policy and return one vector per input. They must never be called directly by
    the HTTP request layer for a remote provider.
    """

    name: str

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


class LocalHashEmbeddingProvider:
    """Small deterministic text vectorizer used when no remote provider exists."""

    name = "local_hashing_v1"

    def __init__(self, dimensions: int = TEXT_EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self._vectorizer = HashingVectorizer(
            n_features=dimensions,
            alternate_sign=False,
            norm="l2",
            lowercase=True,
            ngram_range=(1, 2),
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        matrix = self._vectorizer.transform(texts)
        return matrix.toarray().astype(float).tolist()


def normalize_text(value: Any) -> str:
    return _VECTOR_PATTERN.sub(" ", str(value or "")).strip()


def build_listing_text(record: Mapping[str, Any]) -> str:
    """Build the only text document used for extraction/embedding from source text."""
    values = (
        record.get("title"),
        record.get("description"),
        record.get("address"),
        record.get("property_type"),
        record.get("legal"),
        record.get("furniture"),
    )
    return " | ".join(part for value in values if (part := normalize_text(value)))


def text_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _first_number(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if not match:
        return None
    return int(next(value for value in match.groups() if value is not None))


def extract_structured_text_features(text: str) -> dict[str, Any]:
    """Extract only explicitly present attributes; unknown values stay ``None``."""
    lowered = normalize_text(text).lower()
    direction = None
    direction_match = _DIRECTION_PATTERN.search(lowered)
    if direction_match:
        direction = direction_match.group(1).replace(" ", "_")
    furnishing = "full" if any(item in lowered for item in ("nội thất đầy đủ", "noi that day du", "full nội thất")) else "basic" if any(item in lowered for item in ("nội thất cơ bản", "noi that co ban")) else None
    legal = "redbook" if any(item in lowered for item in ("sổ đỏ", "so do")) else "pinkbook" if any(item in lowered for item in ("sổ hồng", "so hong")) else None
    return {
        "extracted_bedrooms": _first_number(_BEDROOM_PATTERN, lowered),
        "extracted_bathrooms": _first_number(_BATHROOM_PATTERN, lowered),
        "extracted_direction": direction,
        "extracted_furnishing": furnishing,
        "extracted_legal_status": legal,
        "extracted_amenity_count": sum(term in lowered for term in _AMENITIES),
    }


def enrich_text_record(record: Mapping[str, Any], provider: EmbeddingProvider | None = None) -> dict[str, Any]:
    """Produce deterministic structured fields and an embedding for one record."""
    text = build_listing_text(record)
    provider = provider or LocalHashEmbeddingProvider()
    enriched = extract_structured_text_features(text)
    enriched.update(
        {
            "text_content_hash": text_content_hash(text),
            "text_embedding": provider.embed_batch([text])[0],
            "text_embedding_provider": provider.name,
            "text_embedding_dimension": TEXT_EMBEDDING_DIMENSIONS,
        }
    )
    return enriched


class SQLiteTextEmbeddingCache:
    """Content-addressed cache for offline preprocessing jobs."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("TEXT_EMBEDDING_CACHE_PATH", "runtime/text_embedding_cache.sqlite"))

    def enrich_many(self, records: Iterable[Mapping[str, Any]], provider: EmbeddingProvider | None = None) -> list[dict[str, Any]]:
        provider = provider or LocalHashEmbeddingProvider()
        items = list(records)
        if not items:
            return []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS text_embedding_cache (content_hash TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            results: list[dict[str, Any] | None] = [None] * len(items)
            misses: list[tuple[int, str, str]] = []
            for index, record in enumerate(items):
                text = build_listing_text(record)
                content_hash = text_content_hash(text)
                row = connection.execute(
                    "SELECT payload FROM text_embedding_cache WHERE content_hash = ?", (content_hash,)
                ).fetchone()
                if row:
                    results[index] = json.loads(row[0])
                else:
                    misses.append((index, text, content_hash))
            if misses:
                vectors = provider.embed_batch([text for _, text, _ in misses])
                for (index, text, content_hash), vector in zip(misses, vectors):
                    payload = extract_structured_text_features(text)
                    payload.update(
                        {
                            "text_content_hash": content_hash,
                            "text_embedding": vector,
                            "text_embedding_provider": provider.name,
                            "text_embedding_dimension": len(vector),
                        }
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO text_embedding_cache (content_hash, payload) VALUES (?, ?)",
                        (content_hash, json.dumps(payload, ensure_ascii=False)),
                    )
                    results[index] = payload
        return [result for result in results if result is not None]
