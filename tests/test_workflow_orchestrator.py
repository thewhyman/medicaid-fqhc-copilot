"""Tests for the Workflow Orchestrator — state machine transitions."""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.workflow_orchestrator import WorkflowOrchestrator


orchestrator = WorkflowOrchestrator()


def _renewal(step="IDENTIFIED", **overrides):
    base = {
        "current_step": step,
        "renewal_due_date": str(date.today() + timedelta(days=30)),
        "updated_at": str(date.today()),
    }
    base.update(overrides)
    return base


# --- Happy path ---

def test_identified_to_notified():
    result = orchestrator.process_event(_renewal("IDENTIFIED"), "risk_scored")
    assert result.success
    assert result.data["new_state"] == "NOTIFIED"


def test_notified_to_engaged():
    result = orchestrator.process_event(_renewal("NOTIFIED"), "patient_responded")
    assert result.success
    assert result.data["new_state"] == "ENGAGED"


def test_engaged_to_doc_collection():
    result = orchestrator.process_event(_renewal("ENGAGED"), "requirements_acknowledged")
    assert result.success
    assert result.data["new_state"] == "DOC_COLLECTION"


def test_doc_collection_to_validation():
    result = orchestrator.process_event(_renewal("DOC_COLLECTION"), "all_docs_received")
    assert result.success
    assert result.data["new_state"] == "VALIDATION"


def test_validation_to_submission_ready():
    result = orchestrator.process_event(_renewal("VALIDATION"), "all_docs_valid")
    assert result.success
    assert result.data["new_state"] == "SUBMISSION_READY"


def test_submission_ready_to_completed():
    result = orchestrator.process_event(_renewal("SUBMISSION_READY"), "submitted_to_state")
    assert result.success
    assert result.data["new_state"] == "COMPLETED"


def test_full_happy_path():
    """Walk through the entire happy path."""
    steps = [
        ("IDENTIFIED", "risk_scored", "NOTIFIED"),
        ("NOTIFIED", "patient_responded", "ENGAGED"),
        ("ENGAGED", "requirements_acknowledged", "DOC_COLLECTION"),
        ("DOC_COLLECTION", "all_docs_received", "VALIDATION"),
        ("VALIDATION", "all_docs_valid", "SUBMISSION_READY"),
        ("SUBMISSION_READY", "submitted_to_state", "COMPLETED"),
    ]
    for current, event, expected_next in steps:
        result = orchestrator.process_event(_renewal(current), event)
        assert result.success, f"{current} --{event}--> failed: {result.error}"
        assert result.data["new_state"] == expected_next


# --- Invalid events rejected ---

def test_invalid_event_completed():
    result = orchestrator.process_event(_renewal("COMPLETED"), "patient_responded")
    assert not result.success


def test_invalid_event_expired():
    result = orchestrator.process_event(_renewal("EXPIRED"), "all_docs_valid")
    assert not result.success


def test_invalid_event_identified():
    result = orchestrator.process_event(_renewal("IDENTIFIED"), "all_docs_valid")
    assert not result.success


# --- Failure/recovery states ---

def test_notified_to_no_response():
    result = orchestrator.process_event(_renewal("NOTIFIED"), "timeout")
    assert result.success
    assert result.data["new_state"] == "NO_RESPONSE"


def test_no_response_recovery():
    result = orchestrator.process_event(_renewal("NO_RESPONSE"), "escalation_sent")
    assert result.success
    assert result.data["new_state"] == "NOTIFIED"


def test_no_response_to_engaged():
    result = orchestrator.process_event(_renewal("NO_RESPONSE"), "patient_responded")
    assert result.success
    assert result.data["new_state"] == "ENGAGED"


def test_no_response_to_expired():
    result = orchestrator.process_event(_renewal("NO_RESPONSE"), "deadline_passed")
    assert result.success
    assert result.data["new_state"] == "EXPIRED"


def test_validation_to_invalid_doc():
    result = orchestrator.process_event(_renewal("VALIDATION"), "doc_invalid")
    assert result.success
    assert result.data["new_state"] == "INVALID_DOC"


def test_invalid_doc_to_doc_collection():
    result = orchestrator.process_event(_renewal("INVALID_DOC"), "resubmission_requested")
    assert result.success
    assert result.data["new_state"] == "DOC_COLLECTION"


