"""Extraction contract/transport tests. Mocks do not represent a live LLM run."""

import json
import threading
from unittest.mock import Mock

import pytest
import requests

from agents.extraction import ExtractionConfig, ExtractionService, localized_decimal, source_text
from agents.providers import DisabledProvider, OpenAICompatibleProvider, ProviderError, external_base_url


RECORD = {
    "url": "https://synthetic.invalid/test/1",
    "description": "Căn hộ 80m2, 2 phòng ngủ, tổng giá 8 tỷ.",
    "is_synthetic": True,
    "run_id": "test",
}


def output(**changes):
    value = {
        "fields": {"price_vnd": 8_000_000_000, "area_m2": 80, "bedroom_count": 2, "property_type": "apartment"},
        "confidence": .9,
        "evidence": {"price_vnd": "8 tỷ", "area_m2": "80m2", "bedroom_count": "2 phòng ngủ", "property_type": "Căn hộ"},
    }
    value.update(changes)
    return json.dumps(value)


def provider(result=None):
    stub = Mock()
    stub.enabled = True
    stub.name = "test_transport"
    stub.model = "test_schema"
    stub.extract.return_value = result if result is not None else output()
    return stub


def test_disabled_service_never_calls_network(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "false")
    request = Mock(side_effect=AssertionError("network must not run"))
    monkeypatch.setattr("agents.providers.requests.post", request)
    service = ExtractionService.from_env()
    result = service.extract(RECORD)
    assert result["status"] == "disabled"
    assert result["attempts"] == 0
    assert result["record"] is None
    request.assert_not_called()


