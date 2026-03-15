import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from mcp import StdioServerParameters

load_dotenv()

BASE_DIR = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"

# Ensure directories exist
REPORTS_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/medicaid")


def _find_npm_package(package_name: str) -> str | None:
    """Find the entry point of a globally-installed npm package.

    Returns the resolved path to the JS entry point, or None if not found.
    This avoids npx, which pollutes stdout with install messages that
    corrupt the MCP JSON-RPC stream.
    """
    try:
        result = subprocess.run(
            ["node", "-e", f"console.log(require.resolve('{package_name}'))"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


# Try to resolve globally-installed MCP server entry points to bypass npx
_pg_entry = _find_npm_package("@modelcontextprotocol/server-postgres")
_fs_entry = _find_npm_package("@modelcontextprotocol/server-filesystem")

SERVER_CONFIGS = {
    "postgres": StdioServerParameters(
        command="node" if _pg_entry else "npx",
        args=[_pg_entry, DATABASE_URL] if _pg_entry else ["-y", "@modelcontextprotocol/server-postgres", DATABASE_URL],
    ),
    "fetch": StdioServerParameters(
        command="python",
        args=["-m", "mcp_server_fetch"],
    ),
    "filesystem": StdioServerParameters(
        command="node" if _fs_entry else "npx",
        args=[_fs_entry, str(REPORTS_DIR)] if _fs_entry else ["-y", "@modelcontextprotocol/server-filesystem", str(REPORTS_DIR)],
    ),
    "memory": StdioServerParameters(
        command="uvx",
        args=["mem0-mcp-server"],
        env={
            "MEM0_API_KEY": os.environ.get("MEM0_API_KEY", ""),
            "MEM0_DEFAULT_USER_ID": "medicaid-copilot",
        },
    ),
}
