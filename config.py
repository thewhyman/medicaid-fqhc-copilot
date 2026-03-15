import os
from pathlib import Path

from dotenv import load_dotenv
from mcp import StdioServerParameters

load_dotenv()

BASE_DIR = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"

# Ensure directories exist
REPORTS_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/medicaid")

SERVER_CONFIGS = {
    "postgres": StdioServerParameters(
        command="npx",
        args=["-y", "--silent", "@modelcontextprotocol/server-postgres", DATABASE_URL],
    ),
    "fetch": StdioServerParameters(
        command="python",
        args=["-m", "mcp_server_fetch"],
    ),
    "filesystem": StdioServerParameters(
        command="npx",
        args=["-y", "--silent", "@modelcontextprotocol/server-filesystem", str(REPORTS_DIR)],
    ),
}
