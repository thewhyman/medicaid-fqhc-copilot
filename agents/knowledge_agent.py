"""Knowledge Agent: FPL tables and state rule lookups."""

from agents.base import AgentResult
from eligibility import determine_category, get_fpl
from prompts import STATE_THRESHOLDS


class KnowledgeAgent:
    def get_patient_rules(self, patient: dict) -> AgentResult:
        """Look up FPL and state threshold for a patient record.

        Returns AgentResult with data containing FPL, category,
        threshold percentage, threshold amount, and expansion status.
        """
        state = patient.get("state", "")
        hh_size = patient.get("household_size", 1)
        category = determine_category(patient)
        fpl = get_fpl(state, hh_size)

        thresholds = STATE_THRESHOLDS.get(state)
        if not thresholds:
            return AgentResult(
                success=False,
                error=f"State '{state}' not found in threshold data",
                data={"fpl": fpl, "category": category},
            )

        if category == "child":
            threshold_pct = thresholds["child_pct"]
        elif category == "pregnant":
            threshold_pct = thresholds["pregnant_pct"]
        else:
            threshold_pct = thresholds["adult_pct"]

        threshold_amount = fpl * threshold_pct / 100

        return AgentResult(
            success=True,
            data={
                "fpl": fpl,
                "category": category,
                "threshold_pct": threshold_pct,
                "threshold_amount": round(threshold_amount, 2),
                "expansion": thresholds["expansion"],
                "state": state,
            },
        )

    def get_state_info(self, state: str) -> AgentResult:
        """Return threshold data for a state."""
        thresholds = STATE_THRESHOLDS.get(state)
        if not thresholds:
            return AgentResult(success=False, error=f"State '{state}' not found")
        return AgentResult(success=True, data={"state": state, **thresholds})
