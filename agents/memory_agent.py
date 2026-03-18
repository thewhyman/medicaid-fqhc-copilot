"""Memory Agent: Mem0 search/save for patient determination history."""

import logging
import os

from dotenv import load_dotenv
from mem0 import MemoryClient

from agents.base import AgentResult

load_dotenv()

logger = logging.getLogger(__name__)

_mem0_api_key = os.environ.get("MEM0_API_KEY", "")


class MemoryAgent:
    def __init__(self):
        self._client = MemoryClient(api_key=_mem0_api_key) if _mem0_api_key else None

    @staticmethod
    def extract_patient_id(session_id: str) -> int | None:
        """Extract patient ID from session_id like 'patient-5-1234567890'."""
        if session_id.startswith("patient-"):
            parts = session_id.split("-")
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        return None

    def search(self, query: str, session_id: str) -> AgentResult:
        """Search Mem0 for prior determinations.

        Returns AgentResult with data={"context": str, "user": str}.
        Degrades gracefully — never crashes on failure.
        """
        patient_id = self.extract_patient_id(session_id)
        mem0_user = f"patient-{patient_id}" if patient_id else "medicaid-copilot"

        if not self._client:
            return AgentResult(success=True, data={"context": "", "user": mem0_user})

        try:
            result = self._client.search(query, filters={"user_id": mem0_user})
            memories = result.get("results", []) if isinstance(result, dict) else result
            if memories:
                mem_texts = [m.get("memory", "") for m in memories[:3] if m.get("memory")]
                if mem_texts:
                    context = (
                        "\n\n## PRIOR DETERMINATIONS FROM MEMORY\n"
                        + "\n".join(f"- {t}" for t in mem_texts)
                    )
                    logger.info("Mem0 returned %d memories for %s", len(mem_texts), mem0_user)
                    return AgentResult(success=True, data={"context": context, "user": mem0_user})
        except Exception as e:
            logger.warning("Mem0 search failed: %s", e)

        return AgentResult(success=True, data={"context": "", "user": mem0_user})

    def save(self, query: str, determination: str, mem0_user: str) -> AgentResult:
        """Save determination to Mem0."""
        if not self._client or not determination:
            return AgentResult(success=False, error="No Mem0 client or empty determination")

        try:
            self._client.add(
                f"Query: {query}\nDetermination: {determination[:500]}",
                user_id=mem0_user,
            )
            logger.info("Saved determination to Mem0")
            return AgentResult(success=True)
        except Exception as e:
            logger.warning("Mem0 save failed: %s", e)
            return AgentResult(success=False, error=str(e))