def test_invalid_doc_caseworker_override():
    result = orchestrator.process_event(_renewal("INVALID_DOC"), "caseworker_override")
    assert result.success
    assert result.data["new_state"] == "SUBMISSION_READY"


def test_dropped_off_re_engaged():
    result = orchestrator.process_event(_renewal("DROPPED_OFF"), "patient_re_engaged")
    assert result.success
    assert result.data["new_state"] == "ENGAGED"


def test_dropped_off_to_expired():
    result = orchestrator.process_event(_renewal("DROPPED_OFF"), "deadline_passed")
    assert result.success
    assert result.data["new_state"] == "EXPIRED"


# --- Terminal states ---

def test_completed_is_terminal():
    assert orchestrator.is_terminal_state("COMPLETED")


def test_expired_is_terminal():
    assert orchestrator.is_terminal_state("EXPIRED")


def test_identified_is_not_terminal():
    assert not orchestrator.is_terminal_state("IDENTIFIED")


# --- Timeouts ---

def test_timeout_not_triggered():
    renewal = _renewal("NOTIFIED", updated_at=str(date.today()))
    result = orchestrator.check_timeouts(renewal)
    assert not result.data["is_timed_out"]


def test_timeout_triggered():
    renewal = _renewal("NOTIFIED", updated_at=str(date.today() - timedelta(days=15)))
    result = orchestrator.check_timeouts(renewal)
    assert result.data["is_timed_out"]
    assert result.data["timeout_event"] == "timeout"


def test_deadline_passed_triggers_timeout():
    renewal = _renewal("NOTIFIED",
                       renewal_due_date=str(date.today() - timedelta(days=1)),
                       updated_at=str(date.today()))
    result = orchestrator.check_timeouts(renewal)
    assert result.data["is_timed_out"]
    assert result.data["timeout_event"] == "deadline_passed"


# --- Required documents ---

def test_required_docs_adult():
    docs = orchestrator.get_required_documents({"age": 35, "is_pregnant": False, "has_disability": False})
    assert "pay_stub" in docs
    assert "utility_bill" in docs


def test_required_docs_pregnant():
    docs = orchestrator.get_required_documents({"age": 28, "is_pregnant": True})
    assert "pregnancy_verification" in docs


def test_required_docs_child():
    docs = orchestrator.get_required_documents({"age": 7})
    assert "birth_certificate" in docs


# --- Actions on transitions ---

def test_transition_has_actions():
    result = orchestrator.process_event(_renewal("IDENTIFIED"), "risk_scored")
    assert len(result.data["actions"]) > 0


# --- Audit log ---

def test_audit_log_on_transition():
    result = orchestrator.process_event(_renewal("IDENTIFIED"), "risk_scored")
    assert result.audit_log_entry is not None
    assert result.audit_log_entry["action"] == "state_transition"


# --- Valid events query ---

def test_get_valid_events():
    events = orchestrator.get_valid_events("IDENTIFIED")
    assert "risk_scored" in events
    assert "manual_add" in events


def test_get_valid_events_terminal():
    events = orchestrator.get_valid_events("COMPLETED")
    assert events == []


if __name__ == "__main__":
    test_identified_to_notified()
    test_notified_to_engaged()
    test_engaged_to_doc_collection()
    test_doc_collection_to_validation()
    test_validation_to_submission_ready()
    test_submission_ready_to_completed()
    test_full_happy_path()
    test_invalid_event_completed()
    test_invalid_event_expired()
    test_invalid_event_identified()
    test_notified_to_no_response()
    test_no_response_recovery()
    test_no_response_to_engaged()
    test_no_response_to_expired()
    test_validation_to_invalid_doc()
    test_invalid_doc_to_doc_collection()
    test_invalid_doc_caseworker_override()
    test_dropped_off_re_engaged()
    test_dropped_off_to_expired()
    test_completed_is_terminal()
    test_expired_is_terminal()
    test_identified_is_not_terminal()
    test_timeout_not_triggered()
    test_timeout_triggered()
    test_deadline_passed_triggers_timeout()
    test_required_docs_adult()
    test_required_docs_pregnant()
    test_required_docs_child()
    test_transition_has_actions()
    test_audit_log_on_transition()
    test_get_valid_events()
    test_get_valid_events_terminal()
    print("All workflow orchestrator tests passed.")
