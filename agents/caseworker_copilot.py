"""Caseworker Copilot Agent: dashboard summaries + recommendations.

LLM for patient timeline summarization and action recommendations.
Deterministic for alert generation and portfolio aggregation.
"""

import logging
from datetime import date, datetime

from openai import OpenAI

from agents.base import AgentResult

logger = logging.getLogger(__name__)


# Alert definitions: (alert_type, priority, check_function_name)
ALERT_DEFINITIONS = {
    "deadline_imminent": {
        "priority": "critical",
        "description": "Renewal deadline is within 7 days",
    },
    "no_response": {
        "priority": "high",
        "description": "Patient has not responded to 2+ outreach attempts",
    },
    "doc_validation_failed": {
        "priority": "high",
        "description": "Document rejected, needs caseworker review",
    },
    "dropped_off": {
        "priority": "medium",
        "description": "Patient was engaged but stopped responding for >7 days",
    },
    "first_renewal": {
        "priority": "medium",
        "description": "Patient's first renewal, may need extra support",
    },
}

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}



class CaseworkerCopilot:
    """Surface actionable insights for caseworkers managing renewal portfolios."""

    def __init__(self, openai_client: OpenAI):
        self.client = openai_client

    def get_portfolio_summary(self, renewals: list[dict]) -> AgentResult:
        """Aggregate stats: by tier, by state, completion rate.

        Args:
            renewals: List of renewal records with current_step, risk_tier.

        Returns:
            AgentResult with data containing counts by tier, state, and rates.
        """
        if not renewals:
            return AgentResult(
                success=True,
                data={
                    "total": 0,
                    "by_tier": {},
                    "by_state": {},
                    "completion_rate": 0.0,
                },
            )

        by_tier = {}
        by_state = {}
        completed = 0
        expired = 0

        for r in renewals:
            tier = r.get("risk_tier", "unknown")
            by_tier[tier] = by_tier.get(tier, 0) + 1

            step = r.get("current_step", "IDENTIFIED")
            by_state[step] = by_state.get(step, 0) + 1

            if step == "COMPLETED":
                completed += 1
            elif step == "EXPIRED":
                expired += 1

        total = len(renewals)
        finished = completed + expired
        completion_rate = round(completed / total * 100, 1) if total else 0.0

        return AgentResult(
            success=True,
            data={
                "total": total,
                "by_tier": by_tier,
                "by_state": by_state,
                "completed": completed,
                "expired": expired,
                "in_progress": total - finished,
                "completion_rate": completion_rate,
            },
        )

    def get_alerts(self, renewals: list[dict]) -> AgentResult:
        """Deterministic alert generation based on state + timing.

        Args:
            renewals: List of renewal records joined with patient data.

        Returns:
            AgentResult with data containing alerts list sorted by priority.
        """
        alerts = []
        today = date.today()

        for r in renewals:
            patient_id = r.get("patient_id")
            patient_name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip()
            step = r.get("current_step", "IDENTIFIED")

            # Skip terminal states
            if step in ("COMPLETED", "EXPIRED"):
                continue

            # Deadline imminent
            due_date = r.get("renewal_due_date")
            if due_date:
                if isinstance(due_date, str):
                    try:
                        due_date = datetime.strptime(due_date, "%Y-%m-%d").date()
                    except ValueError:
                        due_date = None
                if due_date:
                    days_left = (due_date - today).days
                    if days_left <= 7 and step != "SUBMISSION_READY":
                        alerts.append({
                            "type": "deadline_imminent",
                            "priority": "critical",
                            "patient_id": patient_id,
                            "patient_name": patient_name,
                            "details": f"Deadline in {days_left} days, currently at {step}",
                        })

            # No response
            if step == "NO_RESPONSE":
                comm_log = r.get("communication_log", [])
                unanswered = sum(
                    1 for e in comm_log
                    if e.get("type") == "sms" and e.get("status") == "no_response"
                )
                if unanswered >= 2:
                    alerts.append({
                        "type": "no_response",
                        "priority": "high",
                        "patient_id": patient_id,
                        "patient_name": patient_name,
                        "details": f"{unanswered} unanswered outreach attempts",
                    })

            # Document validation failed
            if step == "INVALID_DOC":
                alerts.append({
                    "type": "doc_validation_failed",
                    "priority": "high",
                    "patient_id": patient_id,
                    "patient_name": patient_name,
                    "details": "Document rejected, needs caseworker review",
                })

            # Dropped off
            if step == "DROPPED_OFF":
                alerts.append({
                    "type": "dropped_off",
                    "priority": "medium",
                    "patient_id": patient_id,
                    "patient_name": patient_name,
                    "details": "Patient was engaged but stopped responding",
                })

            # First renewal
            outcome = r.get("previous_renewal_outcome", "")
            if outcome == "first_renewal" and step not in ("COMPLETED", "EXPIRED"):
                alerts.append({
                    "type": "first_renewal",
                    "priority": "medium",
                    "patient_id": patient_id,
                    "patient_name": patient_name,
                    "details": "First renewal — may need extra support",
                })

        # Sort by priority
        alerts.sort(key=lambda a: PRIORITY_ORDER.get(a.get("priority", "low"), 3))

        return AgentResult(
            success=True,
            data={"alerts": alerts, "total": len(alerts)},
        )

    def process_override(
        self, renewal_id: int, override_data: dict
    ) -> AgentResult:
        """Record caseworker override decision.

        Args:
            renewal_id: ID of the renewal being overridden.
            override_data: Dict with caseworker, reason, new_state, etc.

        Returns:
            AgentResult confirming the override was recorded.
        """
        caseworker = override_data.get("caseworker", "unknown")
        reason = override_data.get("reason", "")
        new_state = override_data.get("new_state")

        if not reason:
            return AgentResult(
                success=False,
                error="Override requires a reason",
            )

        return AgentResult(
            success=True,
            data={
                "renewal_id": renewal_id,
                "override_accepted": True,
                "new_state": new_state,
                "caseworker": caseworker,
                "reason": reason,
            },
            audit_log_entry={
                "actor": caseworker,
                "action": "caseworker_override",
                "details": {
                    "renewal_id": renewal_id,
                    "reason": reason,
                    "new_state": new_state,
                },
            },
        )
