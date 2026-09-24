"""Offline audit safeguards; these mocks are not evidence of a Gemini call."""
import json
from unittest.mock import Mock
import pytest

from agents.provider_audit import capture, emit, scrub
from agents.providers import OpenAICompatibleProvider, ProviderError


def test_default_and_nonselected_events_never_touch_database(monkeypatch):
    monkeypatch.delenv("AI_AUDIT_EVENT_IDS", raising=False)
    db = Mock()
    with capture(db, "unselected", {"description": "private"}):
        emit("request", {"messages": []})
    monkeypatch.setenv("AI_AUDIT_EVENT_IDS", "another")
    with capture(db, "unselected", {}):
        emit("request", {})
    assert db.mock_calls == []


def test_transport_capture_records_exact_payload_and_body_without_credentials(monkeypatch):
    secret = "unit-test-only-credential"
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("AI_AUDIT_EVENT_IDS", "selected")
    collection = Mock()
    database = {"ai_provider_audit": collection}
    response = Mock(status_code=200)
    raw = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
    response.iter_content.return_value = [raw.encode()]
    post = Mock(return_value=response)
    monkeypatch.setattr("agents.providers.requests.post", post)
    messages = [{"role": "user", "content": "Only a test"}]
    with capture(database, "selected", {"description": "Only a test"}):
        assert OpenAICompatibleProvider("https://provider.example/v1", "test-model", secret).extract(messages, 10) == "{}"
        emit("parsed", {"api_key": secret, "echo": secret})
    stages = [call.args[1]["$push"]["stages"] for call in collection.update_one.call_args_list]
    assert [s["stage"] for s in stages] == ["before", "request", "http_status", "response", "parsed"]
    assert stages[1]["payload"]["request"] == post.call_args.kwargs["json"]
    assert stages[3]["payload"]["raw_response"] == raw
    assert secret not in json.dumps(stages)
    assert "Authorization" not in json.dumps(stages)
    assert stages[-1]["payload"] == {"api_key": "[REDACTED]", "echo": "[REDACTED]"}
    count = collection.update_one.call_count
    emit("request", {})
    assert collection.update_one.call_count == count


def test_audit_database_failure_does_not_change_extraction_or_log_exception(monkeypatch, caplog):
    monkeypatch.setenv("AI_AUDIT_EVENT_IDS", "selected")
    collection = Mock()
    collection.update_one.side_effect = RuntimeError("private-connection-string")
    with capture({"ai_provider_audit": collection}, "selected", {}):
        emit("request", {"sample": True})
    assert "provider_audit_write_failed" in caplog.text
    assert "private-connection-string" not in caplog.text


def test_nested_secret_redaction():
    assert scrub({"items": [{"authorization": "Bearer abc"}], "raw": "Bearer abc.def", "password": "x"}) == {
        "items": [{"authorization": "[REDACTED]"}], "raw": "Bearer [REDACTED]", "password": "[REDACTED]"}


def test_provider_error_body_is_evidence_not_success(monkeypatch):
    monkeypatch.setenv("AI_AUDIT_EVENT_IDS", "selected")
    collection = Mock()
    response = Mock(status_code=404)
    raw = '{"error":{"code":404,"status":"NOT_FOUND"}}'
    response.iter_content.return_value = [raw.encode()]
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=response))
    with capture({"ai_provider_audit": collection}, "selected", {}):
        with pytest.raises(ProviderError, match="provider_request_rejected"):
            OpenAICompatibleProvider("https://provider.example/v1", "test-model", "unit-test-key").extract([], 10)
    stages = [call.args[1]["$push"]["stages"] for call in collection.update_one.call_args_list]
    assert stages[-1]["stage"] == "response"
    assert stages[-1]["payload"] == {"http_status": 404, "raw_response": raw, "truncated": False}


def test_error_body_is_bounded_and_configured_secret_is_redacted(monkeypatch):
    secret = "unit-test-provider-credential"
    monkeypatch.setenv("LLM_API_KEY", secret)
    monkeypatch.setenv("AI_AUDIT_EVENT_IDS", "selected")
    monkeypatch.setattr(OpenAICompatibleProvider, "MAX_RESPONSE_BYTES", 64)
    collection = Mock()
    response = Mock(status_code=404)
    raw = (secret + " " + "x" * 100).encode()
    response.iter_content.return_value = (bytes([byte]) for byte in raw)
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=response))
    with capture({"ai_provider_audit": collection}, "selected", {}):
        with pytest.raises(ProviderError, match="provider_request_rejected"):
            OpenAICompatibleProvider("https://provider.example/v1", "test-model", secret).extract([], 10)
    stages = [call.args[1]["$push"]["stages"] for call in collection.update_one.call_args_list]
    evidence = stages[-1]["payload"]
    assert evidence["truncated"] is True
    assert evidence["raw_response"] == raw[:64].decode().replace(secret, "[REDACTED]")
    assert secret not in json.dumps(stages)
    response.close.assert_called_once()


def test_error_body_is_not_read_when_audit_is_disabled(monkeypatch):
    monkeypatch.delenv("AI_AUDIT_EVENT_IDS", raising=False)
    response = Mock(status_code=404)
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=response))
    with capture(Mock(), "unselected", {}):
        with pytest.raises(ProviderError, match="provider_request_rejected"):
            OpenAICompatibleProvider("https://provider.example/v1", "test-model", "test-key").extract([], 10)
    response.iter_content.assert_not_called()
    response.close.assert_called_once()


@pytest.mark.parametrize("status,code,retryable", [
    (401, "provider_auth", False),
    (403, "provider_auth", False),
    (404, "provider_request_rejected", False),
    (429, "provider_rate_limited", True),
    (503, "provider_unavailable", True),
])
def test_error_body_read_failure_preserves_provider_classification(monkeypatch, caplog, status, code, retryable):
    monkeypatch.setenv("AI_AUDIT_EVENT_IDS", "selected")
    collection = Mock()
    response = Mock(status_code=status)
    response.iter_content.side_effect = RuntimeError("private-provider-details")
    monkeypatch.setattr("agents.providers.requests.post", Mock(return_value=response))
    with capture({"ai_provider_audit": collection}, "selected", {}):
        with pytest.raises(ProviderError) as caught:
            OpenAICompatibleProvider("https://provider.example/v1", "test-model", "test-key").extract([], 10)
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    stages = [call.args[1]["$push"]["stages"] for call in collection.update_one.call_args_list]
    assert stages[-1]["payload"] == {"http_status": status, "raw_response": "", "truncated": True}
    assert "private-provider-details" not in json.dumps(stages) + caplog.text
    response.close.assert_called_once()


def test_capture_cleans_up_after_extraction_exception(monkeypatch):
    monkeypatch.setenv("AI_AUDIT_EVENT_IDS", "selected")
    collection = Mock()
    with pytest.raises(RuntimeError, match="extraction-failed"):
        with capture({"ai_provider_audit": collection}, "selected", {}):
            raise RuntimeError("extraction-failed")
    count = collection.update_one.call_count
    emit("request", {"record": "another event must not leak into selected audit"})
    assert collection.update_one.call_count == count
