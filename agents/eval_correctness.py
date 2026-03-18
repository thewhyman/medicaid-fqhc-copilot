"""Correctness Eval Agent: compare LLM determination against deterministic engine."""

import logging

from agents.base import EvalResult
from eligibility import (
    compute_eligibility,
    format_determination_summary,
    parse_determination,
)

logger = logging.getLogger(__name__)


class CorrectnessEval:
    def check(self, patient: dict, determination: str) -> EvalResult:
        """Compare LLM determination against deterministic engine.

        Returns EvalResult with data containing:
        - match: bool
        - engine_result: dict
        - engine_summary: str
        - llm_eligible: bool | None
        """
        engine_result = compute_eligibility(patient)
        engine_summary = format_determination_summary(patient, engine_result)
        llm_eligible = parse_determination(determination)

        # If ambiguous (disabled/elderly in non-expansion), accept either answer
        if engine_result["ambiguous"]:
            return EvalResult(
                passed=True,
                dimension="correctness",
                details="Ambiguous case — accepting either answer",
                data={
                    "match": True,
                    "engine_result": engine_result,
                    "engine_summary": engine_summary,
                    "llm_eligible": llm_eligible,
                },
            )

        match = llm_eligible == engine_result["eligible"]
        if not match:
            logger.warning(
                "GUARDRAIL MISMATCH: LLM said %s, engine says %s for patient %s %s",
                llm_eligible,
                engine_result["eligible"],
                patient.get("first_name", "?"),
                patient.get("last_name", "?"),
            )

        return EvalResult(
            passed=match,
            dimension="correctness",
            details=f"LLM={llm_eligible}, engine={engine_result['eligible']}",
            data={
                "match": match,
                "engine_result": engine_result,
                "engine_summary": engine_summary,
                "llm_eligible": llm_eligible,
            },
        )
