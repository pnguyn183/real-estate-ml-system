"""Bounded, robots-aware HTTP access; never attempts to evade access controls."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import re
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests


class ScraperPolicyError(RuntimeError):
    """Stop this scheduled attempt (exit 20); operator/source action is needed."""


class ScraperFetchError(RuntimeError):
    """A bounded transient request failed; a later scheduled run may retry."""


class ScraperMissingError(ScraperFetchError):
    """The requested page is gone; another listing can still be collected."""


class ScraperRateLimitError(ScraperFetchError):
    """Stop this source for this run instead of requesting another detail."""


class PoliteHTTPClient:
    def __init__(self, session, config, origin: str):
        self.session = session
        self.config = config
        self.origin = origin
        self.rules = None
        self.minimum_interval = 0.0
        self.last_request = None
        self.not_before = 0.0

    def _wait(self):
        now = time.monotonic()
        gap = max(
            random.uniform(self.config.delay_min_seconds, self.config.delay_max_seconds),
            self.config.request_delay_seconds, self.config.detail_delay_seconds,
            self.minimum_interval,
        )
        deadline = max(self.not_before, self.last_request + gap if self.last_request is not None else now)
        if deadline > now:
            time.sleep(deadline - now)
        self.last_request = time.monotonic()

    def _retry_wait(self, header: str | None, attempt: int) -> float:
        delay = self.config.retry_backoff_seconds * (2 ** attempt)
        if header:
            try:
                if header.strip().isdigit():
                    delay = float(header.strip())
                else:
                    date = parsedate_to_datetime(header)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    delay = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
        if delay > self.config.max_retry_wait_seconds:
            # Do not retry earlier than requested merely to fit our run budget.
            raise ScraperRateLimitError("retry_after_exceeds_run_budget")
        return delay

    def _request(self, url: str, *, robots: bool = False) -> str:
        for attempt in range(self.config.max_retries):
            self._wait()
            retry_header = None
            try:
                with self.session.get(
                    url, timeout=self.config.timeout_seconds,
                    allow_redirects=False, stream=True,
                ) as response:
                    status = response.status_code
                    if status in {401, 403} or response.headers.get("cf-mitigated", "").lower() == "challenge":
                        raise ScraperPolicyError(f"source_access_denied_http_{status}")
                    if robots and status in {404, 410}:
                        return ""
                    if status in {404, 410}:
                        raise ScraperMissingError(f"source_http_{status}_missing")
                    if status in {408, 429, 500, 502, 503, 504}:
                        retry_header = response.headers.get("Retry-After")
                        failure = f"source_http_{status}"
                    elif status != 200:
                        raise ScraperPolicyError(f"source_http_{status}_not_followed_or_retried")
                    else:
                        content_type = response.headers.get("Content-Type", "").lower()
                        allowed_types = ("text/plain",) if robots else ("text/html", "application/xhtml+xml")
                        if not any(value in content_type for value in allowed_types):
                            raise ScraperPolicyError("unexpected_source_content_type")
                        body = bytearray()
                        for chunk in response.iter_content(16384):
                            body.extend(chunk)
                            if len(body) > self.config.max_response_bytes:
                                raise ScraperFetchError("source_response_too_large")
                            if time.monotonic() - self.last_request > self.config.timeout_seconds:
                                raise ScraperFetchError("source_response_deadline")
                        text = body.decode("utf-8-sig", errors="strict")
                        if re.search(r"<title[^>]*>\s*(?:just a moment|access denied|captcha|verify.{0,30}human)", text, re.I):
                            raise ScraperPolicyError("source_access_challenge")
                        if robots and re.search(r"<\s*(?:html|!doctype)", text, re.I):
                            raise ScraperPolicyError("unexpected_robots_html")
                        return text
            except (requests.Timeout, requests.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ContentDecodingError) as error:
                failure = "source_transport_" + type(error).__name__
            except (requests.RequestException, UnicodeError) as error:
                raise ScraperFetchError("source_response_" + type(error).__name__) from None
            if attempt + 1 >= self.config.max_retries:
                if failure == "source_http_429":
                    raise ScraperRateLimitError("source_http_429_attempts_exhausted")
                if retry_header:
                    # Exhausting retries is not permission to ignore a server's
                    # cooldown by immediately requesting the next listing.
                    raise ScraperRateLimitError(f"{failure}_retry_after")
                raise ScraperFetchError(f"{failure}_attempts_exhausted")
            self.not_before = time.monotonic() + self._retry_wait(retry_header, attempt)
        raise ScraperFetchError("no_request_attempts")

    def _load_robots(self):
        content = self._request(self.origin + "/robots.txt", robots=True)
        rules = RobotFileParser()
        rules.parse(content.splitlines())
        delay = rules.crawl_delay(self.config.user_agent)
        rate = rules.request_rate(self.config.user_agent)
        if rate and (rate.requests <= 0 or rate.seconds <= 0):
            raise ScraperPolicyError("invalid_robots_request_rate")
        minimum_interval = max(float(delay or 0), rate.seconds / rate.requests if rate else 0)
        if minimum_interval > self.config.max_retry_wait_seconds:
            raise ScraperPolicyError("robots_delay_exceeds_run_budget")
        # Cache only a fully validated policy. Failed loading must not let a
        # later call reuse permissive rules while ignoring its pacing limit.
        self.minimum_interval = minimum_interval
        self.rules = rules

    def get_html(self, url: str) -> str:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != urlsplit(self.origin).netloc
                or parts.username or parts.password or parts.fragment):
            raise ScraperPolicyError("unexpected_source_origin")
        if self.rules is None:
            self._load_robots()
        if not self.rules.can_fetch(self.config.user_agent, url):
            raise ScraperPolicyError("robots_disallowed")
        return self._request(url)
