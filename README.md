# MediAssist AI — Coverage Continuity Infrastructure

An **agentic AI copilot** that helps Federally Qualified Health Center (FQHC) caseworkers determine Medicaid eligibility using MCP (Model Context Protocol) servers, a five-layer defense architecture, and an eval-gated deployment pipeline.

## What Makes This Agentic

This is not a simple chatbot — it's a **ReAct-style autonomous agent** that:

1. **Receives a goal** (e.g., "Check eligibility for patient #3")
2. **Recalls prior determinations** from Mem0 memory scoped per patient
3. **Reasons about what tools to use** and in what order
4. **Executes tools autonomously** in a loop until the task is complete
5. **Validates its own output** against a deterministic engine and QA agent
6. **Saves results** to memory and filesystem for future recall

The agent decides on its own whether to query the database, calculate income thresholds, and save a report — all in a single interaction.

## Five-Layer Defense Architecture

LLMs are unreliable calculators. A single LLM call is insufficient for high-stakes Medicaid determinations. This system uses five independent layers — each catches a different failure mode:

```
┌─────────────────────────────────────────────────────────┐
│ Layer 1: System Prompt                                  │
│   FPL tables + 50-state rules embedded in context       │
│   Catches: Basic reasoning errors                       │
├─────────────────────────────────────────────────────────┤
│ Layer 2: Deterministic Engine (eligibility.py)          │
│   Pure Python — zero LLM involvement                    │
│   Catches: All math errors                              │
├─────────────────────────────────────────────────────────┤
│ Layer 3: Structured Output                              │
│   JSON schema for tool calls                            │
│   Catches: Format/parsing errors                        │
├─────────────────────────────────────────────────────────┤
│ Layer 4: Post-Hoc Guardrail                             │
│   Compares LLM output vs engine in real-time            │
│   Catches: Hallucinated determinations                  │
├─────────────────────────────────────────────────────────┤
│ Layer 5: QA Agent                                       │
│   Second LLM reviewing first with ground truth          │
│   Catches: Reasoning errors the engine can't catch      │
└─────────────────────────────────────────────────────────┘
```

**Layer 4 validated the entire architecture**: Patient #10 (Kevin Park) has income of $21,598 — exactly $1 over the $21,597 threshold. The LLM got this wrong. The guardrail caught it and corrected the response in real-time.

**Layer 5 (QA Agent)** checks five things independently: category, FPL table, math, citizenship, and expansion status. It receives ground truth from the deterministic engine — it's a reasoning auditor, not a coin flip.

All five layers apply to both streaming and non-streaming paths.

## Deterministic Eligibility Engine

`eligibility.py` is the **single source of truth** for eligibility math. It's a pure Python function with zero LLM involvement:

- Looks up correct FPL threshold for household size (standard + Alaska + Hawaii tables)
- Determines Medicaid expansion status for the patient's state
- Applies correct threshold percentage (138% expansion, 100% non-expansion, higher for pregnant/children)
- Returns: `eligible`, `category`, `threshold_used`, `fpl_amount`, `reasoning`

Used by three consumers:
1. **Layer 4 guardrail** — compares LLM determination against engine in real-time
2. **Layer 5 QA agent** — provides ground truth for reasoning audit
3. **Eval suite** — computes expected results for all 16 seed patients

## Architecture

```
User (Browser) --> FastAPI Server --> AI Agent (GPT-4o-mini)
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
               Mem0 SDK          MCP Manager          Guardrail
              (pre/post)        /     │      \         + QA Agent
                            Postgres Fetch  Filesystem
                            MCP      MCP    MCP
                              │    (banned)   │
                          Patient DB       Reports
```

### MCP Servers

The agent connects to **three MCP servers** via stdio:

| Server | Package | Purpose |
|--------|---------|---------|
| **PostgreSQL** | `@modelcontextprotocol/server-postgres` | Query patient records from the database |
| **Fetch** | `mcp-server-fetch` | Web access (banned by evals — FPL data is embedded) |
| **Filesystem** | `@modelcontextprotocol/server-filesystem` | Save eligibility determination reports |

