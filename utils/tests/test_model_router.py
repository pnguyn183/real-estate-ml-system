"""Offline model routing, deadlines, credential scoping, and extraction failover."""

import json
import threading
from unittest.mock import Mock

import pytest

from agents.extraction import ExtractionConfig, ExtractionService
from agents.model_router import GEMINI_BASE_URL, ModelRouter, build_router_from_env
from agents.provider_audit import capture
from agents.providers import OpenAICompatibleProvider, ProviderError


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class FakeProvider:
    enabled = True

    def __init__(self, name, *outputs):
        self.name = name
        self.model = name + "-model"
        self.outputs = list(outputs)
        self.calls = []

    def extract(self, messages, timeout):
        self.calls.append(timeout)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output(timeout) if callable(output) else output


def test_quota_failover_records_actual_model_and_waits_before_primary_probe():
    clock = Clock()
    primary = FakeProvider("primary", ProviderError("provider_quota_exhausted"), "recovered")
    fallback = FakeProvider("fallback", "fallback-result", "still-fallback")
    router = ModelRouter([primary, fallback], clock=clock, quota_cooldown_seconds=3600)
    result = router.extract_result([], 10)
    assert (result.content, result.provider, result.model, result.attempts) == (
        "fallback-result", "fallback", "fallback-model", 2)
    assert primary.calls == [5]
    assert router.extract([], 10) == "still-fallback"
    assert len(primary.calls) == 1
    clock.now = 3600
    assert router.extract_result([], 10).provider == "primary"
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 2


@pytest.mark.parametrize("code", ["provider_rate_limited", "provider_timeout", "provider_unavailable",
                                   "provider_connection", "provider_auth"])
def test_failover_supported_failures(code):
    router = ModelRouter([FakeProvider("primary", ProviderError(code)), FakeProvider("fallback", "ok")])
    assert router.extract_result([], 4).provider == "fallback"


def test_request_or_schema_error_does_not_hide_behind_fallback():
    fallback = FakeProvider("fallback", "ok")
    router = ModelRouter([FakeProvider("primary", ProviderError("provider_request_rejected")), fallback])
    with pytest.raises(ProviderError, match="provider_request_rejected"):
        router.extract([], 4)
    assert fallback.calls == []


def test_timeout_allocation_reserves_fallback_time_within_overall_deadline():
    clock = Clock()

    def time_out(timeout):
        clock.now += timeout
        raise ProviderError("provider_timeout", retryable=True)

    def succeed(timeout):
        clock.now += timeout * .9
        return "ok"

    primary = FakeProvider("primary", time_out)
    fallback = FakeProvider("fallback", succeed)
    router = ModelRouter([primary, fallback], clock=clock)
    assert router.extract([], 8) == "ok"
    assert primary.calls == fallback.calls == [4]
    assert clock.now < 8


def test_late_result_is_rejected_and_no_new_call_starts_after_deadline():
    clock = Clock()

    def overrun(timeout):
        clock.now += 20
        return "too late"

    fallback = FakeProvider("fallback", "unused")
    router = ModelRouter([FakeProvider("primary", overrun), fallback], clock=clock)
    with pytest.raises(ProviderError, match="provider_timeout"):
        router.extract([], 4)
    assert fallback.calls == []


def test_all_cooling_returns_immediately_and_recovered_fallback_is_not_starved():
    clock = Clock()
    primary = FakeProvider("primary", ProviderError("provider_quota_exhausted"))
    fallback = FakeProvider("fallback", ProviderError("provider_unavailable"), "ok")
    router = ModelRouter([primary, fallback], clock=clock, cooldown_seconds=10)
    with pytest.raises(ProviderError) as first:
        router.extract([], 4)
    assert first.value.retryable is False
    assert first.value.attempts == 2
    assert first.value.provider == "fallback"
    for _ in range(4):
        with pytest.raises(ProviderError, match="provider_cooldown") as cooling:
            router.extract([], 4)
        assert cooling.value.retry_after == 10
    assert len(primary.calls) == len(fallback.calls) == 1
    clock.now = 10
    assert router.extract([], 4) == "ok"
    assert len(primary.calls) == 1


def test_retry_after_takes_precedence_over_short_cooldown():
    clock = Clock()
    primary = FakeProvider("primary", ProviderError("provider_rate_limited", retry_after=120), "ok")
    router = ModelRouter([primary], clock=clock, cooldown_seconds=5)
    with pytest.raises(ProviderError):
        router.extract([], 4)
    clock.now = 119
    with pytest.raises(ProviderError, match="provider_cooldown") as caught:
        router.extract([], 4)
    assert caught.value.retry_after == 1
    clock.now = 120
    assert router.extract([], 4) == "ok"


