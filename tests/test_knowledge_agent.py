"""Tests for the Knowledge Agent."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.knowledge_agent import KnowledgeAgent


agent = KnowledgeAgent()


def test_ca_pregnant():
    patient = {"state": "CA", "household_size": 3, "age": 28, "is_pregnant": True}
    result = agent.get_patient_rules(patient)
    assert result.success
    assert result.data["category"] == "pregnant"
    assert result.data["expansion"] is True
    assert result.data["fpl"] > 0
    assert result.data["threshold_pct"] > 0


def test_ak_adult():
    patient = {"state": "AK", "household_size": 1, "age": 41}
    result = agent.get_patient_rules(patient)
    assert result.success
    assert result.data["category"] == "adult"
    # Alaska FPL should be higher than standard
    assert result.data["fpl"] > 15000


def test_hi_large_household():
    patient = {"state": "HI", "household_size": 8, "age": 34}
    result = agent.get_patient_rules(patient)
    assert result.success
    assert result.data["fpl"] > 50000  # Hawaii HH=8 FPL is high


def test_unknown_state():
    patient = {"state": "ZZ", "household_size": 1, "age": 30}
    result = agent.get_patient_rules(patient)
    assert not result.success
    assert "not found" in result.error


def test_child_category():
    patient = {"state": "FL", "household_size": 4, "age": 7}
    result = agent.get_patient_rules(patient)
    assert result.success
    assert result.data["category"] == "child"


def test_get_state_info():
    result = agent.get_state_info("TX")
    assert result.success
    assert result.data["expansion"] is False

    result = agent.get_state_info("CA")
    assert result.success
    assert result.data["expansion"] is True


def test_get_state_info_unknown():
    result = agent.get_state_info("ZZ")
    assert not result.success


if __name__ == "__main__":
    test_ca_pregnant()
    test_ak_adult()
    test_hi_large_household()
    test_unknown_state()
    test_child_category()
    test_get_state_info()
    test_get_state_info_unknown()
    print("All knowledge agent tests passed.")
