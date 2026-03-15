# Medicaid Eligibility Copilot for FQHC Caseworkers

An **agentic AI copilot** that helps Federally Qualified Health Center (FQHC) caseworkers determine Medicaid eligibility using multiple MCP (Model Context Protocol) servers.

## What Makes This Agentic

This is not a simple chatbot — it's a **ReAct-style autonomous agent** that:

1. **Receives a goal** (e.g., "Check eligibility for patient #3")
2. **Reasons about what tools to use** and in what order
3. **Executes tools autonomously** in a loop until the task is complete
4. **Handles errors and retries** without human intervention

The agent decides on its own whether to query the database, fetch poverty guidelines from the web, calculate income thresholds, and save a report — all in a single interaction.

## Architecture

```
User (Browser) --> FastAPI Server --> AI Agent (GPT-4o)
                                        |
                                        v
                                   MCP Manager
                                   /    |    \
                          Postgres   Fetch   Filesystem
                          MCP Server MCP Server MCP Server
                              |         |          |
                          Patient DB  HHS.gov   Reports Dir
```

### MCP Servers

The agent connects to **three MCP servers** via stdio, each providing specialized tools:

| Server | Package | Purpose |
|--------|---------|---------|
| **PostgreSQL** | `@modelcontextprotocol/server-postgres` | Query patient records from the database |
| **Fetch** | `mcp-server-fetch` | Retrieve Federal Poverty Level guidelines from HHS.gov |
| **Filesystem** | `@modelcontextprotocol/server-filesystem` | Save eligibility determination reports as markdown |

The `MCPManager` connects to all servers on startup, merges their tools into a unified list, and routes tool calls to the correct server at runtime.

### Agentic Tool-Use Loop

```python
# Simplified flow in agent.py
while True:
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=conversation,
        tools=all_mcp_tools,  # Tools from all 3 MCP servers
    )

    if response.finish_reason == "tool_calls":
        # Agent decided to use a tool — execute it via MCP
        for tool_call in response.tool_calls:
            result = mcp_manager.call_tool(tool_call.name, tool_call.args)
            conversation.append(tool_result_message)
        continue  # Loop back — agent may need more tools

    break  # Agent is done, return final answer
```

### Streaming

The copilot streams responses token-by-token to the browser using `ReadableStream`, with live markdown rendering including tables, lists, and formatted text.

## Eligibility Determination Workflow

When given a patient, the agent autonomously:

1. **Queries the database** for the patient's record (age, state, income, household size, etc.)
2. **Fetches current FPL data** from HHS.gov (with hardcoded fallback data for reliability)
3. **Applies state-specific rules** — expansion status, income thresholds for adults/children/pregnant women
4. **Produces a determination** with step-by-step reasoning
5. **Saves a report** to the filesystem as a timestamped markdown file

## Tech Stack

- **Agent**: Python + OpenAI SDK (GPT-4o) with function calling
- **MCP**: Model Context Protocol for tool integration (stdio transport)
- **Backend**: FastAPI with streaming responses
- **Database**: PostgreSQL (via MCP, not direct SQL in app code)
- **Frontend**: Vanilla HTML/CSS/JS — single file, no build step
- **Deployment**: Render (web service + managed Postgres)

## Project Structure

```
.
├── agent.py          # Core agentic loop with tool-use
├── mcp_manager.py    # Multi-server MCP connection manager
├── config.py         # MCP server configurations
├── prompts.py        # System prompt + FPL reference data
├── server.py         # FastAPI endpoints + static file serving
├── seed_db.py        # Database schema + sample patient data
├── render.yaml       # Render deployment blueprint
├── static/
│   └── index.html    # FQHC Copilot UI (three-panel layout)
└── reports/          # Saved eligibility determination reports
```

## Running Locally

### Prerequisites

- Python 3.13+
- Node.js (for MCP servers via npx)
- PostgreSQL (via Docker or local install)

### Setup

```bash
# Start PostgreSQL
docker run -d --name medicaid-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=medicaid -p 5432:5432 postgres:16

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env  # Add your OPENAI_API_KEY

# Seed the database
python seed_db.py

# Run the server
uvicorn server:app --port 8000
```

Open http://localhost:8000 to use the copilot.

## Deploying to Render

1. Push to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com/select-repo?type=blueprint)
3. Connect the repo — Render detects `render.yaml`
4. Set `OPENAI_API_KEY` when prompted
5. Deploy

The blueprint creates a free web service and a free PostgreSQL database, seeds patient data on first build.

## License

MIT
