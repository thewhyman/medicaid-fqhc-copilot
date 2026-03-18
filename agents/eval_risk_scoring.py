"""Risk Scoring Eval: verify determinism and tier correctness."""

from agents.base import EvalResult
from agents.risk_scoring_agent import RiskScoringAgent
from config import RISK_TIERS


class RiskScoringEval:
    """Eval agent for risk scoring determinism and correctness."""

    def __init__(self):
        self.agent = RiskScoringAgent()

    def check_determinism(self, patient: dict, renewal: dict, runs: int = 3) -> EvalResult:
        """Verify that the same inputs produce the same score every time."""
        scores = []
        for _ in range(runs):
            result = self.agent.score(patient, renewal)
            scores.append(result.data["score"])

        is_deterministic = len(set(scores)) == 1
        return EvalResult(
            passed=is_deterministic,
            dimension="risk_scoring_determinism",
            details=f"Scores across {runs} runs: {scores}",
            data={"scores": scores, "is_deterministic": is_deterministic},
        )

    def check_tier_assignment(self, score: float, expected_tier: str) -> EvalResult:
        """Verify that a score maps to the correct risk tier."""
        actual_tier = RiskScoringAgent._get_tier(score)
        match = actual_tier == expected_tier
        return EvalResult(
            passed=match,
            dimension="risk_tier_assignment",
            details=f"Score {score} → tier '{actual_tier}' (expected '{expected_tier}')",
            data={"score": score, "actual_tier": actual_tier, "expected_tier": expected_tier},
        )

    def check_score_range(self, patient: dict, renewal: dict) -> EvalResult:
        """Verify that score is within valid range [0.0, 1.0]."""
        result = self.agent.score(patient, renewal)
        score = result.data["score"]
        in_range = 0.0 <= score <= 1.0
        return EvalResult(
            passed=in_range,
            dimension="risk_score_range",
            details=f"Score: {score} (valid: {in_range})",
            data={"score": score, "in_range": in_range},
        )

    def check_tier_boundaries(self) -> EvalResult:
        """Verify tier boundaries are correct for edge cases."""
        test_cases = [
            (0.0, "low"),
            (0.19, "low"),
            (0.20, "medium"),
            (0.39, "medium"),
            (0.40, "high"),
            (0.69, "high"),
            (0.70, "critical"),
            (1.0, "critical"),
        ]
        failures = []
        for score, expected in test_cases:
            actual = RiskScoringAgent._get_tier(score)
            if actual != expected:
                failures.append(f"Score {score}: expected '{expected}', got '{actual}'")

        return EvalResult(
            passed=len(failures) == 0,
            dimension="risk_tier_boundaries",
            details="; ".join(failures) if failures else "All boundary checks passed",
            data={"test_cases": len(test_cases), "failures": failures},
        )