def test_only_one_concurrent_half_open_probe():
    clock = Clock()
    started, release = threading.Event(), threading.Event()

    def probe(timeout):
        started.set()
        assert release.wait(2)
        return "ok"

    primary = FakeProvider("primary", ProviderError("provider_unavailable"), probe)
    router = ModelRouter([primary], clock=clock, cooldown_seconds=5)
    with pytest.raises(ProviderError):
        router.extract([], 4)
    clock.now = 5
    results = []
    thread = threading.Thread(target=lambda: results.append(router.extract([], 4)))
    thread.start()
    try:
        assert started.wait(2)
        with pytest.raises(ProviderError, match="provider_cooldown"):
            router.extract([], 4)
        assert len(primary.calls) == 2
    finally:
        release.set()
        thread.join(2)
    assert results == ["ok"]


def test_local_attempt_budget_stops_failover_without_penalizing_fallback():
    primary = FakeProvider("primary", ProviderError("provider_unavailable"))
    fallback = FakeProvider("fallback", "ok")
    router = ModelRouter([primary, fallback])
    hook = Mock(side_effect=[None, ProviderError("local_rate_limit")])
    with pytest.raises(ProviderError, match="local_rate_limit") as caught:
        router.extract_result([], 4, before_attempt=hook)
    assert caught.value.attempts == 1
    assert caught.value.provider == "primary"
    assert caught.value.model == "primary-model"
    assert fallback.calls == []
    assert router.extract([], 4) == "ok"


def configuration():
    return {"LLM_PROVIDER": "openai_compatible", "LLM_BASE_URL": "https://primary.example/v1",
            "LLM_MODEL": "primary", "LLM_API_KEY": "primary-test-key",
            "LLM_FALLBACK_BASE_URL": "https://fallback.example/v1",
            "LLM_FALLBACK_MODEL": "fallback", "LLM_FALLBACK_API_KEY": "fallback-test-key"}


def test_factory_is_network_free_and_controller_inherits_whole_configuration(monkeypatch):
    post = Mock(side_effect=AssertionError("network must not run"))
    monkeypatch.setattr("agents.providers.requests.post", post)
    router = build_router_from_env("CONTROL_LLM", fallback_to_llm=True, environ=configuration())
    assert router.enabled
    assert [r.provider.model for r in router._routes] == ["primary", "fallback"]
    post.assert_not_called()


def test_partial_controller_config_cannot_send_inherited_key_to_new_endpoint():
    environment = configuration() | {"CONTROL_LLM_BASE_URL": "https://different.example/v1"}
    with pytest.raises(ValueError, match="CONTROL_LLM") as caught:
        build_router_from_env("CONTROL_LLM", fallback_to_llm=True, environ=environment)
    assert "primary-test-key" not in str(caught.value)


@pytest.mark.parametrize("setting", ["CONTROL_LLM_PROVIDER", "CONTROL_LLM_FALLBACK_PROVIDER"])
def test_explicit_controller_provider_requires_its_own_complete_group(setting):
    environment = configuration() | {setting: "groq"}
    with pytest.raises(ValueError, match="CONTROL_LLM"):
        build_router_from_env("CONTROL_LLM", fallback_to_llm=True, environ=environment)


def test_partial_controller_group_does_not_mix_in_global_gemini_key():
    environment = configuration() | {"CONTROL_LLM_MODEL": "test", "GEMINI_API_KEY": "gemini-test-key"}
    with pytest.raises(ValueError, match="CONTROL_LLM"):
        build_router_from_env("CONTROL_LLM", fallback_to_llm=True, environ=environment)


def test_gemini_key_default_matches_compose_without_cross_provider_key_reuse():
    environment = {"LLM_PROVIDER": "openai_compatible", "LLM_MODEL": "gemini-test",
                   "GEMINI_API_KEY": "gemini-test-key"}
    router = build_router_from_env(environ=environment)
    assert router._routes[0].provider.base_url == GEMINI_BASE_URL
    assert router._routes[0].provider._api_key == "gemini-test-key"
    with pytest.raises(ValueError):
        build_router_from_env(environ=environment | {"LLM_BASE_URL": "https://different.example/v1"})
    # An unrelated primary key must not be sent to the default Gemini endpoint
    # just because a separate Gemini fallback credential exists on the host.
    with pytest.raises(ValueError):
        build_router_from_env(environ=environment | {"LLM_API_KEY": "unrelated-provider-key"})


def test_factory_rejects_incomplete_fallback_and_deduplicates_identical_routes():
    environment = configuration()
    del environment["LLM_FALLBACK_API_KEY"]
    with pytest.raises(ValueError, match="LLM_FALLBACK"):
        build_router_from_env(environ=environment)
    environment = configuration()
    for field in ("BASE_URL", "MODEL", "API_KEY"):
        environment["LLM_FALLBACK_" + field] = environment["LLM_" + field]
    assert len(build_router_from_env(environ=environment)._routes) == 1


