"""Quality Eval Agent: LLM-based QA review and keyword checking."""

import json
import logging

from openai import OpenAI

from agents.base import EvalResult
from config import MODEL
from eligibility import format_determination_summary
from prompts import QA_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Required keywords per patient — imported from evals for consistency
# Each keyword list uses alternatives (any match counts).
REQUIRED_KEYWORDS = {
    1: {"keyword_groups": [["pregnant", "pregnancy"], ["213", "213%"]], "state_alts": ["CA", "california"]},
    2: {"keyword_groups": [["non-expansion", "not expanded", "has not expanded"], ["14%", "14 %", "14 percent"]], "state_alts": ["TX", "texas"]},
    3: {"keyword_groups": [["child", "children", "minor", "under 19"]], "state_alts": ["FL", "florida"]},
    4: {"keyword_groups": [["elderly", "aged", "senior", "65", "over 64"]], "state_alts": ["NY", "new york"]},
    5: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["OH", "ohio"]},
    6: {"keyword_groups": [["disab", "disability", "disabled", "ssi"]], "state_alts": ["GA", "georgia"]},
    7: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["WA", "washington"]},
    8: {"keyword_groups": [["non-expansion", "not expanded", "has not expanded"], ["18%", "18 %", "18 percent"]], "state_alts": ["AL", "alabama"]},
    9: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["CA", "california"]},
    10: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["OH", "ohio"]},
    11: {"keyword_groups": [["citizen", "citizenship", "immigration", "non-citizen"]], "state_alts": ["NY", "new york"]},
    12: {"keyword_groups": [["adult", "18"]], "state_alts": ["FL", "florida"]},
    13: {"keyword_groups": [["elderly", "aged", "senior", "65"]], "state_alts": ["TX", "texas"]},
    14: {"keyword_groups": [["pregnant", "pregnancy"], ["220", "220%"]], "state_alts": ["GA", "georgia"]},
    15: {"keyword_groups": [["adult", "alaska"], ["138%", "138 %", "138 percent"]], "state_alts": ["AK", "alaska"]},
    16: {"keyword_groups": [["adult"], ["138%", "138 %", "138 percent"]], "state_alts": ["HI", "hawaii"]},
}


class QualityEval:
    def __init__(self, openai_client: OpenAI):
        self.client = openai_client

    def run_qa_review(
        self, patient: dict, determination: str, engine_result: dict
    ) -> EvalResult:
        """QA agent: second LLM pass reviewing the determination for errors.

        Uses the deterministic engine result as ground truth context.
        Returns EvalResult with data={"approved": bool, "issues": list, "corrected_eligible": bool}.
        """
        engine_summary = format_determination_summary(patient, engine_result)
        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": QA_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"## Patient Record\n{json.dumps(patient, default=str)}\n\n"
                            f"## Deterministic Engine Result\n{engine_summary}\n\n"
                            f"## Agent Determination\n{determination[:2000]}"
                        ),
                    },
                ],
            )
            qa_text = response.choices[0].message.content or ""
            # Strip markdown code fences if present
            qa_text = qa_text.strip()
            if qa_text.startswith("```"):
                qa_text = qa_text.split("\n", 1)[-1]
            if qa_text.endswith("```"):
                qa_text = qa_text.rsplit("```", 1)[0]
            qa_result = json.loads(qa_text.strip())
            logger.info(
                "QA review: approved=%s issues=%s",
                qa_result.get("approved"),
                qa_result.get("issues"),
            )
            return EvalResult(
                passed=qa_result.get("approved", False),
                dimension="quality",
                details=str(qa_result.get("issues", [])),
                data=qa_result,
            )
        except Exception as e:
            logger.warning("QA review failed: %s", e)
            return EvalResult(
                passed=False,
                dimension="quality",
                details=f"QA review failed: {e}",
                data={},
            )

    @staticmethod
    def check_keywords(patient_id: int, response: str) -> EvalResult:
        """Check agent response contains required keywords.

        Returns EvalResult with pass/fail and list of issues.
        """
        issues = []
        reqs = REQUIRED_KEYWORDS.get(patient_id)
        if not reqs:
            return EvalResult(passed=True, dimension="quality", details="No keywords required")

        text_lower = response.lower()

        # Check keyword groups — at least one alternative in each group must appear
        for group in reqs["keyword_groups"]:
            if not any(alt.lower() in text_lower for alt in group):
                issues.append(f"missing one of {group}")

        # Check state — accept any alternative
        if not any(alt.lower() in text_lower for alt in reqs["state_alts"]):
            issues.append(f"missing state {reqs['state_alts']}")

        return EvalResult(
            passed=len(issues) == 0,
            dimension="quality",
            details="; ".join(issues) if issues else "OK",
            data={"issues": issues},
        )
