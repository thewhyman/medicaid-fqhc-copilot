"""Tests for eval agents (correctness, efficiency, quality keywords)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.eval_correctness import CorrectnessEval
from agents.eval_efficiency import EfficiencyEval
from agents.eval_quality import QualityEval


correctness = CorrectnessEval()
efficiency = EfficiencyEval()


# --- Correctness ---

def test_correctness_match():
    patient = {
        "state": "CA", "household_size": 1, "annual_income": 18000,
        "age": 30, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    result = correctness.check(patient, "The patient is ELIGIBLE for Medicaid.")
    assert result.passed
    assert result.data["match"]


def test_correctness_mismatch():
    patient = {
        "state": "CA", "household_size": 1, "annual_income": 18000,
        "age": 30, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    result = correctness.check(patient, "The patient is NOT ELIGIBLE for Medicaid.")
    assert not result.passed
    assert not result.data["match"]


def test_correctness_ambiguous():
    patient = {
        "state": "GA", "household_size": 1, "annual_income": 8000,
        "age": 55, "is_pregnant": False, "has_disability": True, "is_us_citizen": True,
    }
    result = correctness.check(patient, "The patient is ELIGIBLE.")
    assert result.passed  # Ambiguous cases always pass
    assert result.data["match"]


def test_correctness_unparseable():
    patient = {
        "state": "CA", "household_size": 1, "annual_income": 18000,
        "age": 30, "is_pregnant": False, "has_disability": False, "is_us_citizen": True,
    }
    result = correctness.check(patient, "I need more information to make a determination.")
    # parse_determination returns None, engine says True => None != True => mismatch
    assert not result.passed


# --- Efficiency ---

def test_efficiency_pass():
    result = efficiency.check(api_calls=3, tool_names=["read_query", "write_file"])
    assert result.passed


def test_efficiency_too_many_calls():
    result = efficiency.check(api_calls=5, tool_names=["read_query"])
    assert not result.passed
    assert "too many" in result.details


def test_efficiency_banned_tool():
    result = efficiency.check(api_calls=2, tool_names=["read_query", "fetch"])
    assert not result.passed
    assert "fetch" in result.details


# --- Quality (keyword checks only — QA review requires LLM) ---

def test_quality_keywords_patient1():
    response = "Maria Garcia is pregnant and lives in California. Her income is below the 213% FPL threshold. She is ELIGIBLE."
    result = QualityEval.check_keywords(1, response)
    assert result.passed


def test_quality_keywords_patient2():
    response = "James Wilson lives in Texas, a non-expansion state. The adult threshold is 14% FPL. He is NOT ELIGIBLE."
    result = QualityEval.check_keywords(2, response)
    assert result.passed


def test_quality_keywords_missing():
    response = "The patient is eligible."
    result = QualityEval.check_keywords(1, response)
    assert not result.passed  # Missing pregnant, 213%, CA


def test_quality_keywords_no_requirements():
    result = QualityEval.check_keywords(99, "Anything goes")
    assert result.passed  # No keywords required for unknown patient


if __name__ == "__main__":
    test_correctness_match()
    test_correctness_mismatch()
    test_correctness_ambiguous()
    test_correctness_unparseable()
    test_efficiency_pass()
    test_efficiency_too_many_calls()
    test_efficiency_banned_tool()
    test_quality_keywords_patient1()
    test_quality_keywords_patient2()
    test_quality_keywords_missing()
    test_quality_keywords_no_requirements()
    print("All eval agent tests passed.")
