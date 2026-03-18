"""Workflow Eval: verify state machine validity and transitions."""

from agents.base import EvalResult
from agents.workflow_orchestrator import TRANSITIONS, WorkflowOrchestrator


class WorkflowEval:
    """Eval agent for renewal workflow state machine correctness."""

    def __init__(self):
        self.orchestrator = WorkflowOrchestrator()

    def check_valid_transitions(self) -> EvalResult:
        """Verify all defined transitions produce valid next states."""
        from config import WORKFLOW_STATES
        failures = []
        for state, events in TRANSITIONS.items():
            if state not in WORKFLOW_STATES:
                failures.append(f"State '{state}' not in WORKFLOW_STATES")
            for event, next_state in events.items():
                if next_state not in WORKFLOW_STATES:
                    failures.append(f"{state} --{event}--> '{next_state}' not in WORKFLOW_STATES")

        return EvalResult(
            passed=len(failures) == 0,
            dimension="workflow_valid_transitions",
            details="; ".join(failures) if failures else "All transitions target valid states",
            data={"failures": failures},
        )

    def check_invalid_event_rejected(self) -> EvalResult:
        """Verify that invalid events for a state are rejected."""
        test_cases = [
            ("COMPLETED", "patient_responded"),
            ("EXPIRED", "all_docs_valid"),
            ("IDENTIFIED", "all_docs_valid"),
            ("SUBMISSION_READY", "timeout"),
        ]
        failures = []
        for state, event in test_cases:
            renewal = {"current_step": state}
            result = self.orchestrator.process_event(renewal, event)
            if result.success:
                failures.append(f"State '{state}' accepted invalid event '{event}'")

        return EvalResult(
            passed=len(failures) == 0,
            dimension="workflow_invalid_events",
            details="; ".join(failures) if failures else "All invalid events correctly rejected",
            data={"tested": len(test_cases), "failures": failures},
        )

    def check_terminal_states(self) -> EvalResult:
        """Verify COMPLETED and EXPIRED are terminal (no outgoing transitions)."""
        terminal = ["COMPLETED", "EXPIRED"]
        failures = []
        for state in terminal:
            events = TRANSITIONS.get(state, {})
            if events:
                failures.append(f"Terminal state '{state}' has transitions: {list(events.keys())}")

        return EvalResult(
            passed=len(failures) == 0,
            dimension="workflow_terminal_states",
            details="; ".join(failures) if failures else "Terminal states have no outgoing transitions",
            data={"failures": failures},
        )

    def check_recovery_paths(self) -> EvalResult:
        """Verify recovery states can transition back to main flow."""
        recovery_cases = [
            ("NO_RESPONSE", "escalation_sent", "NOTIFIED"),
            ("NO_RESPONSE", "patient_responded", "ENGAGED"),
            ("INVALID_DOC", "resubmission_requested", "DOC_COLLECTION"),
            ("DROPPED_OFF", "patient_re_engaged", "ENGAGED"),
        ]
        failures = []
        for state, event, expected_next in recovery_cases:
            renewal = {"current_step": state}
            result = self.orchestrator.process_event(renewal, event)
            if not result.success:
                failures.append(f"{state} --{event}--> failed: {result.error}")
            elif result.data["new_state"] != expected_next:
                failures.append(
                    f"{state} --{event}--> {result.data['new_state']} "
                    f"(expected {expected_next})"
                )

        return EvalResult(
            passed=len(failures) == 0,
            dimension="workflow_recovery_paths",
            details="; ".join(failures) if failures else "All recovery paths work correctly",
            data={"tested": len(recovery_cases), "failures": failures},
        )

    def check_happy_path(self) -> EvalResult:
        """Verify the complete happy path from IDENTIFIED to COMPLETED."""
        happy_path = [
            ("IDENTIFIED", "risk_scored", "NOTIFIED"),
            ("NOTIFIED", "patient_responded", "ENGAGED"),
            ("ENGAGED", "requirements_acknowledged", "DOC_COLLECTION"),
            ("DOC_COLLECTION", "all_docs_received", "VALIDATION"),
            ("VALIDATION", "all_docs_valid", "SUBMISSION_READY"),
            ("SUBMISSION_READY", "submitted_to_state", "COMPLETED"),
        ]
        failures = []
        for state, event, expected_next in happy_path:
            renewal = {"current_step": state}
            result = self.orchestrator.process_event(renewal, event)
            if not result.success:
                failures.append(f"{state} --{event}--> failed: {result.error}")
                break
            if result.data["new_state"] != expected_next:
                failures.append(
                    f"{state} --{event}--> {result.data['new_state']} "
                    f"(expected {expected_next})"
                )
                break

        return EvalResult(
            passed=len(failures) == 0,
            dimension="workflow_happy_path",
            details="; ".join(failures) if failures else "Happy path IDENTIFIED → COMPLETED works",
            data={"steps": len(happy_path), "failures": failures},
        )

    def check_audit_log_entries(self) -> EvalResult:
        """Verify every transition produces an audit log entry."""
        failures = []
        for state, events in TRANSITIONS.items():
            for event in events:
                renewal = {"current_step": state}
                result = self.orchestrator.process_event(renewal, event)
                if result.success and not result.audit_log_entry:
                    failures.append(f"{state} --{event}--> missing audit_log_entry")

        return EvalResult(
            passed=len(failures) == 0,
            dimension="workflow_audit_log",
            details="; ".join(failures) if failures else "All transitions produce audit entries",
            data={"failures": failures},
        )
