"""Core Medicaid Eligibility Agent with agentic tool-use loop.

This module contains the agent logic that can be used by both the CLI
and the FastAPI server.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator

from anthropic import Anthropic
from dotenv import load_dotenv

from mcp_manager import MCPManager
from prompts import SYSTEM_PROMPT

load_dotenv()

logger = logging.getLogger(__name__)


class MedicaidAgent:
    def __init__(self):
        self.anthropic = Anthropic()
        self.mcp = MCPManager()
        self.conversations: dict[str, list] = {}  # session_id -> messages

    async def setup(self):
        """Connect to all MCP servers."""
        logger.info("Connecting to MCP servers...")
        await self.mcp.connect_all()
        tool_names = [t["name"] for t in self.mcp.tools]
        logger.info("Ready. Available tools: %s", tool_names)

    async def process_query(self, query: str, session_id: str = "default") -> str:
        """Process a user query through the agentic loop.

        Sends the query to Claude with all MCP tools available.
        Loops until Claude stops requesting tool calls.
        """
        if session_id not in self.conversations:
            self.conversations[session_id] = []

        messages = self.conversations[session_id]
        messages.append({"role": "user", "content": query})

        # Initial call to Claude
        response = self.anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=self.mcp.tools,
        )

        # Agentic loop: keep going while Claude wants to use tools
        while response.stop_reason == "tool_use":
            assistant_content = response.content
            messages.append({"role": "assistant", "content": assistant_content})

            # Process all tool calls in this response
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    logger.info("Tool call: %s(%s)", block.name, _truncate(str(block.input)))
                    try:
                        result = await self.mcp.call_tool(block.name, block.input)
                        # Extract text from MCP result
                        result_text = ""
                        for content in result.content:
                            if hasattr(content, "text"):
                                result_text += content.text
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                    except Exception as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: {e}",
                            "is_error": True,
                        })

            messages.append({"role": "user", "content": tool_results})

            # Next iteration
            response = self.anthropic.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=self.mcp.tools,
            )

        # Extract final text response
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text

        messages.append({"role": "assistant", "content": response.content})
        return final_text

    async def process_query_stream(
        self, query: str, session_id: str = "default"
    ) -> AsyncGenerator[str, None]:
        """Process a query and yield text chunks as they arrive."""
        if session_id not in self.conversations:
            self.conversations[session_id] = []

        messages = self.conversations[session_id]
        messages.append({"role": "user", "content": query})

        while True:
            collected_content = []
            with self.anthropic.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=self.mcp.tools,
            ) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            yield event.delta.text

            response = stream.get_final_message()
            collected_content = response.content
            messages.append({"role": "assistant", "content": collected_content})

            if response.stop_reason != "tool_use":
                break

            # Process tool calls
            tool_results = []
            for block in collected_content:
                if block.type == "tool_use":
                    try:
                        result = await self.mcp.call_tool(block.name, block.input)
                        result_text = ""
                        for content in result.content:
                            if hasattr(content, "text"):
                                result_text += content.text
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                    except Exception as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: {e}",
                            "is_error": True,
                        })

            messages.append({"role": "user", "content": tool_results})

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
