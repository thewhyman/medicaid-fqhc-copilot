"""Core Medicaid Eligibility Agent with agentic tool-use loop.

This module contains the agent logic that can be used by both the CLI
and the FastAPI server.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from mem0 import MemoryClient
from openai import OpenAI

from config import MAX_AGENT_ITERATIONS, MAX_TOOL_RESULT_LENGTH, MODEL
from eligibility import (
    compute_eligibility,
    format_determination_summary,
    parse_determination,
)
from mcp_manager import MCPManager
from prompts import QA_SYSTEM_PROMPT, SYSTEM_PROMPT

load_dotenv()

logger = logging.getLogger(__name__)

# Mem0 SDK client for storing/retrieving determination history
_mem0_api_key = os.environ.get("MEM0_API_KEY", "")
mem0 = MemoryClient(api_key=_mem0_api_key) if _mem0_api_key else None


def _convert_tools(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool dicts to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in mcp_tools
    ]


class MedicaidAgent:
    def __init__(self):
        self.client = OpenAI()
        self.mcp = MCPManager()
        self.conversations: dict[str, list] = {}  # session_id -> messages
        self._db_url = None  # set during setup for persistence
        self.last_query_metrics: dict = {}  # api_calls, tool_names for evals

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
        # Filter to only serializable message types (user, assistant text)
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

    def _extract_patient_id(self, session_id: str) -> int | None:
        """Extract patient ID from session_id like 'patient-5-1234567890'."""
        if session_id.startswith("patient-"):
            parts = session_id.split("-")
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        return None

    def _init_session(self, session_id: str):
        """Load session from DB if not already in memory."""
        if session_id not in self.conversations:
            saved = self.load_conversation(session_id)
            self.conversations[session_id] = saved if saved else []

    def _extract_patient_record(self, messages: list) -> dict | None:
        """Extract patient record from tool call results in the conversation.

        Looks for a JSON-like patient record returned by the Postgres MCP
        tool call. Returns parsed dict or None.
        """
        for msg in messages:
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if msg.get("role") != "tool" or not content:
                continue
            # Look for patient-like data with key fields
            if "annual_income" in content and "state" in content:
                # Try to parse as JSON array (MCP postgres returns rows)
                try:
                    rows = json.loads(content)
                    if isinstance(rows, list) and rows:
                        return rows[0]
                    if isinstance(rows, dict):
                        return rows
                except (json.JSONDecodeError, TypeError):
                    pass
                # Try to find JSON object in the text
                match = re.search(r'\{[^{}]*"annual_income"[^{}]*\}', content)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        pass
        return None

    def _run_guardrail(self, patient: dict, llm_text: str) -> dict:
        """Layer 4 guardrail: compare LLM determination against deterministic engine.

        Returns dict with keys:
          - match (bool): whether LLM and engine agree
          - engine_result (dict): deterministic eligibility result
          - engine_summary (str): formatted summary
          - llm_eligible (bool|None): what the LLM said
        """
        engine_result = compute_eligibility(patient)
        engine_summary = format_determination_summary(patient, engine_result)
        llm_eligible = parse_determination(llm_text)

        # If ambiguous (disabled/elderly in non-expansion), accept either answer
        if engine_result["ambiguous"]:
            return {
                "match": True,
                "engine_result": engine_result,
                "engine_summary": engine_summary,
                "llm_eligible": llm_eligible,
            }

        match = llm_eligible == engine_result["eligible"]
        if not match:
            logger.warning(
                "GUARDRAIL MISMATCH: LLM said %s, engine says %s for patient %s %s",
                llm_eligible,
                engine_result["eligible"],
                patient.get("first_name", "?"),
                patient.get("last_name", "?"),
            )

        return {
            "match": match,
            "engine_result": engine_result,
            "engine_summary": engine_summary,
            "llm_eligible": llm_eligible,
        }

    def _run_qa_review(self, patient: dict, determination: str, engine_result: dict) -> dict | None:
        """QA agent: second LLM pass reviewing the determination for errors.

        Uses the deterministic engine result as ground truth context.
        Returns parsed QA response or None on failure.
        """
        engine_summary = format_determination_summary(patient, engine_result)
        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": QA_SYSTEM_PROMPT},
                    {"role": "user", "content": (
                        f"## Patient Record\n{json.dumps(patient, default=str)}\n\n"
                        f"## Deterministic Engine Result\n{engine_summary}\n\n"
                        f"## Agent Determination\n{determination[:2000]}"
                    )},
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
            logger.info("QA review: approved=%s issues=%s", qa_result.get("approved"), qa_result.get("issues"))
            return qa_result
        except Exception as e:
            logger.warning("QA review failed: %s", e)
            return None

    def _get_mem0_context(self, query: str, session_id: str) -> tuple[str, str]:
        """Search Mem0 for prior determinations. Returns (mem0_context, mem0_user)."""
        patient_id = self._extract_patient_id(session_id)
        mem0_user = f"patient-{patient_id}" if patient_id else "medicaid-copilot"
        if not mem0:
            return "", mem0_user
        try:
            result = mem0.search(query, filters={"user_id": mem0_user})
            memories = result.get("results", []) if isinstance(result, dict) else result
            if memories:
                mem_texts = [m.get("memory", "") for m in memories[:3] if m.get("memory")]
                if mem_texts:
                    context = "\n\n## PRIOR DETERMINATIONS FROM MEMORY\n" + "\n".join(f"- {t}" for t in mem_texts)
                    logger.info("Mem0 returned %d memories for %s", len(mem_texts), mem0_user)
                    return context, mem0_user
        except Exception as e:
            logger.warning("Mem0 search failed: %s", e)
        return "", mem0_user

    def _save_mem0(self, query: str, determination: str, mem0_user: str):
        """Save determination to Mem0."""
        if mem0 and determination:
            try:
                mem0.add(f"Query: {query}\nDetermination: {determination[:500]}", user_id=mem0_user)
                logger.info("Saved determination to Mem0")
            except Exception as e:
                logger.warning("Mem0 save failed: %s", e)

    def _apply_guardrail_and_qa(
        self, messages: list, determination: str, api_calls: int
    ) -> tuple[str, dict | None, dict | None, int]:
        """Run guardrail check and QA review on a determination.

        Returns (possibly_corrected_text, guardrail_result, qa_result, updated_api_calls).
        """
        patient_record = self._extract_patient_record(messages)
        if not patient_record or not determination:
            return determination, None, None, api_calls

        guardrail_result = self._run_guardrail(patient_record, determination)

        if not guardrail_result["match"]:
            correction = (
                f"\n\n---\n**Guardrail Correction**: The deterministic eligibility engine "
                f"produced a different result than the initial assessment.\n\n"
                f"{guardrail_result['engine_summary']}\n\n"
                f"The corrected determination has been applied."
            )
            determination += correction
            messages.append({"role": "assistant", "content": correction})
            logger.info("Guardrail correction appended to response")

        qa_result = self._run_qa_review(
            patient_record, determination, guardrail_result["engine_result"]
        )
        api_calls += 1

        return determination, guardrail_result, qa_result, api_calls

    def _build_metrics(
        self,
        api_calls: int,
        tool_names: list[str],
        session_id: str,
        elapsed_ms: int,
        guardrail_result: dict | None,
        qa_result: dict | None,
        messages: list,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> dict:
        """Build the metrics dict used by the UI and evals."""
        patient_record = self._extract_patient_record(messages)
        skipped_reason = None
        if not patient_record:
            skipped_reason = (
                "No patient record found in tool results — likely a Mem0 cached "
                "response or follow-up question. Guardrail and QA require a fresh DB query."
            )

        tool_call_count = len(tool_names)
        react_calls = api_calls - 1  # subtract QA review
        metrics = {
            "llm_api_calls": api_calls,
            "llm_api_calls_breakdown": {
                "react_loop": react_calls,
                "qa_review": 1 if qa_result else 0,
            },
            "tool_call_count": tool_call_count,
            "tool_names": tool_names,
            "session_id": session_id,
            "latency_ms": elapsed_ms,
            "guardrail_match": guardrail_result["match"] if guardrail_result else None,
            "guardrail_details": {
                "engine_eligible": guardrail_result["engine_result"]["eligible"],
                "llm_eligible": guardrail_result["llm_eligible"],
                "category": guardrail_result["engine_result"]["category"],
                "income_pct": guardrail_result["engine_result"]["income_pct"],
                "threshold_pct": guardrail_result["engine_result"]["threshold_pct"],
                "threshold_amount": guardrail_result["engine_result"]["threshold_amount"],
                "fpl": guardrail_result["engine_result"]["fpl"],
                "expansion": guardrail_result["engine_result"]["expansion"],
                "reason": guardrail_result["engine_result"].get("reason", ""),
            } if guardrail_result else None,
            "qa_approved": qa_result.get("approved") if qa_result else None,
            "qa_issues": qa_result.get("issues") if qa_result else None,
            "qa_corrected_eligible": qa_result.get("corrected_eligible") if qa_result else None,
            "skipped_reason": skipped_reason,
        }
        if input_tokens or output_tokens:
            metrics["input_tokens"] = input_tokens
            metrics["output_tokens"] = output_tokens
            metrics["total_tokens"] = input_tokens + output_tokens

        return metrics

    @staticmethod
    def _sanitize_tool_result(result_text: str) -> str:
        """Truncate oversized tool results and strip control characters."""
        if len(result_text) > MAX_TOOL_RESULT_LENGTH:
            result_text = result_text[:MAX_TOOL_RESULT_LENGTH] + "\n...[truncated]"
        # Strip null bytes and other control chars (except newline/tab)
        result_text = "".join(
            ch for ch in result_text if ch in ("\n", "\t") or (ch >= " ")
        )
        return result_text

    async def process_query(self, query: str, session_id: str = "default") -> str:
        """Process a user query through the agentic loop.

        Sends the query to GPT-4o with all MCP tools available.
        Loops until the model stops requesting tool calls.
        """
        self._init_session(session_id)

        messages = self.conversations[session_id]
        messages.append({"role": "user", "content": query})

        mem0_context, mem0_user = self._get_mem0_context(query, session_id)
        system_prompt = SYSTEM_PROMPT + mem0_context
        openai_tools = _convert_tools(self.mcp.tools)

        api_calls = 1
        total_input_tokens = 0
        total_output_tokens = 0
        tool_names_used = []
        start_time = time.monotonic()

        response = self.client.chat.completions.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            tools=openai_tools or None,
        )

        choice = response.choices[0]
        if response.usage:
            total_input_tokens += response.usage.prompt_tokens
            total_output_tokens += response.usage.completion_tokens

        # Agentic loop: keep going while the model wants to use tools
        iterations = 0
        while choice.finish_reason == "tool_calls":
            iterations += 1
            if iterations >= MAX_AGENT_ITERATIONS:
                logger.warning("Agent hit max iterations (%d) for session %s", MAX_AGENT_ITERATIONS, session_id)
                messages.append({"role": "assistant", "content": "I was unable to complete the determination within the allowed number of steps. Please try a more specific query."})
                break

            assistant_msg = choice.message
            messages.append(assistant_msg.model_dump())

            for tool_call in assistant_msg.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                tool_names_used.append(func_name)
                logger.info("Tool call: %s(%s)", func_name, _truncate(str(func_args)))

                try:
                    result = await self.mcp.call_tool(func_name, func_args)
                    result_text = ""
                    for content in result.content:
                        if hasattr(content, "text"):
                            result_text += content.text
                except Exception as e:
                    result_text = f"Error: {e}"

                result_text = self._sanitize_tool_result(result_text)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                })

            api_calls += 1
            response = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=openai_tools or None,
            )
            choice = response.choices[0]
            if response.usage:
                total_input_tokens += response.usage.prompt_tokens
                total_output_tokens += response.usage.completion_tokens

        # Extract final text response
        if iterations < MAX_AGENT_ITERATIONS:
            final_text = choice.message.content or ""
            messages.append({"role": "assistant", "content": final_text})
        else:
            final_text = messages[-1].get("content", "") if messages else ""

        # Layer 4 Guardrail + QA Agent
        final_text, guardrail_result, qa_result, api_calls = self._apply_guardrail_and_qa(
            messages, final_text, api_calls
        )

        elapsed_ms = round((time.monotonic() - start_time) * 1000)
        self.last_query_metrics = self._build_metrics(
            api_calls, tool_names_used, session_id, elapsed_ms,
            guardrail_result, qa_result, messages,
            total_input_tokens, total_output_tokens,
        )
        logger.info(
            "Query completed: %d API calls, %d tokens, %dms, guardrail=%s, qa=%s for session %s",
            api_calls, total_input_tokens + total_output_tokens, elapsed_ms,
            guardrail_result["match"] if guardrail_result else "skipped",
            qa_result.get("approved") if qa_result else "skipped",
            session_id,
        )

        self._save_mem0(query, final_text, mem0_user)
        self.save_conversation(session_id, self._extract_patient_id(session_id))
        return final_text

    async def process_query_stream(
        self, query: str, session_id: str = "default"
    ) -> AsyncGenerator[str, None]:
        """Process a query and yield text chunks as they arrive."""
        self._init_session(session_id)

        messages = self.conversations[session_id]
        messages.append({"role": "user", "content": query})

        mem0_context, mem0_user = self._get_mem0_context(query, session_id)
        system_prompt = SYSTEM_PROMPT + mem0_context
        openai_tools = _convert_tools(self.mcp.tools)

        api_calls = 0
        tool_names_used = []
        start_time = time.monotonic()
        iterations = 0

        while True:
            iterations += 1
            api_calls += 1
            if iterations > MAX_AGENT_ITERATIONS:
                logger.warning("Stream hit max iterations (%d) for session %s", MAX_AGENT_ITERATIONS, session_id)
                yield "\n\nI was unable to complete the determination within the allowed number of steps."
                break

            stream = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=openai_tools or None,
                stream=True,
            )

            # Accumulate the full response from chunks
            collected_content = ""
            collected_tool_calls: dict[int, dict] = {}
            finish_reason = None

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

                # Stream text content
                if delta and delta.content:
                    collected_content += delta.content
                    yield delta.content

                # Accumulate tool calls (arrive incrementally)
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in collected_tool_calls:
                            collected_tool_calls[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        if tc_delta.id:
                            collected_tool_calls[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                collected_tool_calls[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                collected_tool_calls[idx]["arguments"] += tc_delta.function.arguments

            # If no tool calls, we're done — run guardrail + QA before finishing
            if finish_reason != "tool_calls":
                messages.append({"role": "assistant", "content": collected_content})

                collected_content, guardrail_result, qa_result, api_calls = (
                    self._apply_guardrail_and_qa(messages, collected_content, api_calls)
                )
                # Stream guardrail correction if one was appended
                if guardrail_result and not guardrail_result["match"]:
                    # The correction text is the last assistant message added by _apply_guardrail_and_qa
                    correction = messages[-1].get("content", "")
                    if correction:
                        yield correction

                self._last_guardrail = guardrail_result
                self._last_qa = qa_result

                self._save_mem0(query, collected_content, mem0_user)
                self.save_conversation(session_id, self._extract_patient_id(session_id))
                break

            # Store assistant message with tool calls
            tool_calls_list = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for _, tc in sorted(collected_tool_calls.items())
            ]
            messages.append({
                "role": "assistant",
                "content": collected_content or None,
                "tool_calls": tool_calls_list,
            })

            # Execute tool calls
            for tc in tool_calls_list:
                func_name = tc["function"]["name"]
                func_args = json.loads(tc["function"]["arguments"])
                tool_names_used.append(func_name)
                try:
                    result = await self.mcp.call_tool(func_name, func_args)
                    result_text = ""
                    for content in result.content:
                        if hasattr(content, "text"):
                            result_text += content.text
                except Exception as e:
                    result_text = f"Error: {e}"

                result_text = self._sanitize_tool_result(result_text)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })

        elapsed_ms = round((time.monotonic() - start_time) * 1000)
        guardrail_result = getattr(self, "_last_guardrail", None)
        qa_result = getattr(self, "_last_qa", None)

        self.last_query_metrics = self._build_metrics(
            api_calls, tool_names_used, session_id, elapsed_ms,
            guardrail_result, qa_result, messages,
        )
        logger.info(
            "Stream completed: %d API calls, %dms, guardrail=%s, qa=%s for session %s",
            api_calls, elapsed_ms,
            guardrail_result["match"] if guardrail_result else "skipped",
            qa_result.get("approved") if qa_result else "skipped",
            session_id,
        )

    async def cleanup(self):
        """Shut down all MCP connections."""
        await self.mcp.cleanup()


def _truncate(s: str, max_len: int = 100) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s


async def cli_main():
    """Interactive CLI mode."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    agent = MedicaidAgent()
    try:
        await agent.setup()
        print("\n=== Medicaid Eligibility Checker Agent ===")
        print("Enter a patient ID or name, or ask a question.")
        print("Type 'quit' to exit.\n")

        while True:
            try:
                query = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if query.lower() in ("quit", "exit", "q"):
                break
            if not query:
                continue

            response = await agent.process_query(query)
            print(f"\nAgent: {response}\n")
    finally:
        await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(cli_main())
