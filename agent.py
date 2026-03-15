"""Core Medicaid Eligibility Agent with agentic tool-use loop.

This module contains the agent logic that can be used by both the CLI
and the FastAPI server.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from openai import OpenAI

from mcp_manager import MCPManager
from prompts import SYSTEM_PROMPT

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"


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

    async def process_query(self, query: str, session_id: str = "default") -> str:
        """Process a user query through the agentic loop.

        Sends the query to GPT-4o with all MCP tools available.
        Loops until the model stops requesting tool calls.
        """
        self._init_session(session_id)

        messages = self.conversations[session_id]
        messages.append({"role": "user", "content": query})

        openai_tools = _convert_tools(self.mcp.tools)

        api_calls = 1
        response = self.client.chat.completions.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            tools=openai_tools or None,
        )

        choice = response.choices[0]

        # Agentic loop: keep going while the model wants to use tools
        while choice.finish_reason == "tool_calls":
            assistant_msg = choice.message
            messages.append(assistant_msg.model_dump())

            # Process all tool calls in this response
            for tool_call in assistant_msg.tool_calls:
                func_name = tool_call.function.name
                func_args = json.loads(tool_call.function.arguments)
                logger.info("Tool call: %s(%s)", func_name, _truncate(str(func_args)))

                try:
                    result = await self.mcp.call_tool(func_name, func_args)
                    result_text = ""
                    for content in result.content:
                        if hasattr(content, "text"):
                            result_text += content.text
                except Exception as e:
                    result_text = f"Error: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                })

            # Next iteration
            api_calls += 1
            response = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                tools=openai_tools or None,
            )
            choice = response.choices[0]

        # Extract final text response
        final_text = choice.message.content or ""
        messages.append({"role": "assistant", "content": final_text})
        logger.info("Query completed: %d OpenAI API calls for session %s", api_calls, session_id)
        self.save_conversation(session_id, self._extract_patient_id(session_id))
        return final_text

    async def process_query_stream(
        self, query: str, session_id: str = "default"
    ) -> AsyncGenerator[str, None]:
        """Process a query and yield text chunks as they arrive."""
        self._init_session(session_id)

        messages = self.conversations[session_id]
        messages.append({"role": "user", "content": query})

        openai_tools = _convert_tools(self.mcp.tools)

        while True:
            stream = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
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

            # If no tool calls, we're done
            if finish_reason != "tool_calls":
                messages.append({"role": "assistant", "content": collected_content})
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
                try:
                    result = await self.mcp.call_tool(func_name, func_args)
                    result_text = ""
                    for content in result.content:
                        if hasattr(content, "text"):
                            result_text += content.text
                except Exception as e:
                    result_text = f"Error: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })

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
