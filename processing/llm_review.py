"""Strict, optional LLM review boundary for already suspicious listings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol


VALID_CLASSIFICATIONS = {"normal", "suspicious", "invalid"}
VALID_ACTIONS = {"accept", "review", "reject"}


class LLMProvider(Protocol):
    """A provider must return JSON only; transport/retries stay provider-owned."""

    def review_listing(self, payload: Mapping[str, Any]) -> str | Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class LLMReview:
    classification: str
    confidence: float
    reason: str
    suggested_action: str
    suggested_adjustment: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_llm_review_response(response: str | Mapping[str, Any]) -> LLMReview:
    """Validate a provider response before any pipeline code can use it."""
    payload = json.loads(response) if isinstance(response, str) else dict(response)
    required = {"classification", "confidence", "reason", "suggested_action", "suggested_adjustment"}
    if set(payload) != required:
        raise ValueError("LLM response must contain exactly the configured review schema")
    classification = str(payload["classification"]).lower()
    action = str(payload["suggested_action"]).lower()
    confidence = payload["confidence"]
    adjustment = payload["suggested_adjustment"]
    if classification not in VALID_CLASSIFICATIONS or action not in VALID_ACTIONS:
        raise ValueError("LLM response has an unsupported classification or action")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("LLM confidence must be a number between zero and one")
    if not isinstance(payload["reason"], str) or not payload["reason"].strip():
        raise ValueError("LLM reason must be non-empty text")
    if adjustment is not None and (isinstance(adjustment, bool) or not isinstance(adjustment, (int, float))):
        raise ValueError("LLM suggested_adjustment must be numeric or null")
    return LLMReview(classification, float(confidence), payload["reason"].strip(), action, adjustment)


class OptionalLLMReviewer:
    """Fail-open reviewer. It never changes raw price or deterministic status."""

    def __init__(self, provider: LLMProvider | None = None, enabled: bool | None = None) -> None:
        self.provider = provider
        self.enabled = enabled if enabled is not None else os.environ.get("LLM_REVIEW_ENABLED", "false").lower() in {"1", "true", "yes"}

    def review(self, record: Mapping[str, Any]) -> dict[str, Any]:
        if record.get("listing_review_status") != "SUSPICIOUS":
            return {"llm_review_status": "SKIPPED", "llm_review": None}
        if not self.enabled or self.provider is None:
            return {"llm_review_status": "DISABLED", "llm_review": None}
        try:
            review = parse_llm_review_response(self.provider.review_listing(record))
            return {"llm_review_status": "AVAILABLE", "llm_review": review.as_dict()}
        except Exception as exc:
            # The deterministic suspicious status survives any provider outage.
            return {"llm_review_status": "UNAVAILABLE", "llm_review": None, "llm_review_error": type(exc).__name__}
