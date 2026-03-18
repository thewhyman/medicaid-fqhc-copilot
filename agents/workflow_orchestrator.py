"""Workflow Orchestrator: renewal state machine for Medicaid recertification.

No LLM — deterministic state transitions. Manages the lifecycle of each
patient's renewal journey and triggers agent actions on transitions.
"""

import logging
from datetime import date, datetime

from agents.base import AgentResult
from config import WORKFLOW_STATES, WORKFLOW_TIMEOUTS

logger = logging.getLogger(__name__)


# Valid state transitions: current_state → {event → next_state}
TRANSITIONS = {
    "IDENTIFIED": {
        "risk_scored": "NOTIFIED",
        "manual_add": "NOTIFIED",
    },
    "NOTIFIED": {
        "patient_responded": "ENGAGED",
        "timeout": "NO_RESPONSE",
    },
    "ENGAGED": {
        "requirements_acknowledged": "DOC_COLLECTION",
        "timeout": "DROPPED_OFF",
    },
    "DOC_COLLECTION": {
        "all_docs_received": "VALIDATION",
        "timeout": "DOC_COLLECTION",  # Stays in same state, triggers reminder
    },
    "VALIDATION": {
        "all_docs_valid": "SUBMISSION_READY",
        "doc_invalid": "INVALID_DOC",
    },
    "SUBMISSION_READY": {
        "submitted_to_state": "COMPLETED",
    },
    "COMPLETED": {},  # Terminal
    "EXPIRED": {},  # Terminal
    # Recovery states
    "NO_RESPONSE": {
        "escalation_sent": "NOTIFIED",
        "patient_responded": "ENGAGED",
        "deadline_passed": "EXPIRED",
    },
    "INVALID_DOC": {
        "resubmission_requested": "DOC_COLLECTION",
        "caseworker_override": "SUBMISSION_READY",
    },
    "DROPPED_OFF": {
        "patient_re_engaged": "ENGAGED",
        "escalation_sent": "NOTIFIED",
        "deadline_passed": "EXPIRED",
    },
}

# Actions triggered by state transitions
TRANSITION_ACTIONS = {
    ("IDENTIFIED", "NOTIFIED"): ["start_outreach_sequence", "send_initial_reminder"],
    ("NOTIFIED", "ENGAGED"): ["identify_required_documents", "send_document_checklist"],
    ("NOTIFIED", "NO_RESPONSE"): ["escalate_outreach", "alert_caseworker"],
    ("ENGAGED", "DOC_COLLECTION"): ["send_document_checklist"],
    ("ENGAGED", "DROPPED_OFF"): ["intensify_outreach", "alert_caseworker"],
    ("DOC_COLLECTION", "VALIDATION"): ["validate_all_documents"],
    ("DOC_COLLECTION", "DOC_COLLECTION"): ["send_document_reminder"],
    ("VALIDATION", "SUBMISSION_READY"): ["assemble_renewal_packet", "notify_caseworker"],
    ("VALIDATION", "INVALID_DOC"): ["request_resubmission", "notify_patient_doc_issue"],
    ("SUBMISSION_READY", "COMPLETED"): ["notify_patient_completed", "notify_caseworker_completed"],
    ("NO_RESPONSE", "NOTIFIED"): ["retry_outreach"],
    ("NO_RESPONSE", "ENGAGED"): ["identify_required_documents"],
    ("NO_RESPONSE", "EXPIRED"): ["flag_for_re_enrollment"],
    ("INVALID_DOC", "DOC_COLLECTION"): ["send_resubmission_guidance"],
    ("INVALID_DOC", "SUBMISSION_READY"): ["log_caseworker_override"],
    ("DROPPED_OFF", "ENGAGED"): ["send_re_engagement_message"],
    ("DROPPED_OFF", "NOTIFIED"): ["retry_outreach"],
    ("DROPPED_OFF", "EXPIRED"): ["flag_for_re_enrollment"],
}

# Required documents by patient category
REQUIRED_DOCUMENTS = {
    "adult": ["pay_stub", "utility_bill"],
    "child": ["birth_certificate", "pay_stub"],
    "pregnant": ["pregnancy_verification", "pay_stub"],
    "elderly": ["ssa_benefit_letter", "utility_bill"],
    "disabled": ["ssa_benefit_letter", "utility_bill"],
}