def test_factory_disabled_without_configuration_and_explicit_controller_opt_out():
    assert not build_router_from_env(environ={}).enabled
    environment = configuration() | {"CONTROL_LLM_PROVIDER": "disabled",
                                     "CONTROL_LLM_FALLBACK_PROVIDER": "disabled"}
    assert not build_router_from_env("CONTROL_LLM", fallback_to_llm=True, environ=environment).enabled


@pytest.mark.parametrize("key,value", [("LLM_COOLDOWN_SECONDS", "nan"),
                                      ("LLM_COOLDOWN_SECONDS", "bad"),
                                      ("LLM_QUOTA_COOLDOWN_SECONDS", "0")])
def test_invalid_router_durations_rejected(key, value):
    with pytest.raises(ValueError):
        build_router_from_env(environ=configuration() | {key: value})


def extraction_output():
    return json.dumps({"fields": {"area_m2": 80}, "confidence": .9, "evidence": {"area_m2": "80m2"}})


def test_extraction_records_actual_fallback_and_counts_each_external_attempt():
    primary = FakeProvider("primary", ProviderError("provider_quota_exhausted"))
    fallback = FakeProvider("fallback", extraction_output())
    service = ExtractionService(ModelRouter([primary, fallback]))
    result = service.extract({"description": "Area 80m2"})
    assert result["status"] == "success"
    assert result["attempts"] == 2
    assert (result["provider"], result["model"]) == ("fallback", "fallback-model")
    assert "ai_extraction_provider_calls_total 2.0" in service.metrics.export().decode()


def test_extraction_router_cooldown_cannot_trip_global_circuit_and_starve_recovery():
    clock = Clock()
    provider = FakeProvider("primary", ProviderError("provider_unavailable"), extraction_output())
    router = ModelRouter([provider], clock=clock, cooldown_seconds=5)
    service = ExtractionService(router, ExtractionConfig(circuit_failure_threshold=1))
    record = {"description": "Area 80m2"}
    assert service.extract(record)["error_code"] == "provider_unavailable"
    assert service.extract(record)["error_code"] == "provider_cooldown"
    clock.now = 5
    assert service.extract(record)["status"] == "success"


def test_extraction_local_rate_budget_counts_fallback_attempts():
    primary = FakeProvider("primary", ProviderError("provider_quota_exhausted"))
    fallback = FakeProvider("fallback", extraction_output())
    service = ExtractionService(ModelRouter([primary, fallback]), ExtractionConfig(rate_limit_per_minute=1))
    result = service.extract({"description": "Area 80m2"})
    assert result["error_code"] == "local_rate_limit"
    assert result["attempts"] == 1
    assert fallback.calls == []


@pytest.mark.parametrize("error,expected,retryable", [
    ({"code": "insufficient_quota", "message": "sensitive"}, "provider_quota_exhausted", False),
    ({"type": "billing_hard_limit_reached"}, "provider_quota_exhausted", False),
    ({"status": "RESOURCE_EXHAUSTED", "message": "quota per minute exceeded"}, "provider_rate_limited", True),
    ({"code": "rate_limit_exceeded"}, "provider_rate_limited", True),
])
def test_transport_classifies_structured_quota_without_exposing_body(monkeypatch, error, expected, retryable):
    reply = Mock(status_code=429, headers={"Retry-After": "120"})
    reply.iter_content.return_value = [json.dumps({"error": error}).encode()]
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=reply))
    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleProvider("https://api.example/v1", "test", "secret").extract([], 4)
    assert caught.value.code == expected
    assert caught.value.retryable is retryable
    assert caught.value.retry_after == 120
    assert "sensitive" not in str(caught.value)
    reply.close.assert_called_once()


def test_fallback_key_is_redacted_from_opt_in_audit_even_without_primary_env_key(monkeypatch):
    monkeypatch.setenv("AI_AUDIT_EVENT_IDS", "selected")
    secret = "fallback-test-only-key"
    reply = Mock(status_code=429, headers={})
    reply.iter_content.return_value = [json.dumps({"error": {"code": "insufficient_quota", "message": secret}}).encode()]
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=reply))
    collection = Mock()
    with capture({"ai_provider_audit": collection}, "selected", {}):
        with pytest.raises(ProviderError):
            OpenAICompatibleProvider("https://fallback.example/v1", "test", secret).extract([], 4)
    stages = [call.args[1]["$push"]["stages"] for call in collection.update_one.call_args_list]
    assert secret not in json.dumps(stages)
    assert "[REDACTED]" in json.dumps(stages)
