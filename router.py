"""Router: orchestrates all agents for Medicaid eligibility determination.

This is the only file that imports all agents. It provides the same public
interface as MedicaidAgent (agent.py) so server.py can swap with a single
import change.
"""

import json
import logging
import time
from collections.abc import AsyncGenerator

from openai import OpenAI

from agents.eligibility_agent import EligibilityAgent
from agents.eval_correctness import CorrectnessEval
from agents.eval_efficiency import EfficiencyEval
from agents.eval_quality import QualityEval
from agents.knowledge_agent import KnowledgeAgent
from agents.memory_agent import MemoryAgent
from mcp_manager import MCPManager
from prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class Router:
    def __init__(self):
        self.client = OpenAI()
        self.mcp = MCPManager()
        self.conversations: dict[str, list] = {}
        self._db_url: str | None = None
        self.last_query_metrics: dict = {}

        # Initialize agents
        self.memory_agent = MemoryAgent()
        self.knowledge_agent = KnowledgeAgent()
        self.eligibility_agent = EligibilityAgent(self.client, self.mcp)
        self.correctness_eval = CorrectnessEval()
        self.efficiency_eval = EfficiencyEval()
        self.quality_eval = QualityEval(self.client)

    # ------------------------------------------------------------------
    # Database / conversation persistence (same as MedicaidAgent)
    # ------------------------------------------------------------------

    def _get_db(self):
        import psycopg2
        conn = psycopg2.connect(self._db_url)
        conn.autocommit = False
        return conn

    def _ensure_table(self):
        """Create conversations table if it doesn't exist."""
        conn = self._get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        session_id TEXT PRIMARY KEY,
                        patient_id INTEGER,
                        messages JSONB NOT NULL DEFAULT '[]',
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
            conn.commit()
        finally:
            conn.close()

    def save_conversation(self, session_id: str, patient_id: int | None = None):
        """Persist a conversation to Postgres."""
        if not self._db_url:
            return
        messages = self.conversations.get(session_id, [])
        serializable = []
        for msg in messages:
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            if role in ("user", "assistant") and isinstance(content, str):
                serializable.append({"role": role, "content": content})
        conn = self._get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO conversations (session_id, patient_id, messages, updated_at)
                    VALUES (%s, %s, %s::jsonb, NOW())
                    ON CONFLICT (session_id) DO UPDATE
                    SET messages = EXCLUDED.messages, updated_at = NOW()
                """, (session_id, patient_id, json.dumps(serializable)))
            conn.commit()
        except Exception as e:
            logger.warning("Failed to save conversation %s: %s", session_id, e)
            conn.rollback()
        finally:
            conn.close()

    def load_conversation(self, session_id: str) -> list | None:
        """Load a conversation from Postgres. Returns None if not found."""
        if not self._db_url:
            return None
        conn = self._get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT messages FROM conversations WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
            if row and row[0]:
                return row[0] if isinstance(row[0], list) else json.loads(row[0])
        except Exception as e:
            logger.warning("Failed to load conversation %s: %s", session_id, e)
        finally:
            conn.close()
        return None

    def list_patient_sessions(self, patient_id: int) -> list[dict]:
        """List saved sessions for a patient."""
        if not self._db_url:
            return []
        conn = self._get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT session_id, updated_at FROM conversations WHERE patient_id = %s ORDER BY updated_at DESC",
                    (patient_id,),
                )
                return [{"session_id": r[0], "updated_at": str(r[1])} for r in cur.fetchall()]
        except Exception as e:
            logger.warning("Failed to list sessions for patient %s: %s", patient_id, e)
        finally:
            conn.close()
        return []

    # ------------------------------------------------------------------
    # Setup / cleanup (same interface as MedicaidAgent)
    # ------------------------------------------------------------------

    async def setup(self, db_url: str | None = None):
        """Connect to all MCP servers and set up conversation persistence."""
        if db_url:
            self._db_url = db_url
            self._ensure_table()
            logger.info("Conversation persistence enabled (Postgres)")
        logger.info("Connecting to MCP servers...")
        await self.mcp.connect_all()
        tool_names = [t["name"] for t in self.mcp.tools]
        logger.info("Ready. Available tools: %s", tool_names)

    async def cleanup(self):
        """Shut down all MCP connections."""
        await self.mcp.cleanup()

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def _init_session(self, session_id: str):
        """Load session from DB if not already in memory."""
        if session_id not in self.conversations:
            saved = self.load_conversation(session_id)
            self.conversations[session_id] = saved if saved else []

    @staticmethod
    def _extract_patient_id(session_id: str) -> int | None:
        """Extract patient ID from session_id like 'patient-5-1234567890'."""
        return MemoryAgent.extract_patient_id(session_id)

    # ------------------------------------------------------------------
    # Guardrail + QA orchestration
    # ------------------------------------------------------------------

    def _apply_guardrail_and_qa(
        self, messages: list, determination: str, api_calls: int
    ) -> tuple[str, dict | None, dict | None, int]:
        """Run guardrail check and QA review on a determination.

        Returns (possibly_corrected_text, guardrail_data, qa_data, updated_api_calls).
        """
        patient_record = EligibilityAgent.extract_patient_record(messages)
        if not patient_record or not determination:
            return determination, None, None, api_calls

        # Correctness eval (guardrail)
        correctness = self.correctness_eval.check(patient_record, determination)
        guardrail_data = correctness.data

        if not correctness.passed:
            correction = (
                f"\n\n---\n**Guardrail Correction**: The deterministic eligibility engine "
                f"produced a different result than the initial assessment.\n\n"
                f"{guardrail_data['engine_summary']}\n\n"
                f"The corrected determination has been applied."
            )
            determination += correction
            messages.append({"role": "assistant", "content": correction})
            logger.info("Guardrail correction appended to response")

        # Quality eval (QA review)
        qa_eval = self.quality_eval.run_qa_review(
            patient_record, determination, guardrail_data["engine_result"]
        )
        qa_data = qa_eval.data
        api_calls += 1

        return determination, guardrail_data, qa_data, api_calls

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _build_metrics(
        self,
        api_calls: int,
        tool_names: list[str],
        session_id: str,
        elapsed_ms: int,
        guardrail_data: dict | None,
        qa_data: dict | None,
        messages: list,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> dict:
        """Build the metrics dict used by the UI and evals."""
        patient_record = EligibilityAgent.extract_patient_record(messages)
        skipped_reason = None
        if not patient_record:
            skipped_reason = (
                "No patient record found in tool results — likely a Mem0 cached "
                "response or follow-up question. Guardrail and QA require a fresh DB query."
            )

        metrics = {
            "api_calls": api_calls,
            "tool_names": tool_names,
            "session_id": session_id,
            "latency_ms": elapsed_ms,
            "guardrail_match": guardrail_data["match"] if guardrail_data else None,
            "guardrail_details": {
                "engine_eligible": guardrail_data["engine_result"]["eligible"],
                "llm_eligible": guardrail_data["llm_eligible"],
                "category": guardrail_data["engine_result"]["category"],
                "income_pct": guardrail_data["engine_result"]["income_pct"],
                "threshold_pct": guardrail_data["engine_result"]["threshold_pct"],
                "threshold_amount": guardrail_data["engine_result"]["threshold_amount"],
                "fpl": guardrail_data["engine_result"]["fpl"],
                "expansion": guardrail_data["engine_result"]["expansion"],
                "reason": guardrail_data["engine_result"].get("reason", ""),
            } if guardrail_data else None,
            "qa_approved": qa_data.get("approved") if qa_data else None,
            "qa_issues": qa_data.get("issues") if qa_data else None,
            "qa_corrected_eligible": qa_data.get("corrected_eligible") if qa_data else None,
            "skipped_reason": skipped_reason,
        }
        if input_tokens or output_tokens:
            metrics["input_tokens"] = input_tokens
            metrics["output_tokens"] = output_tokens
            metrics["total_tokens"] = input_tokens + output_tokens

        return metrics

    # ------------------------------------------------------------------
    # Main query processing (same interface as MedicaidAgent)
    # ------------------------------------------------------------------

    async def process_query(self, query: str, session_id: str = "default") -> str:
        """Process a user query through the multi-agent pipeline."""
        self._init_session(session_id)
        messages = self.conversations[session_id]
        messages.append({"role": "user", "content": query})
        start_time = time.monotonic()

        # 1. Memory Agent: search for prior context
        mem_result = self.memory_agent.search(query, session_id)
        mem0_context = mem_result.data.get("context", "")
        mem0_user = mem_result.data.get("user", "")

        # 2. Build system prompt
        system_prompt = SYSTEM_PROMPT + mem0_context

        # 3. Eligibility Agent: ReAct loop
        result = await self.eligibility_agent.determine(query, system_prompt, messages)
        determination = result.data["determination"]
        api_calls = result.data["api_calls"]
        tool_names_used = result.data["tool_names"]
        input_tokens = result.data["input_tokens"]
        output_tokens = result.data["output_tokens"]

        # 4. Guardrail + QA
        determination, guardrail_data, qa_data, api_calls = self._apply_guardrail_and_qa(
            messages, determination, api_calls
        )

        # 5. Build metrics
        elapsed_ms = round((time.monotonic() - start_time) * 1000)
        self.last_query_metrics = self._build_metrics(
            api_calls, tool_names_used, session_id, elapsed_ms,
            guardrail_data, qa_data, messages,
            input_tokens, output_tokens,
        )
        logger.info(
            "Query completed: %d API calls, %d tokens, %dms, guardrail=%s, qa=%s for session %s",
            api_calls, input_tokens + output_tokens, elapsed_ms,
            guardrail_data["match"] if guardrail_data else "skipped",
            qa_data.get("approved") if qa_data else "skipped",
            session_id,
        )

        # 6. Memory Agent: save
        self.memory_agent.save(query, determination, mem0_user)

        # 7. Persist conversation
        self.save_conversation(session_id, self._extract_patient_id(session_id))

        return determination

    async def process_query_stream(
        self, query: str, session_id: str = "default"
    ) -> AsyncGenerator[str, None]:
        """Process a query and yield text chunks as they arrive."""
        self._init_session(session_id)
        messages = self.conversations[session_id]
        messages.append({"role": "user", "content": query})
        start_time = time.monotonic()

        # 1. Memory Agent: search
        mem_result = self.memory_agent.search(query, session_id)
        mem0_context = mem_result.data.get("context", "")
        mem0_user = mem_result.data.get("user", "")

        # 2. Build system prompt
        system_prompt = SYSTEM_PROMPT + mem0_context

        # 3. Eligibility Agent: streaming ReAct loop
        async for chunk in self.eligibility_agent.determine_stream(
            query, system_prompt, messages
        ):
            yield chunk

        # 4. Post-stream: guardrail + QA
        result = self.eligibility_agent.last_result
        determination = result.data["determination"] if result else ""
        api_calls = result.data["api_calls"] if result else 0
        tool_names_used = result.data["tool_names"] if result else []

        determination, guardrail_data, qa_data, api_calls = self._apply_guardrail_and_qa(
            messages, determination, api_calls
        )

        # Stream guardrail correction if one was appended
        if guardrail_data and not guardrail_data["match"]:
            correction = messages[-1].get("content", "")
            if correction:
                yield correction

        # 5. Build metrics
        elapsed_ms = round((time.monotonic() - start_time) * 1000)
        self.last_query_metrics = self._build_metrics(
            api_calls, tool_names_used, session_id, elapsed_ms,
            guardrail_data, qa_data, messages,
        )
        logger.info(
            "Stream completed: %d API calls, %dms, guardrail=%s, qa=%s for session %s",
            api_calls, elapsed_ms,
            guardrail_data["match"] if guardrail_data else "skipped",
            qa_data.get("approved") if qa_data else "skipped",
            session_id,
        )

        # 6. Memory Agent: save
        self.memory_agent.save(query, determination, mem0_user)

        # 7. Persist conversation
        self.save_conversation(session_id, self._extract_patient_id(session_id))