class WorkflowOrchestrator:
    """Manage the renewal state machine for each patient."""

    def process_event(
        self, renewal: dict, event: str, event_data: dict | None = None
    ) -> AgentResult:
        """Central event processor. Validates transition and returns new state + actions.

        Args:
            renewal: Current renewal record with current_step, renewal_due_date, etc.
            event: Event name triggering the transition.
            event_data: Optional data associated with the event.

        Returns:
            AgentResult with data containing new_state, actions, previous_state.
        """
        current_state = renewal.get("current_step", "IDENTIFIED")

        if current_state not in TRANSITIONS:
            return AgentResult(
                success=False,
                error=f"Unknown state: {current_state}",
            )

        valid_events = TRANSITIONS[current_state]
        if event not in valid_events:
            return AgentResult(
                success=False,
                error=f"Invalid event '{event}' for state '{current_state}'. "
                      f"Valid events: {list(valid_events.keys())}",
            )

        new_state = valid_events[event]
        actions = TRANSITION_ACTIONS.get((current_state, new_state), [])

        return AgentResult(
            success=True,
            data={
                "previous_state": current_state,
                "new_state": new_state,
                "event": event,
                "actions": actions,
                "event_data": event_data or {},
            },
            audit_log_entry={
                "actor": "workflow_orchestrator",
                "action": "state_transition",
                "details": {
                    "from_state": current_state,
                    "to_state": new_state,
                    "event": event,
                },
            },
        )

    def check_timeouts(self, renewal: dict) -> AgentResult:
        """Check if renewal has timed out in current state.

        Args:
            renewal: Renewal record with current_step, updated_at.

        Returns:
            AgentResult with data containing is_timed_out, timeout_event,
            days_in_state.
        """
        current_state = renewal.get("current_step", "IDENTIFIED")
        timeout_days = WORKFLOW_TIMEOUTS.get(current_state)

        if not timeout_days:
            return AgentResult(
                success=True,
                data={"is_timed_out": False, "timeout_event": None, "days_in_state": 0},
            )

        updated_at = renewal.get("updated_at")
        if not updated_at:
            return AgentResult(
                success=True,
                data={"is_timed_out": False, "timeout_event": None, "days_in_state": 0},
            )

        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at)
            except ValueError:
                return AgentResult(
                    success=True,
                    data={"is_timed_out": False, "timeout_event": None, "days_in_state": 0},
                )

        if hasattr(updated_at, "date"):
            days_in_state = (date.today() - updated_at.date()).days
        else:
            days_in_state = (date.today() - updated_at).days

        is_timed_out = days_in_state >= timeout_days

        # Also check if deadline has passed
        due_date = renewal.get("renewal_due_date")
        deadline_passed = False
        if due_date:
            if isinstance(due_date, str):
                try:
                    due_date = datetime.strptime(due_date, "%Y-%m-%d").date()
                except ValueError:
                    due_date = None
            if due_date and date.today() > due_date:
                deadline_passed = True

        if deadline_passed and current_state not in ("COMPLETED", "EXPIRED", "SUBMISSION_READY"):
            return AgentResult(
                success=True,
                data={
                    "is_timed_out": True,
                    "timeout_event": "deadline_passed",
                    "days_in_state": days_in_state,
                },
            )

        return AgentResult(
            success=True,
            data={
                "is_timed_out": is_timed_out,
                "timeout_event": "timeout" if is_timed_out else None,
                "days_in_state": days_in_state,
            },
        )

    @staticmethod
    def get_required_documents(patient: dict) -> list[str]:
        """Determine docs needed based on patient category.

        Args:
            patient: Patient record with age, is_pregnant, has_disability.

        Returns:
            List of required document type names.
        """
        from eligibility import determine_category
        category = determine_category(patient)
        return REQUIRED_DOCUMENTS.get(category, REQUIRED_DOCUMENTS["adult"])

    @staticmethod
    def is_terminal_state(state: str) -> bool:
        """Check if a state is terminal (no further transitions)."""
        return state in ("COMPLETED", "EXPIRED")

    @staticmethod
    def get_valid_events(state: str) -> list[str]:
        """Get list of valid events for a given state."""
        return list(TRANSITIONS.get(state, {}).keys())
