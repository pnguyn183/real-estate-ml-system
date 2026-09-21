"""Extraction-only Prometheus metrics with bounded label values."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class AIExtractionMetrics:
    def __init__(self, registry: CollectorRegistry | None = None):
        self.registry = registry if registry is not None else CollectorRegistry()
        self.requests = Counter("ai_extraction_requests_total", "Extraction requests received", registry=self.registry)
        self.success = Counter("ai_extraction_success_total", "Accepted extractions", registry=self.registry)
        self.failure = Counter("ai_extraction_failure_total", "Failed extractions", ["reason"], registry=self.registry)
        self.disabled = Counter("ai_extraction_disabled_total", "Requests skipped while disabled", registry=self.registry)
        self.duration = Histogram("ai_extraction_duration_seconds", "End-to-end extraction duration", buckets=(.1, .5, 1, 2, 5, 10, 20, 30), registry=self.registry)
        self.confidence = Histogram("ai_extraction_confidence", "Validated provider confidence, not calibrated accuracy", buckets=(0, .25, .5, .75, .9, 1), registry=self.registry)
        self.confidence_threshold = Gauge("ai_extraction_confidence_threshold", "Configured minimum provider confidence; not a calibrated accuracy target", registry=self.registry)
        self.retries = Counter("ai_extraction_retries_total", "Additional provider attempts", registry=self.registry)
        self.calls = Counter("ai_extraction_provider_calls_total", "External LLM requests attempted", registry=self.registry)
        self.inflight = Gauge("ai_extraction_inflight", "Concurrent extraction operations", registry=self.registry)
        self.circuit_open = Gauge("ai_extraction_circuit_open", "One when provider circuit breaker is open", registry=self.registry)
        self.validation_success = Counter("ai_validation_success_total", "Extraction schema, confidence and evidence checks accepted; processor validates business rules separately", registry=self.registry)
        self.validation_failure = Counter("ai_validation_failure_total", "Extraction schema, confidence or evidence check failed", ["reason"], registry=self.registry)
        self.rate_limits = Counter("ai_rate_limit_events_total", "Provider or local request limit reached", ["source"], registry=self.registry)
        self.fallback = Counter("ai_extraction_fallback_total", "Extraction requests returned for pipeline failure/review handling", ["reason"], registry=self.registry)

    def export(self) -> bytes:
        return generate_latest(self.registry)