### Memory (Mem0 SDK)

Mem0 runs as a **direct Python SDK call**, not an MCP tool:

- **Why not MCP?** Each MCP tool exposed to GPT adds potential API calls. Mem0 as MCP added 2 extra OpenAI round-trips per query.
- **SDK approach**: Memory search happens in Python *before* the GPT call (injected into system prompt). Memory save happens *after* the final response. Zero additional API calls.
- **Per-patient scoping**: Memories are keyed by `patient-{id}` to prevent cross-patient pollution — a HIPAA-aware design pattern.

### Model Pinning

The model is pinned to `gpt-4o-mini-2024-07-18` (specific snapshot), not the `gpt-4o-mini` alias. Aliases silently resolve to new snapshots that can change behavior. Changing the model version requires a deliberate code change + regression eval run.

## Evaluation Suite

### Three Eval Dimensions

Every determination is evaluated on three independent dimensions:

| Dimension | Method | Threshold |
|-----------|--------|-----------|
| **Correctness** | Deterministic engine comparison | Must match exactly |
| **Efficiency** | API call count + banned tool check | ≤4 API calls, no Fetch |
| **Quality** | Keyword matching with alternatives | Must mention category, state, threshold |

A determination can pass correctness but fail quality (or vice versa) — the dimensions are independent.

### 16 Seed Patients

8 standard cases + 8 edge cases covering the full eligibility decision space:

| # | Patient | Edge Case |
|---|---------|-----------|
| 9 | Elena Ruiz | Income exactly at 138% FPL threshold (`<=` boundary) |
| 10 | Kevin Park | Income $1 over threshold (just above cutoff) |
| 11 | Yuki Tanaka | Non-US citizen (citizenship disqualification) |
| 12 | Jordan Lee | Age 18 (child→adult category boundary) |
| 13 | Margaret Davis | Age 65 in non-expansion state (adult→elderly boundary) |
| 14 | Tamika Williams | Pregnant in non-expansion state (higher threshold) |
| 15 | John Whitehorse | Alaska (different FPL table) |
| 16 | Leilani Kealoha | Hawaii, household size 8 (FPL table max boundary) |

### Running Evals

```bash
npm run eval          # Deterministic evals (instant, no API calls)
npm run eval:agent    # Full agent evals (requires running server + OpenAI key)
npm run build         # Full build: pip install → deterministic evals → seed database
```

### CI/CD Pipeline (Eval-Gated Deployment)

A single combined GitHub Actions workflow (`regression-evals.yml`) handles all eval automation:

- **On every push/PR**: Deterministic evals run instantly — blocks broken code
- **Daily schedule**: Full agent evals for drift monitoring
- **Manual trigger**: `workflow_dispatch` for on-demand agent evals
- **Auto-issue on failure**: Agent eval failures auto-create GitHub issues with `eval-regression` label and run link
- **Render deployment**: Deterministic evals run in the build pipeline — if they fail, deployment is blocked

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Marketing site (MediAssist AI home page) |
| `GET` | `/copilot` | FQHC Copilot UI (eligibility determination app) |
| `GET` | `/health` | MCP server connection status |
| `GET` | `/patients` | List all patients |
| `POST` | `/patients` | Create a new patient |
| `GET` | `/patients/{id}` | Get patient by ID |
| `POST` | `/check` | Run eligibility check (returns determination + metrics) |
| `POST` | `/check/stream` | Streaming eligibility check |
| `POST` | `/check/{patient_id}` | Check eligibility by patient ID |
| `GET` | `/metrics` | Last query metrics (guardrail, QA, latency, tokens) |
| `GET` | `/sessions` | List active conversation sessions |
| `GET` | `/sessions/{id}` | Retrieve conversation history |
| `GET` | `/patients/{id}/sessions` | List sessions for a patient |
| `GET` | `/reports` | List saved determination reports |
| `GET` | `/reports/{filename}` | Get a specific report |

### Metrics Schema

The `/check` response and `/metrics` endpoint expose:

```json
{
  "api_calls": 3,
  "tool_names": ["read_query", "write_file"],
  "guardrail_match": true,
  "guardrail_details": {
    "engine_eligible": true,
    "llm_eligible": true,
    "category": "adult_expansion",
    "income_pct_fpl": 95.2,
    "threshold_pct": 138
  },
  "qa_approved": true,
  "qa_issues": [],
  "latency_ms": 4200,
  "input_tokens": 3500,
  "output_tokens": 800,
  "total_tokens": 4300
}
```

## Best Practices Implemented

### Agent Loop Guardrails
The tool-use loop is capped at `MAX_AGENT_ITERATIONS = 10`. If the model keeps requesting tools beyond this limit, the agent stops and returns a graceful message. Applied to both streaming and non-streaming paths.

### Tool Result Sanitization
All MCP tool results pass through sanitization before being sent to GPT:
- **Truncation**: Results over 10,000 characters are truncated
- **Control character stripping**: Null bytes and non-printable characters removed

### Embedded Reference Data
2025 Federal Poverty Level tables and all 50-state Medicaid thresholds are embedded directly in the system prompt. The agent does **not** need to fetch from HHS.gov. The Fetch MCP server is available but banned by evals.

### Streaming Parity
The streaming path has full feature parity with the non-streaming path:
- Mem0 search before GPT, Mem0 save after completion
- Loop guardrails (same `MAX_AGENT_ITERATIONS`)
- Tool result sanitization
- Post-hoc guardrail + QA agent review
- Metrics tracking and conversation persistence

### API Call Optimization
Reduced from 5-6 OpenAI API calls per query to 3:
- Removing Mem0 MCP (was 2 extra calls, now 0 via SDK)
- Embedding FPL data (was 1-2 fetch calls, now 0)
- Typical flow: 1 initial + 1 tool execution + 1 final = 3 calls

### Conversation Persistence
Conversations are stored in PostgreSQL as JSONB, keyed by session ID with patient association. Multi-turn sessions can be recovered across server restarts.

## Tech Stack

| Component | Technology |
|-----------|------------|
| **Language** | Python 3.13+ |
| **LLM** | OpenAI gpt-4o-mini-2024-07-18 (pinned) |
| **Framework** | FastAPI |
| **Database** | PostgreSQL (Render managed) |
| **Memory** | Mem0 SDK (direct Python, not MCP) |
| **Tool Protocol** | MCP (stdio transport) |
| **Deployment** | Render (render.yaml blueprint) |
| **CI/CD** | GitHub Actions (eval-gated) |
| **Frontend** | Vanilla HTML/CSS/JS |
| **Evals** | npm scripts + Python |

## Project Structure

```
.
├── agent.py              # ReAct loop, guardrails, QA agent, Mem0, metrics
├── eligibility.py        # Deterministic engine (single source of truth)
├── prompts.py            # System prompt + QA prompt with embedded FPL data
├── config.py             # Constants, MCP configs, model pinning
├── mcp_manager.py        # Multi-server MCP connection manager with retry
├── server.py             # FastAPI endpoints
├── seed_db.py            # Database schema + 16 seed patients
├── package.json          # npm scripts: eval, eval:agent, start, build
├── render.yaml           # Render blueprint (eval-gated deployment)
├── .github/
│   └── workflows/
│       └── regression-evals.yml  # Deterministic on push + agent evals daily
├── evals/
│   └── test_eligibility.py       # 3-dimension eval suite (16 patients)
├── static/
│   ├── index.html                # MediAssist AI marketing site
│   └── copilot.html              # FQHC Copilot UI (3-panel app)
├── reports/                      # Saved eligibility determination reports
└── requirements.txt
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

Open http://localhost:8000 for the marketing site, or http://localhost:8000/copilot for the eligibility tool.

## Deploying to Render

1. Push to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com/select-repo?type=blueprint)
3. Connect the repo — Render detects `render.yaml`
4. Set `OPENAI_API_KEY` and `MEM0_API_KEY` when prompted
5. Deploy

The blueprint creates a free web service and a free PostgreSQL database. Deterministic evals run during build — if they fail, deployment is blocked.

## License

MIT
