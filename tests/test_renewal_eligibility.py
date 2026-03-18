"""Tests for renewal eligibility checks (EligibilityAgent.check_renewal_eligibility)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.eligibility_agent import EligibilityAgent


# --- No changes → still eligible ---

def test_no_changes():
    patient = {
        "state": "CA", "household_size": 1, "annual_income": 18000,
        "age": 30, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    result = EligibilityAgent.check_renewal_eligibility(patient, {})
    assert result.success
    assert result.data["still_eligible"]
    assert result.data["changes"] == []
    assert result.data["action"] == "approve_renewal"


# --- Income increased above threshold ---

def test_income_increased_ineligible():
    patient = {
        "state": "CA", "household_size": 1, "annual_income": 18000,
        "age": 30, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    result = EligibilityAgent.check_renewal_eligibility(patient, {"annual_income": 50000})
    assert result.success
    assert not result.data["still_eligible"]
    assert result.data["action"] == "notify_patient_ineligible_with_alternatives"
    # Income_pct should have changed
    change_fields = [c["field"] for c in result.data["changes"]]
    assert "eligible" in change_fields
    assert "income_pct" in change_fields


# --- Patient moved to non-expansion state ---

def test_moved_to_non_expansion():
    patient = {
        "state": "CA", "household_size": 1, "annual_income": 18000,
        "age": 30, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    # TX is non-expansion with 14% adult threshold → $18,000 would exceed it
    result = EligibilityAgent.check_renewal_eligibility(patient, {"state": "TX"})
    assert result.success
    assert not result.data["still_eligible"]
    change_fields = [c["field"] for c in result.data["changes"]]
    assert "expansion" in change_fields


# --- Age transition: child → adult ---

def test_age_transition_child_to_adult():
    patient = {
        "state": "FL", "household_size": 3, "annual_income": 35000,
        "age": 18, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    # At age 18 they're already adult; at age 19 same. But check at 7 → 19 transition
    child_patient = {**patient, "age": 7}
    result = EligibilityAgent.check_renewal_eligibility(child_patient, {"age": 19})
    assert result.success
    change_fields = [c["field"] for c in result.data["changes"]]
    assert "category" in change_fields


# --- Became pregnant (higher threshold) ---

def test_became_pregnant():
    patient = {
        "state": "GA", "household_size": 2, "annual_income": 40000,
        "age": 27, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    result_before = EligibilityAgent.check_renewal_eligibility(patient, {})
    result_after = EligibilityAgent.check_renewal_eligibility(patient, {"is_pregnant": True})
    # Pregnancy gives higher threshold in GA
    assert result_after.data["renewed"]["category"] == "pregnant"
    change_fields = [c["field"] for c in result_after.data["changes"]]
    assert "category" in change_fields


# --- Ambiguous case → caseworker review ---

def test_ambiguous_caseworker_review():
    patient = {
        "state": "GA", "household_size": 1, "annual_income": 8000,
        "age": 55, "is_pregnant": False, "has_disability": True, "is_us_citizen": True,
    }
    result = EligibilityAgent.check_renewal_eligibility(patient, {})
    assert result.success
    # Disabled in non-expansion is ambiguous
    assert result.data["renewed"]["ambiguous"]
    assert result.data["action"] == "caseworker_review_required"


# --- Alaska FPL ---

def test_alaska_fpl_renewal():
    patient = {
        "state": "AK", "household_size": 1, "annual_income": 26000,
        "age": 41, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    result = EligibilityAgent.check_renewal_eligibility(patient, {})
    assert result.success
    assert result.data["renewed"]["fpl"] > 15000  # Alaska FPL is higher


# --- Audit log ---

def test_audit_log_present():
    patient = {
        "state": "CA", "household_size": 1, "annual_income": 18000,
        "age": 30, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    result = EligibilityAgent.check_renewal_eligibility(patient, {})
    assert result.audit_log_entry is not None
    assert result.audit_log_entry["actor"] == "eligibility_agent"


if __name__ == "__main__":
    test_no_changes()
    test_income_increased_ineligible()
    test_moved_to_non_expansion()
    test_age_transition_child_to_adult()
    test_became_pregnant()
    test_ambiguous_caseworker_review()
    test_alaska_fpl_renewal()
    test_audit_log_present()
    print("All renewal eligibility tests passed.")
