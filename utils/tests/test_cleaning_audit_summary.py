"""Cleaning summaries must distinguish proposed extraction from applied results."""
from scripts.audit_gemini_clean import summarize


def test_provider_success_and_historical_changes_are_not_proof_of_application():
    report = summarize([
        {"status": "success", "changed_fields": ["price_text"]},
        {"status": "success", "final_outcome": "invalid", "applied": False, "changed_fields": []},
        {"status": "success", "final_outcome": "stale", "applied": False, "changed_fields": []},
        {"status": "success", "final_outcome": "success", "applied": True, "changed_fields": ["area_text"]},
    ])
    assert report["status_counts"] == {"success": 4}
    assert report["records_applied"] == 1
    assert report["records_without_final_outcome"] == 1
    assert report["applied_changed_fields"] == {"area_text": 1}
    assert report["final_outcome_counts"] == {"invalid": 1, "stale": 1, "success": 1, "unknown": 1}
    assert report["records_detail"][0]["applied"] is None


def test_provider_filter_applies_to_final_outcomes_too():
    report = summarize([
        {"provider": "selected", "final_outcome": "failed", "applied": False},
        {"provider": "other", "final_outcome": "success", "applied": True, "changed_fields": ["price_text"]},
    ], provider="selected")
    assert report["records"] == 1
    assert report["records_applied"] == 0
    assert report["final_outcome_counts"] == {"failed": 1}
    assert report["applied_changed_fields"] == {}
