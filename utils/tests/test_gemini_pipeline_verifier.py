"""Offline tests of evidence validation, never evidence of a Gemini call."""
import copy

import pytest

from scripts.verify_gemini_pipeline import (
    VerificationBlocked, build_case, validate_case, verify_evidence, main,
)

MODEL = "offline-model-for-validator-test"


def test_prepared_case_meets_unmodified_fallback_and_is_isolated():
    case = build_case(MODEL)
    record = validate_case(case)
    assert record["is_synthetic"] is True
    assert record["url"].startswith("https://synthetic.invalid/")
    assert record["schema_version"] == 2
    assert record["price_text"] is None and record["area_text"] is None
    assert record["price_raw"] is None and record["area_raw"] is None
    assert case["routing_errors_before"] == ["missing_or_invalid_price_vnd", "missing_or_invalid_area_m2"]
    assert build_case(MODEL)["event_id"] != case["event_id"]


def test_case_cannot_be_relabelled_as_a_live_record():
    case = build_case(MODEL)
    case["record"]["url"] = "https://example.com/listing"
    with pytest.raises(VerificationBlocked, match="synthetic_domain"):
        validate_case(case)


def test_case_detects_changes_after_event_filter_was_prepared():
    case = build_case(MODEL)
    case["record"]["description"] += " changed"
    with pytest.raises(VerificationBlocked, match="identity_mismatch"):
        validate_case(case)


def test_case_accepts_explicit_model_without_changing_fallback():
    case = build_case("models/offline-model-for-validator-test")
    assert case["expected_model"] == "models/offline-model-for-validator-test"
    assert validate_case(case) == case["record"]
    assert case["routing_errors_before"] == ["missing_or_invalid_price_vnd", "missing_or_invalid_area_m2"]


def test_cli_never_guesses_a_model_when_preparing_a_live_case(monkeypatch, tmp_path):
    target = tmp_path / "case.json"
    monkeypatch.setattr("sys.argv", ["verify_gemini_pipeline.py", "--prepare", "--case", str(target)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not target.exists()


@pytest.mark.parametrize("model", [None, "", " ", "model\n", "../model", "model//name", "model?key=secret", "model#fragment", "x" * 201])
def test_case_rejects_unsafe_or_empty_model(model):
    with pytest.raises(VerificationBlocked, match="expected_model_invalid"):
        build_case(model)
    case = build_case(MODEL)
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


def canonical_evidence(evidence):
    record = build_case(MODEL)["record"]
    evidence["before"] = record
    evidence["final_record"].update(
        schema_version=2, feature_status="current", price_vnd=8e9, area_m2=80,
        validation_errors=[], training_excluded=False,
        **{field: record[field] for field in ("source_url", "source_listing_id", "raw_payload_hash")},
    )
    evidence["cleaning_audit"].update(final_outcome="success", applied=True)
    return evidence


def test_canonical_source_requires_applied_validated_fields(evidence):
    verify_evidence(canonical_evidence(evidence))


@pytest.mark.parametrize("change,reason", [
    (lambda report: report["final_record"].update(price_vnd=None), "canonical_source_extraction"),
    (lambda report: report["final_record"].update(training_excluded=True), "canonical_source_extraction"),
    (lambda report: report["final_record"].update(schema_version=1), "canonical_source_extraction"),
    (lambda report: report["final_record"].update(source_url="changed"), "canonical_source_provenance"),
    (lambda report: report["cleaning_audit"].update(final_outcome="invalid", applied=False), "final_cleaning_application"),
])
def test_canonical_source_evidence_rejects_unapplied_or_changed_identity(evidence, change, reason):
    report = canonical_evidence(evidence)
    change(report)
    with pytest.raises(VerificationBlocked, match=reason):
        verify_evidence(report)


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
