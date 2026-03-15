"""Multi-server MCP connection manager.

Connects to multiple MCP servers via stdio, merges their tools into a single
list for the Anthropic API, and routes tool calls to the correct server.
"""

import logging
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from config import SERVER_CONFIGS

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class MCPManager:
    def __init__(self):
        self.exit_stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}
        self.tools: list[dict] = []  # Anthropic-formatted tool list
        self.tool_to_session: dict[str, str] = {}  # tool_name -> server name
        self._server_tools: dict[str, list[dict]] = {}  # server -> its tools

    async def _connect_server(self, name: str, params) -> bool:
        """Connect to a single MCP server. Returns True on success."""
        try:
            transport = await self.exit_stack.enter_async_context(
                stdio_client(server=params, errlog=sys.stderr)
            )
            read_stream, write_stream = transport
            session = await self.exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            self.sessions[name] = session

            # Collect tools from this server
            response = await session.list_tools()
            server_tools = []
            for tool in response.tools:
                tool_def = {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
                server_tools.append(tool_def)
                self.tool_to_session[tool.name] = name

            self._server_tools[name] = server_tools
            self.tools = [t for tools in self._server_tools.values() for t in tools]

            logger.info(
                "Connected to '%s' MCP server (%d tools)", name, len(response.tools)
            )
            return True
        except Exception as e:
            logger.warning("Failed to connect to '%s' MCP server: %s", name, e)
            return False

    async def connect_all(self):
        """Connect to all configured MCP servers and collect their tools."""
        for name, params in SERVER_CONFIGS.items():
            await self._connect_server(name, params)

    async def _reconnect_server(self, name: str) -> bool:
        """Attempt to reconnect a failed server."""
        params = SERVER_CONFIGS.get(name)
        if not params:
            return False
        logger.info("Attempting to reconnect '%s' MCP server...", name)
        return await self._connect_server(name, params)

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Route a tool call to the correct MCP server session.

        Retries once with a reconnection if the call fails.
        """
        session_name = self.tool_to_session.get(tool_name)
        if not session_name:
            raise ValueError(f"Unknown tool: {tool_name}")

        for attempt in range(1, MAX_RETRIES + 1):
            session = self.sessions.get(session_name)
            if not session:
                if not await self._reconnect_server(session_name):
                    raise ConnectionError(
                        f"MCP server '{session_name}' is not connected"
                    )
                session = self.sessions[session_name]

            try:
                return await session.call_tool(tool_name, arguments)
            except Exception:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "Tool call '%s' failed (attempt %d/%d), reconnecting...",
                        tool_name,
                        attempt,
                        MAX_RETRIES,
                    )
                    self.sessions.pop(session_name, None)
                else:
                    raise

    async def cleanup(self):
        """Close all MCP server connections."""
        await self.exit_stack.aclose()
