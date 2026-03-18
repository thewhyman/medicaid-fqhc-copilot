"""Tests for the Risk Scoring Agent."""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.risk_scoring_agent import RiskScoringAgent


agent = RiskScoringAgent()


def _patient(**overrides):
    base = {"age": 35, "household_size": 2, "preferred_language": "en",
            "response_history": [], "contact_info_quality": "verified"}
    base.update(overrides)
    return base


def _renewal(**overrides):
    base = {"renewal_due_date": str(date.today() + timedelta(days=45)),
            "previous_renewal_outcome": "completed"}
    base.update(overrides)
    return base


# --- Score range ---

def test_score_in_range():
    result = agent.score(_patient(), _renewal())
    assert 0.0 <= result.data["score"] <= 1.0


def test_score_capped_at_1():
    """Extreme case: all risk factors maxed out."""
    patient = _patient(
        age=70, household_size=8, preferred_language="es",
        response_history=[{"status": "no_response"}] * 10,
        contact_info_quality="bounced",
    )
    renewal = _renewal(
        renewal_due_date=str(date.today() + timedelta(days=5)),
        previous_renewal_outcome="lapsed",
    )
    result = agent.score(patient, renewal)
    assert result.data["score"] <= 1.0


# --- Tier assignment ---

def test_low_tier():
    result = agent.score(_patient(), _renewal())
    assert result.data["tier"] == "low"


def test_critical_tier_deadline_imminent():
    renewal = _renewal(
        renewal_due_date=str(date.today() + timedelta(days=5)),
        previous_renewal_outcome="lapsed",
    )
    patient = _patient(
        response_history=[{"status": "no_response"}] * 4,
    )
    result = agent.score(patient, renewal)
    assert result.data["tier"] in ("critical", "high")
    assert result.data["score"] >= 0.40


def test_medium_tier():
    renewal = _renewal(
        renewal_due_date=str(date.today() + timedelta(days=25)),
        previous_renewal_outcome="first_renewal",
    )
    result = agent.score(_patient(), renewal)
    assert result.data["tier"] in ("medium", "high")


# --- Factor detection ---

def test_deadline_factor():
    renewal = _renewal(renewal_due_date=str(date.today() + timedelta(days=10)))
    result = agent.score(_patient(), renewal)
    factor_names = [f["name"] for f in result.data["factors"]]
    assert "deadline_proximity" in factor_names


def test_non_english_factor():
    patient = _patient(preferred_language="es")
    result = agent.score(patient, _renewal())
    factor_names = [f["name"] for f in result.data["factors"]]
    assert "non_english" in factor_names


def test_elderly_factor():
    patient = _patient(age=70)
    result = agent.score(patient, _renewal())
    factor_names = [f["name"] for f in result.data["factors"]]
    assert "elderly" in factor_names


def test_large_household_factor():
    patient = _patient(household_size=8)
    result = agent.score(patient, _renewal())
    factor_names = [f["name"] for f in result.data["factors"]]
    assert "large_household" in factor_names


def test_lapsed_factor():
    renewal = _renewal(previous_renewal_outcome="lapsed")
    result = agent.score(_patient(), renewal)
    factor_names = [f["name"] for f in result.data["factors"]]
    assert "prior_lapsed" in factor_names


def test_first_renewal_factor():
    renewal = _renewal(previous_renewal_outcome="first_renewal")
    result = agent.score(_patient(), renewal)
    factor_names = [f["name"] for f in result.data["factors"]]
    assert "first_renewal" in factor_names


def test_no_response_rate():
    patient = _patient(
        response_history=[{"status": "no_response"}] * 3 + [{"status": "responded"}],
    )
    result = agent.score(patient, _renewal())
    factor_names = [f["name"] for f in result.data["factors"]]
    assert "no_response_rate" in factor_names


# --- Determinism ---

def test_deterministic():
    patient = _patient(age=70, preferred_language="es")
    renewal = _renewal(renewal_due_date=str(date.today() + timedelta(days=10)))
    scores = [agent.score(patient, renewal).data["score"] for _ in range(5)]
    assert len(set(scores)) == 1


# --- Recommended actions ---

def test_recommended_actions_exist():
    result = agent.score(_patient(), _renewal())
    assert len(result.data["recommended_actions"]) > 0


# --- Audit log ---

def test_audit_log_entry():
    result = agent.score(_patient(), _renewal())
    assert result.audit_log_entry is not None
    assert result.audit_log_entry["actor"] == "risk_scoring_agent"


# --- Tier boundary checks ---

def test_tier_boundaries():
    assert RiskScoringAgent._get_tier(0.0) == "low"
    assert RiskScoringAgent._get_tier(0.19) == "low"
    assert RiskScoringAgent._get_tier(0.20) == "medium"
    assert RiskScoringAgent._get_tier(0.39) == "medium"
    assert RiskScoringAgent._get_tier(0.40) == "high"
    assert RiskScoringAgent._get_tier(0.69) == "high"
    assert RiskScoringAgent._get_tier(0.70) == "critical"
    assert RiskScoringAgent._get_tier(1.0) == "critical"


if __name__ == "__main__":
    test_score_in_range()
    test_score_capped_at_1()
    test_low_tier()
    test_critical_tier_deadline_imminent()
    test_medium_tier()
    test_deadline_factor()
    test_non_english_factor()
    test_elderly_factor()
    test_large_household_factor()
    test_lapsed_factor()
    test_first_renewal_factor()
    test_no_response_rate()
    test_deterministic()
    test_recommended_actions_exist()
    test_audit_log_entry()
    test_tier_boundaries()
    print("All risk scoring tests passed.")