def test_explicitly_enabled_service_rejects_missing_provider_configuration(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    with pytest.raises(ValueError, match="AI_ENABLED requires"):
        ExtractionService.from_env()


def test_success_adapts_raw_units_preserves_provenance_and_known_fields():
    original = {**RECORD, "area_text": "81 m2", "verified": 0, "province_slug": "ha-noi"}
    service = ExtractionService(provider())
    result = service.extract(original)
    assert result["status"] == "success"
    assert result["record"]["price_text"] == "8.0 tỷ"
    assert result["record"]["area_text"] == "81 m2"
    assert result["record"]["bedroom_text"] == "2"
    assert result["record"]["is_synthetic"] is True
    assert result["record"]["run_id"] == "test"
    assert result["record"]["verified"] == 0
    assert result["record"]["province_slug"] == "ha-noi"
    assert "price_text" not in original
    assert result["attempts"] == 1
    metrics = service.metrics.export().decode()
    assert "ai_extraction_success_total 1.0" in metrics
    assert "ai_extraction_inflight 0.0" in metrics


@pytest.mark.parametrize("fields", [
    {"area_m2": "80"}, {"price_vnd": True}, {"area_m2": float("nan")},
    {"price_vnd": float("inf")}, {"bedroom_count": 2.5}, {"latitude": 10.0},
    {"verified": 1}, {"area_m2": -1}, {"price_vnd": 501_000_000_000},
])
def test_strict_schema_rejects_bad_or_fabricated_fields(fields):
    stub = provider(output(fields=fields))
    result = ExtractionService(stub).extract(RECORD)
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_schema"
    assert result["attempts"] == 1


@pytest.mark.parametrize("content,reason", [
    ("not JSON", "invalid_schema"),
    (output(confidence=.6), "low_confidence"),
    (output(evidence={"area_m2": "invented text"}), "invalid_evidence"),
    (output(fields={"price_vnd": None, "area_m2": None}), "no_extracted_fields"),
])
def test_permanent_output_failures_are_not_retried(content, reason):
    stub = provider(content)
    result = ExtractionService(stub).extract(RECORD)
    assert result["error_code"] == reason
    assert result["attempts"] == 1
    assert result["record"] is None


def test_missing_fields_stay_absent_and_partial_is_explicit():
    stub = provider(output(fields={"area_m2": 80, "price_vnd": None}, evidence={"area_m2": "80m2"}))
    result = ExtractionService(stub).extract(RECORD)
    assert result["status"] == "success"
    assert result["record"]["area_text"] == "80.0 m2"
    assert "price_text" not in result["record"]


def test_retry_only_transient_and_respect_attempt_limit(monkeypatch):
    monkeypatch.setattr("agents.extraction.time.sleep", lambda value: None)
    stub = provider()
    stub.extract.side_effect = [ProviderError("provider_rate_limited", retryable=True), output()]
    service = ExtractionService(stub)
    assert service.extract(RECORD)["attempts"] == 2
    assert "ai_extraction_retries_total 1.0" in service.metrics.export().decode()
    stub.extract.side_effect = ProviderError("provider_auth")
    result = service.extract(RECORD)
    assert result["attempts"] == 1
    assert result["error_code"] == "provider_auth"


def test_circuit_opens_and_stops_external_calls():
    stub = provider()
    stub.extract.side_effect = ProviderError("provider_auth")
    service = ExtractionService(stub, ExtractionConfig(circuit_failure_threshold=1))
    assert service.extract(RECORD)["error_code"] == "provider_auth"
    assert service.extract(RECORD)["error_code"] == "circuit_open"
    assert stub.extract.call_count == 1


def test_local_rate_limiter_counts_attempts_not_success():
    stub = provider()
    service = ExtractionService(stub, ExtractionConfig(rate_limit_per_minute=1))
    assert service.extract(RECORD)["status"] == "success"
    assert service.extract(RECORD)["error_code"] == "local_rate_limit"
    assert stub.extract.call_count == 1


def test_concurrency_is_fail_fast_and_slots_released():
    stub = provider()
    started, release = threading.Event(), threading.Event()

    def wait_for_release(*args):
        started.set()
        assert release.wait(3)
        return output()

    stub.extract.side_effect = wait_for_release
    service = ExtractionService(stub, ExtractionConfig(max_concurrent=1))
    thread = threading.Thread(target=service.extract, args=(RECORD,))
    thread.start()
    assert started.wait(2)
    try:
        assert service.extract(RECORD)["error_code"] == "busy"
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert service.extract(RECORD)["status"] == "success"


def test_prompt_contains_only_allowlisted_source_not_ground_truth():
    stub = provider()
    payload = {**RECORD, "ground_truth": {"price_vnd": 123}, "api_key": "never-send-me"}
    ExtractionService(stub).extract(payload)
    messages = stub.extract.call_args.args[0]
    assert "untrusted data" in messages[0]["content"]
    assert "never-send-me" not in json.dumps(messages)
    assert "ground_truth" not in messages[1]["content"]


def test_limits_reject_oversize_without_provider_calls():
    stub = provider()
    assert ExtractionService(stub).extract({"description": "x" * 70_000})["error_code"] == "input_too_large"
    stub.extract.assert_not_called()


def response(status=200, body=None):
    result = Mock()
    result.status_code = status
    result.iter_content.return_value = [json.dumps(body or {"choices": [{"finish_reason": "stop", "message": {"content": output()}}]}).encode()]
    return result


def test_external_transport_genuinely_posts_json_request(monkeypatch):
    reply = response()
    post = Mock(return_value=reply)
    monkeypatch.setattr("agents.providers.requests.post", post)
    adapter = OpenAICompatibleProvider("https://api.example.com/v1", "operator-selected-model", "unit-test-key")
    assert adapter.extract([{"role": "user", "content": "test"}], 8) == output()
    assert post.call_args.args == ("https://api.example.com/v1/chat/completions",)
    assert post.call_args.kwargs["json"]["model"] == "operator-selected-model"
    assert post.call_args.kwargs["json"]["response_format"] == {"type": "json_object"}
    assert post.call_args.kwargs["allow_redirects"] is False
    reply.close.assert_called_once()


@pytest.mark.parametrize("status,code,retryable", [
    (429, "provider_rate_limited", True), (503, "provider_unavailable", True),
    (401, "provider_auth", False), (400, "provider_request_rejected", False),
    (302, "provider_request_rejected", False),
])
def test_provider_status_handling_never_exposes_body(monkeypatch, status, code, retryable):
    reply = response(status, {"error": "secret provider details"})
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=reply))
    with pytest.raises(ProviderError) as error:
        OpenAICompatibleProvider("https://api.example.com/v1", "test", "key").extract([], 8)
    assert error.value.code == code
    assert error.value.retryable is retryable
    assert "secret" not in str(error.value)
    reply.close.assert_called_once()


def test_provider_timeout_is_transient_and_sanitized(monkeypatch):
    monkeypatch.setattr("agents.providers.requests.post", Mock(side_effect=requests.Timeout("secret body")))
    with pytest.raises(ProviderError) as error:
        OpenAICompatibleProvider("https://api.example.com/v1", "test", "key").extract([], 8)
    assert error.value.code == "provider_timeout"
    assert error.value.retryable
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("url", ["http://api.example.com/v1", "https://localhost/v1", "https://127.0.0.1/v1", "https://192.168.1.1/v1", "https://host.docker.internal/v1", "https://key@api.example.com/v1", "https://api.example.com?key=secret"])
def test_local_or_credential_urls_are_not_external_provider_configuration(url):
    assert not external_base_url(url)


