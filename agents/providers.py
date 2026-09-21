"""External, OpenAI-compatible LLM transport. No provider is contacted on import."""

from __future__ import annotations

import ipaddress
import json
import time
from typing import Protocol
from urllib.parse import urlsplit

import requests


class ProviderError(Exception):
    """Bounded machine-readable failure; never includes response bodies or keys."""

    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class AIExtractionProvider(Protocol):
    name: str
    model: str
    enabled: bool

    def extract(self, messages: list[dict[str, str]], timeout: float) -> str: ...


class DisabledProvider:
    name = "disabled"
    model = ""
    enabled = False

    def extract(self, messages: list[dict[str, str]], timeout: float) -> str:
        raise ProviderError("disabled")


def external_base_url(value: str) -> bool:
    """Only configured external HTTPS endpoints; credentials never belong in URLs."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return False
        if parsed.query or parsed.fragment or host in {"localhost", "host.docker.internal"}:
            return False
        if host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host
    except ValueError:
        return False


class OpenAICompatibleProvider:
    """Chat-completions JSON mode, usable with configured external providers.

    API key, URL and model must be supplied by the operator. This adapter neither
    selects a model nor downloads or runs a local model.
    """

    name = "openai_compatible"
    MAX_RESPONSE_BYTES = 65_536

    def __init__(self, base_url: str, model: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self._api_key = api_key.strip()
        self.enabled = bool(external_base_url(base_url) and self.model and self._api_key)

    def extract(self, messages: list[dict[str, str]], timeout: float) -> str:
        if not self.enabled:
            raise ProviderError("disabled")
        deadline = time.monotonic() + timeout
        response = None
        try:
            # Splitting connect/read budgets bounds normal timeout retries; the
            # streamed body also checks the total deadline and maximum size.
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                    "max_tokens": 1600,
                    "stream": False,
                },
                timeout=(min(2.0, timeout / 3), max(0.1, timeout - min(2.0, timeout / 3))),
                allow_redirects=False,
                stream=True,
            )
            if response.status_code == 429:
                raise ProviderError("provider_rate_limited", retryable=True)
            if response.status_code >= 500:
                raise ProviderError("provider_unavailable", retryable=True)
            if response.status_code in (401, 403):
                raise ProviderError("provider_auth")
            if response.status_code != 200:
                raise ProviderError("provider_request_rejected")
            content_bytes = bytearray()
            # Yield each received byte so a slow trickle cannot keep filling a
            # large chunk indefinitely without checking our wall-clock budget.
            # The response is capped at 64 KiB; this is not a bulk-data stream.
            for chunk in response.iter_content(chunk_size=1):
                if time.monotonic() > deadline:
                    raise ProviderError("provider_timeout", retryable=True)
                content_bytes.extend(chunk)
                if len(content_bytes) > self.MAX_RESPONSE_BYTES:
                    raise ProviderError("provider_response_too_large")
            body = json.loads(content_bytes)
            choice = body["choices"][0]
            if not isinstance(choice, dict):
                raise ProviderError("provider_invalid_response")
            if choice.get("finish_reason") not in (None, "stop"):
                raise ProviderError("provider_incomplete_response")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderError("provider_invalid_response")
            return content
        except ProviderError:
            raise
        except requests.Timeout:
            raise ProviderError("provider_timeout", retryable=True) from None
        except requests.RequestException:
            raise ProviderError("provider_connection", retryable=True) from None
        except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
            raise ProviderError("provider_invalid_response") from None
        finally:
            if response is not None:
                response.close()
