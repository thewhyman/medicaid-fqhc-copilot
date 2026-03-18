"""Tests for the Memory Agent."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.memory_agent import MemoryAgent


def test_extract_patient_id():
    assert MemoryAgent.extract_patient_id("patient-5-1234567890") == 5
    assert MemoryAgent.extract_patient_id("patient-12-abc") == 12
    assert MemoryAgent.extract_patient_id("default") is None
    assert MemoryAgent.extract_patient_id("patient-abc") is None
    assert MemoryAgent.extract_patient_id("patient-") is None


def test_search_no_client():
    """When Mem0 is not configured, returns empty context gracefully."""
    agent = MemoryAgent()
    agent._client = None  # Force no client
    result = agent.search("test query", "patient-5-123")
    assert result.success
    assert result.data["context"] == ""
    assert result.data["user"] == "patient-5"


def test_search_default_user():
    """Non-patient session IDs use default user."""
    agent = MemoryAgent()
    agent._client = None
    result = agent.search("test", "default")
    assert result.data["user"] == "medicaid-copilot"


def test_save_no_client():
    """Save without Mem0 client returns failure gracefully."""
    agent = MemoryAgent()
    agent._client = None
    result = agent.save("query", "determination", "patient-5")
    assert not result.success


def test_save_empty_determination():
    """Save with empty determination returns failure."""
    agent = MemoryAgent()
    agent._client = None
    result = agent.save("query", "", "patient-5")
    assert not result.success


if __name__ == "__main__":
    test_extract_patient_id()
    test_search_no_client()
    test_search_default_user()
    test_save_no_client()
    test_save_empty_determination()
    print("All memory agent tests passed.")
