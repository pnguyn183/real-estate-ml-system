"""Offline tests of evidence validation, never evidence of a Gemini call."""
import copy

import pytest

from scripts.verify_gemini_pipeline import (
    MODEL, VerificationBlocked, build_case, validate_case, verify_evidence,
)


def test_prepared_case_meets_unmodified_fallback_and_is_isolated():
    case = build_case()
    record = validate_case(case)
    assert record["is_synthetic"] is True
    assert record["url"].startswith("https://synthetic.invalid/")
    assert "price_text" not in record and "area_text" not in record
    assert case["routing_errors_before"] == ["missing_or_invalid_price_vnd", "missing_or_invalid_area_m2"]
    assert build_case()["event_id"] != case["event_id"]


def test_case_cannot_be_relabelled_as_a_live_record():
    case = build_case()
    case["record"]["url"] = "https://example.com/listing"
    with pytest.raises(VerificationBlocked, match="synthetic_domain"):
        validate_case(case)


def test_case_detects_changes_after_event_filter_was_prepared():
    case = build_case()
    case["record"]["description"] += " changed"
    with pytest.raises(VerificationBlocked, match="identity_mismatch"):
        validate_case(case)


def test_case_accepts_explicit_model_without_changing_fallback():
    case = build_case("models/offline-model-for-validator-test")
    assert case["expected_model"] == "models/offline-model-for-validator-test"
    assert validate_case(case) == case["record"]
    assert case["routing_errors_before"] == ["missing_or_invalid_price_vnd", "missing_or_invalid_area_m2"]


@pytest.mark.parametrize("model", [None, "", " ", "model\n", "../model", "model//name", "model?key=secret", "model#fragment", "x" * 201])
def test_case_rejects_unsafe_or_empty_model(model):
    with pytest.raises(VerificationBlocked, match="expected_model_invalid"):
        build_case(model)
    case = build_case()
    case["expected_model"] = model
    with pytest.raises(VerificationBlocked, match="expected_model_invalid"):
        validate_case(case)


@pytest.fixture
def evidence():
    # Deliberately local fixtures to exercise validator branches only.
    return {
        "event_id": "offline-test",
        "expected_model": MODEL,
        "provider_audit": {"stages": [
            {"stage": "request", "payload": {
                "request": {"model": MODEL}, "provider": "openai_compatible",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            }},
            {"stage": "response", "payload": {"http_status": 200, "raw_response": "test-only"}},
            {"stage": "parsed", "payload": {"parsed_response": {"fields": {"area_m2": 80}}}},
        ]},
        "kafka_messages": {
            "raw_input": {}, "ai_input": {},
            "ai_result": {"value": {"result": {"status": "success", "model": MODEL, "attempts": 1}}},
        },
        "receipt": {"status": "completed", "outcome": "success"},
        "final_record": {"is_synthetic": True, "is_model_candidate": False,
                         "ai_status": "success", "ai_event_id": "offline-test"},
        "cleaning_audit": {"event_id": "offline-test"},
        "raw_record_preserved": True, "primary_matching_documents": {"listings_raw": 0},
    }


def test_full_correlated_evidence_is_accepted(evidence):
    verify_evidence(evidence)


def test_custom_model_requires_matching_request_and_result(evidence):
    model = "models/offline-model-for-validator-test"
    evidence["expected_model"] = model
    evidence["provider_audit"]["stages"][0]["payload"]["request"]["model"] = model
    evidence["kafka_messages"]["ai_result"]["value"]["result"]["model"] = model
    verify_evidence(evidence)


@pytest.mark.parametrize("location,reason", [
    ("request", "actual_request_model_mismatch"),
    ("result", "actual_result_model_mismatch"),
])
def test_request_or_result_using_unexpected_model_is_rejected(evidence, location, reason):
    if location == "request":
        evidence["provider_audit"]["stages"][0]["payload"]["request"]["model"] = "offline-other-model"
    else:
        evidence["kafka_messages"]["ai_result"]["value"]["result"]["model"] = "offline-other-model"
    with pytest.raises(VerificationBlocked, match=reason):
        verify_evidence(evidence)


def test_one_matching_request_does_not_hide_a_different_model(evidence):
    mismatched = copy.deepcopy(evidence["provider_audit"]["stages"][0])
    mismatched["payload"]["request"]["model"] = "offline-other-model"
    evidence["provider_audit"]["stages"].append(mismatched)
    with pytest.raises(VerificationBlocked, match="actual_request_model_mismatch"):
        verify_evidence(evidence)


@pytest.mark.parametrize("http_status,error_code", [(404, "provider_request_rejected"), (429, "provider_rate_limited")])
def test_real_provider_rejection_has_precise_blocker_and_is_not_verified(evidence, http_status, error_code):
    evidence["provider_audit"]["stages"][1]["payload"].update(
        http_status=http_status, raw_response='{"error":{"message":"offline error fixture"}}',
    )
    evidence["provider_audit"]["stages"] = evidence["provider_audit"]["stages"][:2]
    evidence["kafka_messages"]["ai_result"]["value"]["result"].update(status="failed", error_code=error_code)
    with pytest.raises(VerificationBlocked, match="extraction_failed_" + error_code):
        verify_evidence(evidence)


@pytest.mark.parametrize("stage,reason", [
    ("request", "actual_provider_request_not_audited"),
    ("response", "actual_successful_provider_response_not_audited"),
    ("parsed", "validated_parsed_response_not_audited"),
])
def test_enabled_configuration_or_cached_record_cannot_replace_wire_evidence(evidence, stage, reason):
    evidence["provider_audit"]["stages"] = [
        item for item in evidence["provider_audit"]["stages"] if item["stage"] != stage
    ]
    with pytest.raises(VerificationBlocked, match=reason):
        verify_evidence(evidence)


@pytest.mark.parametrize("change,reason", [
    (lambda report: report["provider_audit"]["stages"][1]["payload"].update(http_status=429), "successful_provider_response"),
    (lambda report: report["provider_audit"]["stages"][0]["payload"].update(base_url="https://example.invalid"), "google_gemini_endpoint"),
    (lambda report: report["receipt"].update(outcome="invalid"), "final_processor_validation"),
    (lambda report: report["kafka_messages"].pop("ai_input"), "incomplete_kafka_path"),
    (lambda report: report["final_record"].update(is_model_candidate=True), "synthetic_training_exclusion"),
    (lambda report: report["primary_matching_documents"].update(training_features=1), "primary_data_isolation"),
    (lambda report: report["kafka_messages"]["ai_result"]["value"]["result"].update(attempts=0), "no_fresh_successful"),
])
def test_partial_error_or_unsafe_run_is_never_marked_verified(evidence, change, reason):
    changed = copy.deepcopy(evidence)
    change(changed)
    with pytest.raises(VerificationBlocked, match=reason):
        verify_evidence(changed)
