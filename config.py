from pathlib import Path
from mcp import StdioServerParameters

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "patients.db"
REPORTS_DIR = BASE_DIR / "reports"

# Ensure directories exist
DB_PATH.parent.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

SERVER_CONFIGS = {
    "sqlite": StdioServerParameters(
        command="python",
        args=["-m", "mcp_server_sqlite", "--db-path", str(DB_PATH)],
    ),
    "fetch": StdioServerParameters(
        command="python",
        args=["-m", "mcp_server_fetch"],
    ),
    "filesystem": StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", str(REPORTS_DIR)],
    ),
}
