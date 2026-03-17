# MediAssist AI — Coverage Continuity Infrastructure

An **agentic AI copilot** that helps Federally Qualified Health Center (FQHC) caseworkers determine Medicaid eligibility using MCP (Model Context Protocol) servers, with built-in guardrails, memory, and an evaluation suite.

## What Makes This Agentic

This is not a simple chatbot — it's a **ReAct-style autonomous agent** that:

1. **Receives a goal** (e.g., "Check eligibility for patient #3")
2. **Recalls prior determinations** from Mem0 memory scoped per patient
3. **Reasons about what tools to use** and in what order
4. **Executes tools autonomously** in a loop until the task is complete
5. **Handles errors and retries** without human intervention
6. **Saves results** to memory and filesystem for future recall

The agent decides on its own whether to query the database, calculate income thresholds, and save a report — all in a single interaction.

## Architecture

```
User (Browser) --> FastAPI Server --> AI Agent (GPT-4o-mini)
                                        |
                                   Mem0 SDK (pre/post query)
                                        |
                                   MCP Manager
                                  /     |      \
                          Postgres  Fetch   Filesystem
                          MCP       MCP     MCP
                            |        |        |
                        Patient DB (spare) Reports
```

### MCP Servers

The agent connects to **three MCP servers** via stdio:

| Server | Package | Purpose |
|--------|---------|---------|
| **PostgreSQL** | `@modelcontextprotocol/server-postgres` | Query patient records from the database |
| **Fetch** | `mcp-server-fetch` | Web access (banned by evals — FPL data is embedded in the prompt) |
| **Filesystem** | `@modelcontextprotocol/server-filesystem` | Save eligibility determination reports as markdown |

### Memory (Mem0 SDK)

Mem0 runs as a **direct Python SDK call**, not an MCP tool. This is a deliberate optimization:

- **Why not MCP?** Each MCP tool exposed to GPT adds potential API calls. Mem0 as MCP added 2 extra OpenAI round-trips per query (search + save).
- **SDK approach**: Memory search happens in Python *before* the GPT call (injected into the system prompt). Memory save happens *after* the final response. Zero additional API calls.
- **Per-patient scoping**: Memories are keyed by `patient-{id}` to prevent cross-patient pollution.

### Agentic Tool-Use Loop

```python
# Simplified flow in agent.py
response = openai.chat.completions.create(
    model="gpt-4o-mini",
    messages=[system_prompt + mem0_context] + conversation,
    tools=all_mcp_tools,
)

iterations = 0
while response.finish_reason == "tool_calls":
    iterations += 1
    if iterations >= MAX_AGENT_ITERATIONS:  # Guardrail: max 10 iterations
        break

    for tool_call in response.tool_calls:
        result = mcp_manager.call_tool(tool_call.name, tool_call.args)
        result = sanitize_tool_result(result)  # Truncate + strip control chars
        conversation.append(tool_result_message)

    response = openai.chat.completions.create(...)  # Next iteration
```

## Best Practices Implemented

### 1. Agent Loop Guardrails
The tool-use loop is capped at `MAX_AGENT_ITERATIONS = 10`. If the model keeps requesting tools beyond this limit, the agent stops and returns a graceful message. This prevents runaway agents from looping forever due to hallucinated tool calls. Applied to both streaming and non-streaming paths.

### 2. Tool Result Sanitization
All MCP tool results pass through `_sanitize_tool_result()` before being sent to GPT:
- **Truncation**: Results over 10,000 characters are truncated to prevent blowing up the context window (e.g., a `SELECT *` on a large table)
- **Control character stripping**: Null bytes and non-printable characters are removed to prevent JSON corruption or model confusion

### 3. Embedded Reference Data (No Fetch Needed)
2025 Federal Poverty Level tables and all 50-state Medicaid thresholds are embedded directly in the system prompt. The agent does **not** need to fetch from HHS.gov, eliminating 1-2 API round-trips per query. The fetch MCP server is still available but banned by evals.

### 4. Per-Patient Memory Scoping
Mem0 memories are scoped to individual patients via `user_id=f"patient-{id}"`. This prevents a determination for Patient #1 from leaking into Patient #5's context. The agent reuses prior determinations when the same patient is queried again.

### 5. Cost and Latency Tracking
Every query records metrics exposed via the `/check` endpoint:
- `api_calls`: Number of OpenAI API round-trips
- `input_tokens` / `output_tokens` / `total_tokens`: Token usage from OpenAI
- `latency_ms`: End-to-end wall clock time
- `tool_names`: Which MCP tools were called

### 6. Streaming Parity
The streaming path (`process_query_stream`) has full feature parity with the non-streaming path:
- Mem0 search before GPT, Mem0 save after completion
- Loop guardrails (same `MAX_AGENT_ITERATIONS`)
- Tool result sanitization
- Tool name tracking and latency metrics
- Conversation persistence to Postgres