def test_decimal_adapter_disambiguates_existing_parser():
    assert localized_decimal(1.234) == "1.2340"
    assert localized_decimal(80.125) == "80.1250"


@pytest.mark.parametrize("name,value", [
    ("LLM_MAX_RETRIES", "999"), ("LLM_MAX_RETRIES", "1.5"),
    ("AI_MAX_CONCURRENT_REQUESTS", "0"), ("AI_MAX_CONCURRENT_REQUESTS", "2.5"),
    ("LLM_TIMEOUT_SECONDS", "nan"), ("LLM_TIMEOUT_SECONDS", "not-a-number"),
    ("AI_RATE_LIMIT_PER_MINUTE", "inf"), ("AI_TOTAL_BUDGET_SECONDS", "0"),
])
def test_invalid_environment_limits_fail_explicitly(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        ExtractionConfig.from_env()


def test_configuration_defaults_match_documented_safe_limits(monkeypatch):
    for name in ("LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES", "AI_MAX_CONCURRENT_REQUESTS",
                 "AI_RATE_LIMIT_PER_MINUTE", "AI_TOTAL_BUDGET_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    config = ExtractionConfig.from_env()
    assert config.timeout_seconds == 30
    assert config.max_retries == 3
    assert config.total_budget_seconds == 120
    assert config.max_concurrent == 1
    assert config.rate_limit_per_minute == 20


def test_spelled_out_raw_values_can_be_filled_without_replacing_known_values():
    original = {**RECORD, "price_text": "Khoảng tám tỷ", "area_text": "tám mươi mét vuông",
                "bedroom_text": "3 phòng ngủ", "description": "Căn hộ 80m2, giá 8 tỷ."}
    stub = provider(output(fields={"price_vnd": 8_000_000_000, "area_m2": 80, "bedroom_count": 2},
                           evidence={"price_vnd": "8 tỷ", "area_m2": "80m2"}))
    result = ExtractionService(stub).extract(original)
    assert result["status"] == "success"
    assert result["record"]["price_text"] == "8.0 tỷ"
    assert result["record"]["area_text"] == "80.0 m2"
    assert result["record"]["bedroom_text"] == "3 phòng ngủ"
    known = json.loads(stub.extract.call_args.args[0][1]["content"])["known_fields"]
    assert "price_text" not in known and "area_text" not in known
    assert known["bedroom_text"] == "3 phòng ngủ"
    assert original["price_text"] == "Khoảng tám tỷ"


@pytest.mark.parametrize("field,value,quote,expected", [
    ("district", "Bình Thạnh", "Quận Bình Thạnh", "binh-thanh"),
    ("district", "BINH THANH", "Bình Thạnh", "binh-thanh"),
    ("district", "Thủ Đức", "thành phố Thủ Đức", "thu-duc"),
    ("district", "Quận 1", "Quận 1", "quan-1"),
    ("province", "Hồ Chí Minh", "TP. HCM", "ho-chi-minh"),
    ("province", "TPHCM", "thành phố Hồ Chí Minh", "ho-chi-minh"),
    ("province", "Hà Nội", "Hanoi", "ha-noi"),
    ("address", "123 Đường A", "123 đường A, Bình Thạnh", "123 Đường A"),
])
def test_explicit_locations_are_grounded_and_adapted(field, value, quote, expected):
    record = {**RECORD, "description": f"Căn hộ tại {quote}."}
    stub = provider(output(fields={field: value}, evidence={field: quote}))
    result = ExtractionService(stub).extract(record)
    assert result["status"] == "success"
    key = field + "_slug" if field != "address" else field
    assert result["record"][key] == expected


@pytest.mark.parametrize("field,value,quote", [
    ("district", "Quận 1", "Bình Thạnh"),
    ("province", "Hồ Chí Minh", "Bình Thạnh"),
    ("province", "Hà Nội", "TPHCM"),
    ("address", "999 Đường B", "Bình Thạnh"),
])
def test_location_cannot_be_invented_using_an_unrelated_literal_quote(field, value, quote):
    stub = provider(output(fields={field: value}, evidence={field: quote}))
    result = ExtractionService(stub).extract({**RECORD, "description": f"Căn hộ tại {quote}."})
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_evidence"
    assert result["record"] is None
    assert stub.extract.call_count == 1


def test_semi_structured_aliases_keep_field_names_and_do_not_send_metadata():
    record = {"url": RECORD["url"], "price": "5 tỷ", "area": "80 m²", "bedrooms": 3,
              "bathrooms": 2, "phong_ngu": "ba", "phong_tam": "hai", "city": "Hà Nội",
              "api_key": "secret", "nested": {"description": "do not serialize"}}
    stub = provider(output(fields={"price_vnd": 5_000_000_000, "area_m2": 80, "bedroom_count": 3},
                           evidence={"price_vnd": "5 tỷ", "area_m2": "80 m²", "bedroom_count": "bedrooms: 3"}))
    result = ExtractionService(stub).extract(record)
    assert result["status"] == "success"
    text = json.loads(stub.extract.call_args.args[0][1]["content"])["source_text"]
    assert "phong_ngu: ba" in text and "phong_tam: hai" in text
    assert "city: Hà Nội" in text and "bathrooms: 2" in text
    assert "secret" not in text and "nested" not in text
    assert result["record"]["bedroom_text"] == "3"
    assert source_text({"bedrooms": True, "price": ["5 tỷ"]}) == ""


def test_retry_exhaustion_and_provider_limits_have_bounded_metrics(monkeypatch):
    monkeypatch.setattr("agents.extraction.time.sleep", lambda value: None)
    stub = provider()
    stub.extract.side_effect = ProviderError("provider_rate_limited", retryable=True)
    service = ExtractionService(stub, ExtractionConfig(max_retries=3))
    result = service.extract(RECORD)
    assert result["error_code"] == "provider_rate_limited"
    assert result["attempts"] == 4
    assert stub.extract.call_count == 4
    metrics = service.metrics.export().decode()
    assert 'ai_rate_limit_events_total{source="provider"} 4.0' in metrics
    assert 'ai_extraction_fallback_total{reason="provider_rate_limited"} 1.0' in metrics
    assert "ai_extraction_retries_total 3.0" in metrics


def test_local_limit_returns_recoverable_failure_without_unbounded_wait():
    stub = provider()
    service = ExtractionService(stub, ExtractionConfig(rate_limit_per_minute=1))
    assert service.extract(RECORD)["status"] == "success"
    result = service.extract(RECORD)
    assert result["error_code"] == "local_rate_limit"
    assert result["attempts"] == 0
    metrics = service.metrics.export().decode()
    assert 'ai_rate_limit_events_total{source="local"} 1.0' in metrics
    assert 'ai_extraction_fallback_total{reason="local_rate_limit"} 1.0' in metrics


@pytest.mark.parametrize("body", [
    {}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": {"area": 80}}}]},
    {"choices": [None]}, {"choices": [1]},
])
def test_transport_rejects_malformed_provider_envelopes(monkeypatch, body):
    reply = response()
    reply.iter_content.return_value = [json.dumps(body).encode()]
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=reply))
    with pytest.raises(ProviderError, match="provider_invalid_response"):
        OpenAICompatibleProvider("https://api.example.com/v1", "test", "key").extract([], 8)
    reply.close.assert_called_once()


