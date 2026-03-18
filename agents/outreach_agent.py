"""Outreach Agent: TCPA-compliant SMS outreach for renewal patients.

No LLM — template selection + compliance rules. Handles message selection,
TCPA compliance gating, and inbound response processing.
"""

import logging
from datetime import datetime, time

from agents.base import AgentResult
from config import QUIET_HOURS, SMS_FREQUENCY_CAP_DAILY, SMS_FREQUENCY_CAP_WEEKLY

logger = logging.getLogger(__name__)


# Message templates: EN and ES for each type
TEMPLATES = {
    "initial_reminder": {
        "en": (
            "Hi {first_name}, your Medicaid coverage needs to be renewed by {deadline}. "
            "We can help you complete this. Reply YES to get started or call {fqhc_phone}. "
            "Reply STOP to opt out."
        ),
        "es": (
            "Hola {first_name}, su cobertura de Medicaid necesita renovarse antes del {deadline}. "
            "Podemos ayudarle a completar esto. Responda SI para comenzar o llame al {fqhc_phone}. "
            "Responda ALTO para cancelar."
        ),
    },
    "second_reminder": {
        "en": (
            "Reminder: {first_name}, your Medicaid renewal is due by {deadline}. "
            "Don't lose your coverage — reply YES and we'll guide you through it. "
            "Reply STOP to opt out."
        ),
        "es": (
            "Recordatorio: {first_name}, su renovacion de Medicaid vence el {deadline}. "
            "No pierda su cobertura — responda SI y le guiaremos. "
            "Responda ALTO para cancelar."
        ),
    },
    "urgent_reminder": {
        "en": (
            "IMPORTANT: {first_name}, your Medicaid coverage expires in {days} days. "
            "If you don't renew, you may lose your health coverage. "
            "Reply YES and we'll walk you through it step by step. "
            "Reply STOP to opt out."
        ),
        "es": (
            "IMPORTANTE: {first_name}, su cobertura de Medicaid expira en {days} dias. "
            "Si no renueva, puede perder su cobertura de salud. "
            "Responda SI y le guiaremos paso a paso. "
            "Responda ALTO para cancelar."
        ),
    },
    "final_warning": {
        "en": (
            "FINAL NOTICE: {first_name}, your Medicaid coverage expires in {days} days. "
            "This is your last reminder. Reply YES now or call {fqhc_phone} immediately. "
            "Reply STOP to opt out."
        ),
        "es": (
            "AVISO FINAL: {first_name}, su cobertura de Medicaid expira en {days} dias. "
            "Este es su ultimo recordatorio. Responda SI ahora o llame al {fqhc_phone} inmediatamente. "
            "Responda ALTO para cancelar."
        ),
    },
    "doc_request": {
        "en": (
            "Hi {first_name}, to complete your Medicaid renewal we need: {doc_list}. "
            "You can take a photo and text it to this number. "
            "Questions? Reply HELP or call {fqhc_phone}. "
            "Reply STOP to opt out."
        ),
        "es": (
            "Hola {first_name}, para completar su renovacion de Medicaid necesitamos: {doc_list}. "
            "Puede tomar una foto y enviarla a este numero. "
            "Preguntas? Responda AYUDA o llame al {fqhc_phone}. "
            "Responda ALTO para cancelar."
        ),
    },
}

# Template sequence by risk tier
SEQUENCE_BY_TIER = {
    "low": ["initial_reminder", "second_reminder", "urgent_reminder", "final_warning"],
    "medium": ["initial_reminder", "second_reminder", "urgent_reminder", "final_warning"],
    "high": ["initial_reminder", "second_reminder", "urgent_reminder", "urgent_reminder", "final_warning", "final_warning"],
    "critical": ["initial_reminder", "urgent_reminder", "final_warning", "final_warning"],
}


