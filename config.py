import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp import StdioServerParameters

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"

# Ensure directories exist
REPORTS_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/medicaid")

# Pin to a specific model snapshot to prevent silent model drift.
# Update deliberately after running regression evals on the new version.
MODEL = "gpt-4o-mini-2024-07-18"

# Agent loop guardrails
MAX_AGENT_ITERATIONS = 10
MAX_TOOL_RESULT_LENGTH = 10000

# MCP connection retry limit
MAX_MCP_RETRIES = 2

# Eval thresholds
MAX_API_CALLS_EVAL = 4  # 1 initial + 1 tool execution + 1 final response + 1 QA review
BANNED_TOOLS = ["fetch"]


def _find_npm_package(package_name: str) -> str | None:
    """Find the JS entry point of a locally-installed npm MCP server package.

    Looks up node_modules/<package>/package.json, reads its ``bin`` field,
    and returns the absolute path to the entry script.  This avoids npx,
    which pollutes stdout with install/audit messages that corrupt the
    MCP JSON-RPC stream.
    """
    pkg_json = BASE_DIR / "node_modules" / package_name / "package.json"
    if not pkg_json.exists():
        logger.warning("npm package not found at %s", pkg_json)
        return None
    try:
        meta = json.loads(pkg_json.read_text())
        bin_field = meta.get("bin", {})
        entry = bin_field if isinstance(bin_field, str) else next(iter(bin_field.values()), None)
        if entry:
            resolved = str((pkg_json.parent / entry).resolve())
            logger.info("Resolved %s -> %s", package_name, resolved)
            return resolved
    except Exception as exc:
        logger.warning("Failed to resolve %s: %s", package_name, exc)
    return None


# Resolve locally-installed MCP server entry points to bypass npx
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
}
