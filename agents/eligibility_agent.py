"""Eligibility Agent: ReAct loop for Medicaid eligibility determination.

Handles the LLM reasoning loop with MCP tool marshaling. Does NOT handle
guardrails, QA review, or memory — those are orchestrated by the router.
"""

import json
import logging
import re
from collections.abc import AsyncGenerator

from openai import OpenAI

from agents.base import AgentResult
from config import MAX_AGENT_ITERATIONS, MAX_TOOL_RESULT_LENGTH, MODEL
from mcp_manager import MCPManager

logger = logging.getLogger(__name__)


def _truncate(s: str, max_len: int = 100) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s


def convert_tools(mcp_tools: list[dict]) -> list[dict]:
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


class EligibilityAgent:
    def __init__(self, openai_client: OpenAI, mcp: MCPManager):
        self.client = openai_client
        self.mcp = mcp
        self.last_result: AgentResult | None = None

    @staticmethod
    def sanitize_tool_result(result_text: str) -> str:
        """Truncate oversized tool results and strip control characters."""
        if len(result_text) > MAX_TOOL_RESULT_LENGTH:
            result_text = result_text[:MAX_TOOL_RESULT_LENGTH] + "\n...[truncated]"
        result_text = "".join(
            ch for ch in result_text if ch in ("\n", "\t") or (ch >= " ")
        )
        return result_text

    @staticmethod
    def extract_patient_record(messages: list) -> dict | None:
        """Extract patient record from tool call results in the conversation.

        Looks for a JSON-like patient record returned by the Postgres MCP
        tool call. Returns parsed dict or None.
        """
        for msg in messages:
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if msg.get("role") != "tool" or not content:
                continue
            if "annual_income" in content and "state" in content:
                try:
                    rows = json.loads(content)
                    if isinstance(rows, list) and rows:
                        return rows[0]
                    if isinstance(rows, dict):
                        return rows
                except (json.JSONDecodeError, TypeError):
                    pass
                match = re.search(r'\{[^{}]*"annual_income"[^{}]*\}', content)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        pass
        return None

    async def _execute_tool_calls(self, tool_calls, messages: list, tool_names_used: list):
        """Execute MCP tool calls and append results to messages."""
        for tool_call in tool_calls:
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

            result_text = self.sanitize_tool_result(result_text)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_text,
            })

    async def determine(
        self,
        query: str,
        system_prompt: str,
        messages: list,
    ) -> AgentResult:
        """Run the non-streaming ReAct loop.

        Messages list is mutated in place (tool calls and responses appended).
        Does NOT run guardrail/QA — the router handles that.

        Returns AgentResult with data containing:
        - determination: str (final text)
        - api_calls: int
        - tool_names: list[str]
        - input_tokens: int
        - output_tokens: int
        """
        openai_tools = convert_tools(self.mcp.tools)

        api_calls = 1
        total_input_tokens = 0
        total_output_tokens = 0
        tool_names_used = []

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

        iterations = 0
        while choice.finish_reason == "tool_calls":
            iterations += 1
            if iterations >= MAX_AGENT_ITERATIONS:
                logger.warning("Agent hit max iterations (%d)", MAX_AGENT_ITERATIONS)
                messages.append({
                    "role": "assistant",
                    "content": "I was unable to complete the determination within the allowed number of steps. Please try a more specific query.",
                })
                break

            assistant_msg = choice.message
            messages.append(assistant_msg.model_dump())

            await self._execute_tool_calls(assistant_msg.tool_calls, messages, tool_names_used)

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

        if iterations < MAX_AGENT_ITERATIONS:
            final_text = choice.message.content or ""
            messages.append({"role": "assistant", "content": final_text})
        else:
            final_text = messages[-1].get("content", "") if messages else ""

        return AgentResult(
            success=True,
            data={
                "determination": final_text,
                "api_calls": api_calls,
                "tool_names": tool_names_used,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            },
        )

    async def determine_stream(
        self,
        query: str,
        system_prompt: str,
        messages: list,
    ) -> AsyncGenerator[str, None]:
        """Run the streaming ReAct loop, yielding text chunks.

        Messages list is mutated in place.
        After the generator is exhausted, self.last_result is populated
        with the same structure as determine() returns.
        """
        openai_tools = convert_tools(self.mcp.tools)

        api_calls = 0
        tool_names_used = []
        iterations = 0

        while True:
            iterations += 1
            api_calls += 1
            if iterations > MAX_AGENT_ITERATIONS:
                logger.warning("Stream hit max iterations (%d)", MAX_AGENT_ITERATIONS)
                yield "\n\nI was unable to complete the determination within the allowed number of steps."
                break

            stream = self.client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=openai_tools or None,
                stream=True,
            )

            collected_content = ""
            collected_tool_calls: dict[int, dict] = {}
            finish_reason = None

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason

                if delta and delta.content:
                    collected_content += delta.content
                    yield delta.content

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

            if finish_reason != "tool_calls":
                messages.append({"role": "assistant", "content": collected_content})
                self.last_result = AgentResult(
                    success=True,
                    data={
                        "determination": collected_content,
                        "api_calls": api_calls,
                        "tool_names": tool_names_used,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                )
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

                result_text = self.sanitize_tool_result(result_text)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })
