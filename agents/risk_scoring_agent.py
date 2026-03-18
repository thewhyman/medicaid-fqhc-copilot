"""Risk Scoring Agent: deterministic risk scoring for renewal patients.

No LLM — pure computation. Assigns priority 0.0-1.0 based on patient
demographics and renewal history to guide outreach intensity.
"""

import logging
from datetime import date, datetime

from agents.base import AgentResult
from config import RISK_TIERS

logger = logging.getLogger(__name__)


class RiskScoringAgent:
    """Compute risk scores for Medicaid renewal patients."""

    @staticmethod
    def _days_until(deadline: date | str) -> int:
        """Days from today to a deadline date."""
        if isinstance(deadline, str):
            deadline = datetime.strptime(deadline, "%Y-%m-%d").date()
        return (deadline - date.today()).days

    @staticmethod
    def _no_response_rate(response_history: list) -> float:
        """Fraction of outreach attempts with no response."""
        if not response_history:
            return 0.0
        no_response = sum(1 for r in response_history if r.get("status") == "no_response")
        return no_response / len(response_history)

    @staticmethod
    def _get_tier(score: float) -> str:
        """Map a score to a risk tier name."""
        for tier, (low, high) in RISK_TIERS.items():
            if low <= score <= high:
                return tier
        return "low"

    @staticmethod
    def _recommended_actions(tier: str) -> list[str]:
        """Return recommended outreach actions for a risk tier."""
        actions = {
            "critical": [
                "immediate_caseworker_escalation",
                "sms_outreach",
                "phone_outreach",
            ],
            "high": [
                "sms_outreach_sequence",
                "caseworker_alert",
            ],
            "medium": [
                "sms_reminder_sequence",
            ],
            "low": [
                "standard_reminder",
            ],
        }
        return actions.get(tier, ["standard_reminder"])

    def score(self, patient: dict, renewal: dict) -> AgentResult:
        """Compute risk score 0.0-1.0 from patient + renewal data.

        Args:
            patient: Patient record with age, household_size, preferred_language,
                     response_history, prior_doc_issues, contact_info_quality.
            renewal: Renewal record with renewal_due_date,
                     previous_renewal_outcome.

        Returns:
            AgentResult with data containing score, tier, factors,
            recommended_actions.
        """
        score = 0.0
        factors = []

        # 1. Deadline proximity (0-0.30)
        due_date = renewal.get("renewal_due_date")
        if due_date:
            days = self._days_until(due_date)
            if days <= 14:
                score += 0.30
                factors.append({"name": "deadline_proximity", "value": days, "weight": 0.30})
            elif days <= 30:
                score += 0.20
                factors.append({"name": "deadline_proximity", "value": days, "weight": 0.20})
            elif days <= 60:
                score += 0.10
                factors.append({"name": "deadline_proximity", "value": days, "weight": 0.10})

        # 2. Prior renewal history (0-0.25)
        outcome = renewal.get("previous_renewal_outcome", "")
        if outcome == "lapsed":
            score += 0.25
            factors.append({"name": "prior_lapsed", "value": outcome, "weight": 0.25})
        elif outcome == "first_renewal":
            score += 0.15
            factors.append({"name": "first_renewal", "value": outcome, "weight": 0.15})

        # 3. Response pattern (0-0.20)
        response_history = patient.get("response_history", [])
        nr_rate = self._no_response_rate(response_history)
        if nr_rate > 0.50:
            score += 0.20
            factors.append({"name": "no_response_rate", "value": round(nr_rate, 2), "weight": 0.20})
        elif nr_rate > 0.25:
            score += 0.10
            factors.append({"name": "no_response_rate", "value": round(nr_rate, 2), "weight": 0.10})

        # 4. Contact quality (0-0.10)
        contact_quality = patient.get("contact_info_quality", "verified")
        if contact_quality == "bounced":
            score += 0.10
            factors.append({"name": "contact_bounced", "value": contact_quality, "weight": 0.10})
        elif contact_quality == "unverified":
            score += 0.05
            factors.append({"name": "contact_unverified", "value": contact_quality, "weight": 0.05})

        # 5. Demographic complexity (0-0.15)
        age = patient.get("age", 0)
        if age >= 65:
            score += 0.05
            factors.append({"name": "elderly", "value": age, "weight": 0.05})

        language = patient.get("preferred_language", "en")
        if language != "en":
            score += 0.05
            factors.append({"name": "non_english", "value": language, "weight": 0.05})

        hh_size = patient.get("household_size", 1)
        if hh_size >= 5:
            score += 0.05
            factors.append({"name": "large_household", "value": hh_size, "weight": 0.05})

        # Cap at 1.0
        score = min(score, 1.0)
        score = round(score, 2)
        tier = self._get_tier(score)

        return AgentResult(
            success=True,
            data={
                "score": score,
                "tier": tier,
                "factors": factors,
                "recommended_actions": self._recommended_actions(tier),
            },
            audit_log_entry={
                "actor": "risk_scoring_agent",
                "action": "score_computed",
                "details": {"score": score, "tier": tier, "factor_count": len(factors)},
            },
        )
