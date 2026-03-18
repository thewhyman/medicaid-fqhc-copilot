"""Tests for the Document Agent — validation logic (no LLM calls)."""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.document_agent import DocumentAgent, _names_match, _parse_date_flexible


# We test only the deterministic validate() method — classify/extract need LLM.


def _patient(**overrides):
    base = {"first_name": "Maria", "last_name": "Garcia", "age": 28, "state": "CA"}
    base.update(overrides)
    return base


# --- Validation: pay stub ---

def test_validate_pay_stub_valid():
    agent = DocumentAgent(openai_client=None)  # No LLM needed for validate
    extracted = {
        "employer_name": "Acme Corp",
        "pay_period_start": str(date.today() - timedelta(days=30)),
        "pay_period_end": str(date.today() - timedelta(days=15)),
        "gross_pay": 1500.0,
        "employee_name": "Maria Garcia",
    }
    result = agent.validate(extracted, "pay_stub", _patient())
    assert result.data["status"] == "accepted"
    assert result.data["issues"] == []


def test_validate_pay_stub_too_old():
    agent = DocumentAgent(openai_client=None)
    extracted = {
        "employer_name": "Acme Corp",
        "pay_period_start": str(date.today() - timedelta(days=100)),
        "pay_period_end": str(date.today() - timedelta(days=85)),
        "gross_pay": 1500.0,
        "employee_name": "Maria Garcia",
    }
    result = agent.validate(extracted, "pay_stub", _patient())
    assert result.data["status"] == "rejected"
    assert any("too old" in issue.lower() for issue in result.data["issues"])


def test_validate_pay_stub_missing_field():
    agent = DocumentAgent(openai_client=None)
    extracted = {
        "employer_name": "Acme Corp",
        "pay_period_start": str(date.today() - timedelta(days=30)),
        # Missing pay_period_end and gross_pay
    }
    result = agent.validate(extracted, "pay_stub", _patient())
    assert result.data["status"] == "rejected"
    assert any("missing" in issue.lower() for issue in result.data["issues"])


# --- Validation: utility bill ---

def test_validate_utility_bill_valid():
    agent = DocumentAgent(openai_client=None)
    extracted = {
        "service_address": "123 Main St, Los Angeles, CA",
        "billing_date": str(date.today() - timedelta(days=20)),
        "account_holder_name": "Maria Garcia",
    }
    result = agent.validate(extracted, "utility_bill", _patient())
    assert result.data["status"] == "accepted"


def test_validate_utility_bill_too_old():
    agent = DocumentAgent(openai_client=None)
    extracted = {
        "service_address": "123 Main St",
        "billing_date": str(date.today() - timedelta(days=120)),
        "account_holder_name": "Maria Garcia",
    }
    result = agent.validate(extracted, "utility_bill", _patient())
    assert result.data["status"] == "rejected"


# --- Validation: tax return ---

def test_validate_tax_return_valid():
    agent = DocumentAgent(openai_client=None)
    current_year = date.today().year
    extracted = {
        "filing_year": current_year - 1,
        "adjusted_gross_income": 18000,
        "filing_status": "single",
    }
    result = agent.validate(extracted, "tax_return", _patient())
    assert result.data["status"] == "accepted"


def test_validate_tax_return_old_year():
    agent = DocumentAgent(openai_client=None)
    extracted = {
        "filing_year": 2020,
        "adjusted_gross_income": 18000,
        "filing_status": "single",
    }
    result = agent.validate(extracted, "tax_return", _patient())
    assert result.data["status"] == "rejected"
    assert any("too old" in issue.lower() for issue in result.data["issues"])


# --- Validation: name mismatch ---

def test_validate_name_mismatch():
    agent = DocumentAgent(openai_client=None)
    extracted = {
        "employer_name": "Acme Corp",
        "pay_period_start": str(date.today() - timedelta(days=30)),
        "pay_period_end": str(date.today() - timedelta(days=15)),
        "gross_pay": 1500.0,
        "employee_name": "James Wilson",
    }
    result = agent.validate(extracted, "pay_stub", _patient())
    assert result.data["status"] == "rejected"
    assert any("mismatch" in issue.lower() for issue in result.data["issues"])


# --- Validation: immigration document expired ---

def test_validate_immigration_expired():
    agent = DocumentAgent(openai_client=None)
    extracted = {
        "document_type": "Green Card",
        "holder_name": "Maria Garcia",
        "expiration_date": str(date.today() - timedelta(days=30)),
        "status": "permanent_resident",
    }
    result = agent.validate(extracted, "immigration_document", _patient())
    assert result.data["status"] == "rejected"
    assert any("expired" in issue.lower() for issue in result.data["issues"])


# --- Name matching helper ---

def test_names_match_exact():
    assert _names_match("maria garcia", "maria garcia")


def test_names_match_subset():
    assert _names_match("maria garcia", "maria elena garcia")


def test_names_match_reversed():
    assert _names_match("garcia maria", "maria garcia")


def test_names_no_match():
    assert not _names_match("maria garcia", "james wilson")


# --- Date parsing helper ---

def test_parse_date_iso():
    result = _parse_date_flexible("2026-03-15")
    assert result == date(2026, 3, 15)


def test_parse_date_us():
    result = _parse_date_flexible("03/15/2026")
    assert result == date(2026, 3, 15)


def test_parse_date_invalid():
    result = _parse_date_flexible("not a date")
    assert result is None


def test_parse_date_none():
    result = _parse_date_flexible(None)
    assert result is None


# --- Unknown document type ---

def test_validate_unknown_type():
    agent = DocumentAgent(openai_client=None)
    result = agent.validate({}, "unknown_type", _patient())
    assert not result.success


if __name__ == "__main__":
    test_validate_pay_stub_valid()
    test_validate_pay_stub_too_old()
    test_validate_pay_stub_missing_field()
    test_validate_utility_bill_valid()
    test_validate_utility_bill_too_old()
    test_validate_tax_return_valid()
    test_validate_tax_return_old_year()
    test_validate_name_mismatch()
    test_validate_immigration_expired()
    test_names_match_exact()
    test_names_match_subset()
    test_names_match_reversed()
    test_names_no_match()
    test_parse_date_iso()
    test_parse_date_us()
    test_parse_date_invalid()
    test_parse_date_none()
    test_validate_unknown_type()
    print("All document agent tests passed.")