def test_transport_rejects_invalid_json_and_closes_response(monkeypatch):
    reply = response()
    reply.iter_content.return_value = [b"not JSON"]
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=reply))
    with pytest.raises(ProviderError, match="provider_invalid_response"):
        OpenAICompatibleProvider("https://api.example.com/v1", "test", "key").extract([], 8)
    reply.close.assert_called_once()


def test_transport_checks_deadline_during_slow_response(monkeypatch):
    reply = response()
    reply.iter_content.return_value = [b"{", b"}"]
    monotonic = Mock(side_effect=[0, 1, 9])
    monkeypatch.setattr("agents.providers.time.monotonic", monotonic)
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=reply))
    with pytest.raises(ProviderError, match="provider_timeout") as error:
        OpenAICompatibleProvider("https://api.example.com/v1", "test", "key").extract([], 8)
    assert error.value.retryable
    reply.iter_content.assert_called_once_with(chunk_size=1)
    reply.close.assert_called_once()


def test_transport_caps_response_size_before_decoding(monkeypatch):
    reply = response()
    reply.iter_content.return_value = [b"x" * 65_537]
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=reply))
    with pytest.raises(ProviderError, match="provider_response_too_large"):
        OpenAICompatibleProvider("https://api.example.com/v1", "test", "key").extract([], 8)
    reply.close.assert_called_once()