### 7. API Call Optimization
Reduced from 5-6 OpenAI API calls per query to 3 through:
- Removing Mem0 MCP (was 2 extra calls: search + save, now 0)
- Embedding FPL data in the prompt (was 1-2 fetch calls, now 0)
- Typical flow: 1 initial call + 1 tool execution + 1 final response = 3 calls

## Evaluation Suite

### Running Evals

```bash
npm run eval          # Deterministic evals (instant, no API calls)
npm run eval:agent    # Full agent evals (requires running server + OpenAI key)
npm run build         # Full build pipeline including evals
```

### Three Eval Dimensions

#### 1. Correctness (Deterministic)
Computes expected eligibility from FPL tables and state thresholds for all 16 seed patients using the same rules the agent should follow. No LLM needed — pure rule-based computation.

Runs in the Render build pipeline (`render.yaml`) — if evals fail, deployment is blocked.

#### 2. Tool Efficiency
Checks that the agent stays within resource bounds:
- **Max 3 API calls** per determination
- **Fetch tool banned** — FPL data is in the prompt, fetching wastes a round-trip

#### 3. Response Quality
Verifies the agent's response contains required keywords using flexible matching:
- Each patient has keyword groups with alternatives (e.g., `["pregnant", "pregnancy"]`)
- State matching accepts abbreviations or full names (e.g., `"CA"` or `"california"`)
- Ambiguous cases (disabled/elderly in non-expansion states) are handled gracefully

### Border Test Cases (Patients #9-16)
The seed data includes 8 edge cases that stress-test the eligibility logic:

| # | Patient | Edge Case |
|---|---------|-----------|
| 9 | Elena Ruiz | Income exactly at 138% FPL threshold (`<=` boundary) |
| 10 | Kevin Park | Income $1 over threshold (just above cutoff) |
| 11 | Yuki Tanaka | Non-US citizen (citizenship disqualification) |
| 12 | Jordan Lee | Age 18 (child/adult category boundary) |
| 13 | Margaret Davis | Age 65 in non-expansion state (adult/elderly boundary) |
| 14 | Tamika Williams | Pregnant in non-expansion state (higher threshold applies) |
| 15 | John Whitehorse | Alaska (different FPL table) |
| 16 | Leilani Kealoha | Hawaii, household size 8 (FPL table max boundary) |

## Eligibility Determination Workflow

When given a patient, the agent autonomously:

1. **Checks Mem0 memory** for prior determinations on this patient (SDK call, not GPT)
2. **Queries the database** for the patient's record via MCP Postgres
3. **Applies eligibility rules** using embedded FPL data (no web fetch needed)
4. **Produces a determination** — ELIGIBLE or NOT ELIGIBLE with step-by-step reasoning
5. **Saves a report** to the filesystem via MCP as a timestamped markdown file
6. **Saves to Mem0** a summary for future recall (SDK call, not GPT)

## Tech Stack

- **Agent**: Python + OpenAI SDK (GPT-4o-mini) with function calling
- **MCP**: Model Context Protocol for tool integration (stdio transport)
- **Memory**: Mem0 SDK (direct Python calls, not MCP)
- **Backend**: FastAPI with streaming responses
- **Database**: PostgreSQL (via MCP, not direct SQL in app code)
- **Frontend**: Vanilla HTML/CSS/JS — single file, no build step
- **Evals**: Deterministic + agent evals with `npm run eval`
- **Deployment**: Render (web service + managed Postgres)

## Project Structure

```
.
├── agent.py              # Core agentic loop with guardrails, memory, metrics
├── mcp_manager.py        # Multi-server MCP connection manager with retry
├── config.py             # MCP server configurations
├── prompts.py            # System prompt + embedded FPL/state threshold data
├── server.py             # FastAPI endpoints (with metrics in /check response)
├── seed_db.py            # Database schema + 16 patients (8 standard + 8 edge cases)
├── package.json          # npm scripts: eval, eval:agent, start, build
├── render.yaml           # Render deployment blueprint (evals gate deployment)
├── evals/
│   └── test_eligibility.py  # Deterministic + agent evals (correctness, efficiency, quality)
├── static/
│   └── index.html        # FQHC Copilot UI (three-panel layout)
└── reports/              # Saved eligibility determination reports
```

## Running Locally

### Prerequisites

- Python 3.13+
- Node.js (for MCP servers)
- PostgreSQL (via Docker or local install)

### Setup

```bash
# Start PostgreSQL
docker run -d --name medicaid-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=medicaid -p 5432:5432 postgres:16

# Install dependencies
pip install -r requirements.txt
npm install

# Configure environment
cp .env.example .env  # Add your OPENAI_API_KEY and MEM0_API_KEY

# Run evals + seed the database
npm run build

# Run the server
npm start
```

Open http://localhost:8000 to use the copilot.

## Deploying to Render

1. Push to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com/select-repo?type=blueprint)
3. Connect the repo — Render detects `render.yaml`
4. Set `OPENAI_API_KEY` and `MEM0_API_KEY` when prompted
5. Deploy

The blueprint creates a free web service and a free PostgreSQL database. Deterministic evals run during build — if they fail, deployment is blocked.

## License

MIT
