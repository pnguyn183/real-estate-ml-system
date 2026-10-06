"""Bounded model failover shared by extraction and feedback control.

Only configured external providers are used. A call visits each eligible
provider once, dividing its deadline so a failed primary leaves time for a
fallback. Provider cooldowns are independent and never sleep a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import threading
import time
from typing import Callable, Iterable, Mapping

from agents.providers import AIExtractionProvider, OpenAICompatibleProvider, ProviderError


@dataclass(frozen=True)
class RoutedResponse:
    content: str
    provider: str
    model: str
    attempts: int


@dataclass
class _Route:
    provider: AIExtractionProvider
    failures: int = 0
    open_until: float = 0.0
    probing: bool = False


class ModelRouter:
    name = "model_router"
    MIN_ATTEMPT_SECONDS = .1
    FAILOVER_CODES = frozenset({
        "provider_quota_exhausted", "provider_rate_limited", "provider_timeout",
        "provider_unavailable", "provider_connection", "provider_auth",
    })

    def __init__(self, providers: Iterable[AIExtractionProvider], *, cooldown_seconds: float = 30,
                 quota_cooldown_seconds: float = 3600,
                 clock: Callable[[], float] = time.monotonic):
        for name, value in (("cooldown_seconds", cooldown_seconds),
                            ("quota_cooldown_seconds", quota_cooldown_seconds)):
            if isinstance(value, bool) or not math.isfinite(value) or not 1 <= value <= 86400:
                raise ValueError(f"Invalid model router configuration: {name}")
        self._routes = [_Route(provider) for provider in providers if provider.enabled]
        self.enabled = bool(self._routes)
        self.model = self._routes[0].provider.model if self._routes else ""
        self.cooldown_seconds = cooldown_seconds
        self.quota_cooldown_seconds = quota_cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()

    def _available(self, route: _Route, now: float) -> bool:
        return now >= route.open_until and not route.probing

    def extract(self, messages: list[dict[str, str]], timeout: float) -> str:
        return self.extract_result(messages, timeout).content

    def extract_result(self, messages: list[dict[str, str]], timeout: float,
                       *, before_attempt: Callable[[], None] | None = None) -> RoutedResponse:
        """Return content with the actual responding provider's provenance.

        ``before_attempt`` supports the caller's local request budget. Its
        failure is propagated without opening any provider circuit. Time spent
        on this hook counts towards the shared deadline.
        """
        if not self.enabled:
            raise ProviderError("disabled")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ProviderError("time_budget_exceeded")
        deadline = self._clock() + timeout
        attempts = 0
        last_error = None
        insufficient_time = False
        for index, route in enumerate(self._routes):
            with self._lock:
                now = self._clock()
                if not self._available(route, now):
                    continue
                remaining = deadline - now
                if remaining < self.MIN_ATTEMPT_SECONDS:
                    insufficient_time = True
                    break
                # One half-open probe per provider; healthy providers retain
                # caller-controlled concurrency.
                probe = bool(route.failures)
                if probe:
                    route.probing = True
                other_available = sum(self._available(other, now) for other in self._routes[index + 1:])
            attempted = False
            try:
                if before_attempt is not None:
                    before_attempt()
                remaining = deadline - self._clock()
                if remaining < self.MIN_ATTEMPT_SECONDS:
                    raise ProviderError("time_budget_exceeded", attempts=attempts)
                allocation = remaining / (1 + other_available)
                attempts += 1
                attempted = True
                content = route.provider.extract(messages, allocation)
                if self._clock() > deadline:
                    raise ProviderError("provider_timeout", retryable=True)
            except ProviderError as exc:
                if not attempted:
                    exc.attempts = attempts
                    # A local budget can stop the next attempt. Preserve the
                    # identity of the last provider actually contacted instead
                    # of attributing that failure to an uncalled fallback.
                    if last_error is not None:
                        exc.provider = last_error.provider
                        exc.model = last_error.model
                    raise
                # Provenance belongs to this call, never shared mutable state.
                exc.provider = route.provider.name
                exc.model = route.provider.model
                exc.attempts = attempts
                last_error = exc
                with self._lock:
                    route.failures += 1
                    cooldown = (self.quota_cooldown_seconds
                                if exc.code in {"provider_quota_exhausted", "provider_auth"}
                                else min(self.quota_cooldown_seconds,
                                         self.cooldown_seconds * 2 ** min(route.failures - 1, 8)))
                    if exc.retry_after is not None and math.isfinite(exc.retry_after):
                        cooldown = max(cooldown, min(86400, exc.retry_after))
                    route.open_until = self._clock() + cooldown
                if exc.code not in self.FAILOVER_CODES:
                    raise
            else:
                with self._lock:
                    route.failures = 0
                    route.open_until = 0
                return RoutedResponse(content, route.provider.name, route.provider.model, attempts)
            finally:
                if probe:
                    with self._lock:
                        route.probing = False
        if last_error is not None:
            # The router already tried all eligible routes. The caller should
            # resume on its next cycle, not retry through another tight loop.
            last_error.retryable = False
            raise last_error
        if insufficient_time or self._clock() >= deadline or timeout < self.MIN_ATTEMPT_SECONDS:
            raise ProviderError("time_budget_exceeded", attempts=attempts)
        with self._lock:
            wait = min(max(0, route.open_until - self._clock()) for route in self._routes)
        raise ProviderError("provider_cooldown", retry_after=wait, attempts=attempts)


SUPPORTED_PROVIDERS = frozenset({"openai_compatible", "openai-compatible", "openai", "groq", "gemini"})
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def build_router_from_env(prefix: str = "LLM", *, fallback_to_llm: bool = False,
                          environ: Mapping[str, str] | None = None) -> ModelRouter:
    """Build primary + optional fallback without network or environment writes.

    A controller may inherit the entire LLM credential group when its own
    PROVIDER/BASE_URL/MODEL/API_KEY are all empty. Partially configured groups are rejected
    instead of mixing a new endpoint with an inherited API key. A fallback must
    be configured explicitly; no paid or 'free' model is silently selected.
    """
    env = os.environ if environ is None else environ
    providers: list[AIExtractionProvider] = []
    identities = set()
    for suffix in ("", "_FALLBACK"):
        group = prefix + suffix
        own_values = [env.get(group + "_" + key, "").strip() for key in ("BASE_URL", "MODEL", "API_KEY")]
        own_provider = env.get(group + "_PROVIDER", "").strip().lower()
        if fallback_to_llm and prefix != "LLM" and not any(own_values) and not own_provider:
            group = "LLM" + suffix
        values = [env.get(group + "_" + key, "").strip() for key in ("BASE_URL", "MODEL", "API_KEY")]
        # Match the existing Compose Gemini configuration. A standalone Gemini
        # credential may only fill a primary on that exact known endpoint.
        if (group == "LLM" and values[0].rstrip("/") in {"", GEMINI_BASE_URL}
                and (values[0] or not values[2])
                and env.get("LLM_PROVIDER", "").strip().lower() not in {"groq", "openai"}):
            if env.get("GEMINI_API_KEY", "").strip():
                values[0] = values[0] or GEMINI_BASE_URL
                values[2] = values[2] or env["GEMINI_API_KEY"].strip()
        default_name = "openai_compatible" if suffix or prefix != "LLM" else "disabled"
        name = env.get(group + "_PROVIDER", default_name).strip().lower() or default_name
        if name == "disabled":
            continue
        if not any(values) and suffix and not env.get(group + "_PROVIDER", "").strip():
            continue
        if name not in SUPPORTED_PROVIDERS or not all(values):
            raise ValueError(f"Invalid model router configuration: {group}_PROVIDER/BASE_URL/MODEL/API_KEY")
        base_url, model, key = values
        provider = OpenAICompatibleProvider(base_url, model, key)
        if not provider.enabled:
            raise ValueError(f"Invalid model router configuration: {group}_BASE_URL")
        # Names are a bounded configured alias. Keys and arbitrary URL fragments
        # are never exposed through response/error metadata.
        provider.name = name
        identity = (provider.base_url, provider.model, key)
        if identity not in identities:
            identities.add(identity)
            providers.append(provider)

    def duration(setting: str, default: float) -> float:
        try:
            raw = env.get(prefix + "_" + setting, "").strip()
            if not raw and fallback_to_llm:
                raw = env.get("LLM_" + setting, "").strip()
            return float(raw) if raw else default
        except ValueError:
            raise ValueError(f"Invalid model router configuration: {prefix}_{setting}") from None

    return ModelRouter(providers, cooldown_seconds=duration("COOLDOWN_SECONDS", 30),
                       quota_cooldown_seconds=duration("QUOTA_COOLDOWN_SECONDS", 3600))
