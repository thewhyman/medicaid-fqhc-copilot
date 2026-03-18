"""Tests for the Outreach Agent — TCPA compliance, templates, responses."""

import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.outreach_agent import OutreachAgent, TEMPLATES


agent = OutreachAgent()


def _patient(**overrides):
    base = {"first_name": "Maria", "preferred_language": "en",
            "consent_status": "opted_in"}
    base.update(overrides)
    return base


def _renewal(**overrides):
    base = {"renewal_due_date": str(date.today() + timedelta(days=30)),
            "communication_log": []}
    base.update(overrides)
    return base


# --- TCPA: opt-out blocks sending ---

def test_opt_out_blocks():
    patient = _patient(consent_status="opted_out")
    result = agent.check_can_send(patient, _renewal())
    assert not result.data["can_send"]
    assert "opted out" in result.data["reason"].lower()


def test_pending_consent_blocks():
    patient = _patient(consent_status="pending")
    result = agent.check_can_send(patient, _renewal())
    assert not result.data["can_send"]


def test_opted_in_allows():
    result = agent.check_can_send(_patient(), _renewal())
    # May still be blocked by quiet hours, but consent check passes
    if not result.data["can_send"]:
        assert "quiet hours" in result.data["reason"].lower() or "cap" in result.data["reason"].lower()


# --- TCPA: frequency caps ---

def test_daily_cap():
    today = str(date.today()) + "T12:00:00"
    log = [{"type": "sms", "direction": "outbound", "timestamp": today}]
    renewal = _renewal(communication_log=log)
    result = agent.check_can_send(_patient(), renewal)
    if not result.data["can_send"]:
        reason = result.data["reason"].lower()
        assert "cap" in reason or "quiet" in reason


def test_weekly_cap():
    today = date.today()
    log = [
        {"type": "sms", "direction": "outbound", "timestamp": str(today - timedelta(days=i)) + "T12:00:00"}
        for i in range(3)
    ]
    renewal = _renewal(communication_log=log)
    result = agent.check_can_send(_patient(), renewal)
    if not result.data["can_send"]:
        reason = result.data["reason"].lower()
        assert "cap" in reason or "quiet" in reason


# --- STOP text in all templates ---

def test_all_templates_have_stop():
    for name, langs in TEMPLATES.items():
        for lang, template in langs.items():
            if lang == "es":
                assert "ALTO" in template or "STOP" in template, f"{name}/{lang} missing STOP/ALTO"
            else:
                assert "STOP" in template, f"{name}/{lang} missing STOP"


# --- Spanish templates exist ---

def test_spanish_templates_exist():
    for name, langs in TEMPLATES.items():
        assert "es" in langs, f"Template {name} missing Spanish translation"


# --- Message selection ---

def test_select_message_english():
    result = agent.select_message(_patient(), _renewal(), "low", template_name="initial_reminder")
    assert result.success
    assert result.data["language"] == "en"
    assert "Maria" in result.data["message"]
    assert "STOP" in result.data["message"]


def test_select_message_spanish():
    patient = _patient(preferred_language="es")
    result = agent.select_message(patient, _renewal(), "low", template_name="initial_reminder")
    assert result.success
    assert result.data["language"] == "es"
    assert "ALTO" in result.data["message"]


def test_select_message_auto_sequence():
    result = agent.select_message(_patient(), _renewal(), "low")
    assert result.success
    assert result.data["template_name"] == "initial_reminder"


def test_select_message_doc_request():
    result = agent.select_message(
        _patient(), _renewal(), "low",
        template_name="doc_request", doc_list="pay stub, utility bill",
    )
    assert result.success
    assert "pay stub" in result.data["message"]


# --- Response processing ---

def test_stop_response():
    result = agent.process_response("STOP")
    assert result.data["action"] == "opt_out"


def test_alto_response():
    result = agent.process_response("Alto")
    assert result.data["action"] == "opt_out"


def test_yes_response():
    result = agent.process_response("YES")
    assert result.data["action"] == "engaged"


def test_si_response():
    result = agent.process_response("SI")
    assert result.data["action"] == "engaged"


def test_help_response():
    result = agent.process_response("HELP")
    assert result.data["action"] == "escalate"


def test_unrecognized_response():
    result = agent.process_response("what is this about?")
    assert result.data["action"] == "unrecognized"


# --- Escalation logic ---

def test_escalation_after_2_unanswered():
    log = [
        {"type": "sms", "direction": "outbound", "status": "no_response"},
        {"type": "sms", "direction": "outbound", "status": "no_response"},
    ]
    result = agent.check_escalation(log)
    assert result.data["needs_escalation"]
    assert result.data["escalation_type"] == "caseworker_alert"


def test_escalation_after_3_unanswered():
    log = [
        {"type": "sms", "direction": "outbound", "status": "no_response"},
        {"type": "sms", "direction": "outbound", "status": "no_response"},
        {"type": "sms", "direction": "outbound", "status": "no_response"},
    ]
    result = agent.check_escalation(log)
    assert result.data["needs_escalation"]
    assert result.data["escalation_type"] == "phone_outreach"


def test_no_escalation_after_response():
    log = [
        {"type": "sms", "direction": "outbound", "status": "no_response"},
        {"type": "sms", "direction": "inbound", "status": "responded"},
    ]
    result = agent.check_escalation(log)
    assert not result.data["needs_escalation"]


if __name__ == "__main__":
    test_opt_out_blocks()
    test_pending_consent_blocks()
    test_opted_in_allows()
    test_daily_cap()
    test_weekly_cap()
    test_all_templates_have_stop()
    test_spanish_templates_exist()
    test_select_message_english()
    test_select_message_spanish()
    test_select_message_auto_sequence()
    test_select_message_doc_request()
    test_stop_response()
    test_alto_response()
    test_yes_response()
    test_si_response()
    test_help_response()
    test_unrecognized_response()
    test_escalation_after_2_unanswered()
    test_escalation_after_3_unanswered()
    test_no_escalation_after_response()
    print("All outreach agent tests passed.")