class OutreachAgent:
    """Manage TCPA-compliant SMS outreach for renewal patients."""

    def check_can_send(self, patient: dict, renewal: dict) -> AgentResult:
        """TCPA compliance gate: consent, quiet hours, frequency caps, opt-out.

        Returns AgentResult with data.can_send (bool) and data.reason if blocked.
        """
        # Opt-out check
        consent = patient.get("consent_status", "pending")
        if consent == "opted_out":
            return AgentResult(
                success=True,
                data={"can_send": False, "reason": "Patient has opted out of SMS"},
            )

        # Consent required
        if consent != "opted_in":
            return AgentResult(
                success=True,
                data={"can_send": False, "reason": f"Consent status is '{consent}', requires 'opted_in'"},
            )

        # Quiet hours check
        now = datetime.now().time()
        quiet_start, quiet_end = QUIET_HOURS
        if now < time(quiet_start) or now >= time(quiet_end):
            return AgentResult(
                success=True,
                data={"can_send": False, "reason": f"Outside quiet hours ({quiet_start}:00-{quiet_end}:00)"},
            )

        # Frequency cap checks
        comm_log = renewal.get("communication_log", [])
        today = datetime.now().date()

        # Daily cap
        today_count = sum(
            1 for entry in comm_log
            if entry.get("type") == "sms"
            and _parse_date(entry.get("timestamp", "")) == today
        )
        if today_count >= SMS_FREQUENCY_CAP_DAILY:
            return AgentResult(
                success=True,
                data={"can_send": False, "reason": f"Daily SMS cap reached ({SMS_FREQUENCY_CAP_DAILY}/day)"},
            )

        # Weekly cap
        week_start = today.toordinal() - today.weekday()
        week_count = sum(
            1 for entry in comm_log
            if entry.get("type") == "sms"
            and _parse_date(entry.get("timestamp", "")).toordinal() >= week_start
        )
        if week_count >= SMS_FREQUENCY_CAP_WEEKLY:
            return AgentResult(
                success=True,
                data={"can_send": False, "reason": f"Weekly SMS cap reached ({SMS_FREQUENCY_CAP_WEEKLY}/week)"},
            )

        return AgentResult(success=True, data={"can_send": True, "reason": None})

    def select_message(
        self,
        patient: dict,
        renewal: dict,
        risk_tier: str,
        template_name: str | None = None,
        days_remaining: int | None = None,
        doc_list: str | None = None,
        fqhc_phone: str = "1-800-555-0199",
    ) -> AgentResult:
        """Pick the right message template based on state, risk, and language.

        Args:
            patient: Patient record with first_name, preferred_language.
            renewal: Renewal record with renewal_due_date, communication_log.
            risk_tier: Risk tier from RiskScoringAgent.
            template_name: Override template name. If None, auto-selects from sequence.
            days_remaining: Days until deadline. Auto-computed if None.
            doc_list: Comma-separated document list for doc_request template.
            fqhc_phone: FQHC phone number for templates.

        Returns:
            AgentResult with data containing template_name, language, message.
        """
        language = patient.get("preferred_language", "en")
        if language not in ("en", "es"):
            language = "en"  # Fallback to English

        # Auto-select template from sequence if not specified
        if not template_name:
            comm_log = renewal.get("communication_log", [])
            sms_count = sum(1 for e in comm_log if e.get("type") == "sms")
            sequence = SEQUENCE_BY_TIER.get(risk_tier, SEQUENCE_BY_TIER["low"])
            idx = min(sms_count, len(sequence) - 1)
            template_name = sequence[idx]

        template_set = TEMPLATES.get(template_name)
        if not template_set:
            return AgentResult(
                success=False,
                error=f"Unknown template: {template_name}",
            )

        template = template_set.get(language, template_set["en"])

        # Build template variables
        first_name = patient.get("first_name", "Patient")
        deadline = renewal.get("renewal_due_date", "")
        if days_remaining is None and deadline:
            from datetime import date as date_cls
            try:
                due = datetime.strptime(deadline, "%Y-%m-%d").date() if isinstance(deadline, str) else deadline
                days_remaining = (due - date_cls.today()).days
            except (ValueError, TypeError):
                days_remaining = 0

        message = template.format(
            first_name=first_name,
            deadline=deadline,
            days=days_remaining or 0,
            fqhc_phone=fqhc_phone,
            doc_list=doc_list or "",
        )

        return AgentResult(
            success=True,
            data={
                "template_name": template_name,
                "language": language,
                "message": message,
            },
            audit_log_entry={
                "actor": "outreach_agent",
                "action": "message_selected",
                "details": {"template": template_name, "language": language},
            },
        )

    def process_response(self, message: str) -> AgentResult:
        """Handle inbound patient response.

        Args:
            message: Raw inbound message text.

        Returns:
            AgentResult with data containing action and details.
        """
        normalized = message.strip().upper()

        if normalized in ("STOP", "ALTO", "UNSUBSCRIBE", "CANCEL"):
            return AgentResult(
                success=True,
                data={
                    "action": "opt_out",
                    "new_consent_status": "opted_out",
                    "details": "Patient requested opt-out via SMS",
                },
                audit_log_entry={
                    "actor": "outreach_agent",
                    "action": "opt_out_processed",
                    "details": {"raw_message": message},
                },
            )

        if normalized in ("YES", "SI", "Y", "1"):
            return AgentResult(
                success=True,
                data={
                    "action": "engaged",
                    "workflow_event": "patient_responded",
                    "details": "Patient responded affirmatively",
                },
            )

        if normalized in ("HELP", "AYUDA"):
            return AgentResult(
                success=True,
                data={
                    "action": "escalate",
                    "workflow_event": "help_requested",
                    "details": "Patient requested help — escalate to caseworker",
                },
            )

        return AgentResult(
            success=True,
            data={
                "action": "unrecognized",
                "details": f"Unrecognized response: {message[:100]}",
            },
        )

    @staticmethod
    def count_unanswered(communication_log: list) -> int:
        """Count consecutive unanswered SMS messages at the end of the log."""
        unanswered = 0
        for entry in reversed(communication_log):
            if entry.get("type") == "sms" and entry.get("direction") == "outbound":
                if entry.get("status") == "no_response":
                    unanswered += 1
                else:
                    break
            elif entry.get("type") == "sms" and entry.get("direction") == "inbound":
                break
        return unanswered

    def check_escalation(self, communication_log: list) -> AgentResult:
        """Check if escalation is needed based on unanswered messages.

        Returns:
            AgentResult with data containing needs_escalation, escalation_type.
        """
        unanswered = self.count_unanswered(communication_log)

        if unanswered >= 3:
            return AgentResult(
                success=True,
                data={
                    "needs_escalation": True,
                    "escalation_type": "phone_outreach",
                    "unanswered_count": unanswered,
                },
            )
        if unanswered >= 2:
            return AgentResult(
                success=True,
                data={
                    "needs_escalation": True,
                    "escalation_type": "caseworker_alert",
                    "unanswered_count": unanswered,
                },
            )

        return AgentResult(
            success=True,
            data={
                "needs_escalation": False,
                "escalation_type": None,
                "unanswered_count": unanswered,
            },
        )


def _parse_date(timestamp_str: str):
    """Parse a timestamp string to a date, returning epoch date on failure."""
    from datetime import date as date_cls
    if not timestamp_str:
        return date_cls(1970, 1, 1)
    try:
        return datetime.fromisoformat(timestamp_str).date()
    except (ValueError, TypeError):
        return date_cls(1970, 1, 1)
