# MediAssist AI — Coverage Continuity Infrastructure

An **agentic AI platform** that prevents avoidable Medicaid coverage loss for FQHC (Federally Qualified Health Center) patients through AI-powered eligibility determination, risk-scored renewal workflows, TCPA-compliant outreach, and a caseworker dashboard.

## Overview

Every year, millions of patients lose Medicaid coverage due to paperwork failures — not ineligibility. During the 2023-2024 unwinding, 69% of disenrollments were procedural. MediAssist AI addresses this with two phases:

- **Phase 1 — Eligibility Copilot**: A ReAct-style AI agent that determines Medicaid eligibility across all 50 states with a five-layer defense architecture ensuring healthcare-grade reliability.
- **Phase 2 — Recertification Engine**: A multi-agent renewal workflow with risk scoring, automated outreach, document processing, and a caseworker dashboard to prevent procedural coverage loss.

## What Makes This Agentic

This is not a simple chatbot — it's a **multi-agent system** with 10+ specialized agents:

1. **Receives a goal** (e.g., "Check eligibility for patient #3")
2. **Recalls prior determinations** from Mem0 memory scoped per patient
3. **Reasons about what tools to use** and in what order
4. **Executes tools autonomously** in a loop until the task is complete
5. **Validates its own output** against a deterministic engine and QA agent
6. **Saves results** to memory and filesystem for future recall
7. **Scores renewal risk** and triggers TCPA-compliant outreach sequences
8. **Manages renewal workflows** through an 11-state state machine
9. **Processes documents** with LLM classification and deterministic validation
10. **Powers a caseworker dashboard** with portfolio views, alerts, and overrides

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
User (Browser) ──► FastAPI Server ──► Router (Orchestrator)
     │                                    │
     ├─ /dashboard ──► Dashboard UI       ├─ Memory Agent (Mem0 SDK)
     ├─ /copilot ────► Eligibility UI     ├─ Knowledge Agent (FPL/rules)
     └─ / ───────────► Marketing Site     ├─ Eligibility Agent (ReAct loop)
                                          │     ├─ MCP: Postgres (patient DB)
                                          │     ├─ MCP: Filesystem (reports)
                                          │     └─ MCP: Fetch (banned)
                                          ├─ Correctness Eval (guardrail)
                                          ├─ Quality Eval (QA agent)
                                          ├─ Risk Scoring Agent
                                          ├─ Outreach Agent (TCPA)
                                          ├─ Document Agent (LLM + rules)
                                          ├─ Workflow Orchestrator (state machine)
                                          └─ Caseworker Copilot (dashboard)
```

### Agent Types

| Agent | LLM? | Purpose |
|-------|------|---------|
| **Eligibility Agent** | Yes | ReAct loop — queries DB, determines eligibility |
| **Risk Scoring Agent** | No | 5-factor deterministic risk scoring (0-1.0) |
| **Outreach Agent** | No | TCPA-compliant SMS templates, consent, frequency caps |
| **Document Agent** | Yes | LLM classifies/extracts; deterministic validation |
| **Workflow Orchestrator** | No | 11-state renewal state machine |
| **Caseworker Copilot** | Partial | Deterministic alerts + LLM summaries |
| **Memory Agent** | No | Mem0 SDK for per-patient memory |
| **Knowledge Agent** | No | FPL tables + 50-state rules |
| **Correctness Eval** | No | Deterministic guardrail (engine vs LLM) |
| **Quality Eval** | Yes | QA agent reviews determination |

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

## Phase 2: Recertification Engine

### Renewal Workflow State Machine

```
IDENTIFIED ──► NOTIFIED ──► ENGAGED ──► DOC_COLLECTION ──► VALIDATION ──► SUBMISSION_READY ──► COMPLETED
                  │             │              │                 │
                  ▼             ▼              ▼                 ▼
             NO_RESPONSE   DROPPED_OFF    (reminder)      DOC_COLLECTION
                  │             │                          (invalid doc)
                  ▼             ▼
              NOTIFIED       ENGAGED          ◄── Recovery paths
             (escalate)    (re-engage)

              EXPIRED ◄── Deadline passed in any non-terminal state
```

Every state transition is logged to `audit_log` for HIPAA compliance.

### Risk Scoring

Deterministic 5-factor scoring (no LLM):

| Factor | Weight | Trigger |
|--------|--------|---------|
| Deadline proximity | 0-0.30 | ≤14d: 0.30, 15-30d: 0.20, 31-60d: 0.10 |
| Prior renewal history | 0-0.25 | Lapsed: 0.25, first renewal: 0.15 |
| Response pattern | 0-0.20 | No-response >50%: 0.20 |
| Contact quality | 0-0.10 | Bounced: 0.10, unverified: 0.05 |
| Demographic complexity | 0-0.15 | Age≥65, non-English, household≥5: 0.05 each |

Four risk tiers: **Critical** (0.70-1.0), **High** (0.40-0.69), **Medium** (0.20-0.39), **Low** (0-0.19).

### TCPA-Compliant Outreach

All outreach enforces:
- **Consent required** — `opted_in` status checked before every message
- **Quiet hours** — 8am-9pm patient local time only
- **Frequency caps** — max 3/week, 1/day
- **Opt-out** — STOP/ALTO immediately blocks all messages
- **Bilingual** — EN + ES templates, selected by `preferred_language`
- **Escalation** — 2 unanswered → caseworker alert, 3 → phone outreach

### Document Processing

Pipeline: **Classify** (LLM) → **Extract** (LLM) → **Validate** (deterministic)

- Supports: pay stubs, tax returns, utility bills, ID documents
- Validation: date ranges, amount extraction, name cross-reference
- Confidence < 0.80 → routed to caseworker for manual review

### Caseworker Dashboard

A unified web UI for non-technical FQHC caseworkers:
- **Pipeline view** — all patients grouped by workflow state
- **Risk-ranked alerts** — critical/high/medium patients needing attention
- **Patient detail** — eligibility chat + renewal status + timeline + actions
- **Override capability** — caseworker can override agent decisions with audit trail
- **Embedded eligibility copilot** — per-patient chat with streaming responses

### Database Schema (Phase 2)

Three new tables added to support renewal workflows:

| Table | Purpose |
|-------|---------|
| `renewals` | One row per renewal workflow — state, risk score, documents, communication log |
| `documents` | Uploaded documents with classification, extraction data, and review status |
| `audit_log` | HIPAA 6-year retention — every state transition, override, and PHI access |

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

### Phase 2 Eval Agents

| Eval | Method | What it checks |
|------|--------|----------------|
| **Risk Scoring Eval** | Deterministic | Score determinism, tier boundaries, all 16 scenarios |
| **Outreach Compliance Eval** | Deterministic | Opt-out blocking, quiet hours, frequency caps, STOP text |
| **Workflow Eval** | Deterministic | Valid transitions, timeout escalations, recovery paths, audit logging |

### Running Evals

```bash
npm run eval          # Deterministic evals (instant, no API calls)
npm run eval:agent    # Full agent evals (requires running server + OpenAI key)
npm run build         # Full build: pip install → deterministic evals → seed database

# Phase 2 unit tests (94 tests)
python -m pytest tests/ -v
```

### CI/CD Pipeline (Eval-Gated Deployment)

A single combined GitHub Actions workflow (`regression-evals.yml`) handles all eval automation:

- **On every push/PR**: Deterministic evals run instantly — blocks broken code
- **Daily schedule**: Full agent evals for drift monitoring
- **Manual trigger**: `workflow_dispatch` for on-demand agent evals
- **Auto-issue on failure**: Agent eval failures auto-create GitHub issues with `eval-regression` label and run link
- **Render deployment**: Deterministic evals run in the build pipeline — if they fail, deployment is blocked

## API Endpoints

### Phase 1 — Eligibility

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Marketing site |
| `GET` | `/copilot` | Eligibility copilot UI |
| `GET` | `/dashboard` | Caseworker dashboard UI |
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

### Phase 2 — Renewals

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/renewals/{patient_id}/start` | Initiate renewal workflow (risk score + first outreach) |
| `GET` | `/renewals/{patient_id}/status` | Current state + audit timeline |
| `GET` | `/renewals/pipeline` | All patients grouped by workflow state |
| `POST` | `/renewals/{patient_id}/event` | Trigger state transition |
| `POST` | `/renewals/{patient_id}/documents` | Upload and process a document |
| `GET` | `/renewals/{patient_id}/documents` | List documents for a renewal |
| `POST` | `/renewals/{patient_id}/check_renewal` | Check renewed eligibility with updated info |

### Phase 2 — Dashboard

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/dashboard/portfolio` | Portfolio summary with risk scores |
| `GET` | `/dashboard/alerts` | Patients needing attention, sorted by priority |
| `GET` | `/dashboard/metrics` | Pipeline health metrics |
| `POST` | `/dashboard/override/{renewal_id}` | Caseworker override with audit trail |

### Phase 2 — Outreach

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/outreach/history/{patient_id}` | Communication log |
| `POST` | `/outreach/opt_out/{patient_id}` | Process TCPA opt-out |

### Metrics Schema

The `/check` response and `/metrics` endpoint expose:

```json
{
  "llm_api_calls": 3,
  "llm_api_calls_breakdown": { "react_loop": 2, "qa_review": 1 },
  "tool_call_count": 2,
  "tool_names": ["read_query", "write_file"],
  "guardrail_match": true,
  "guardrail_details": {
    "engine_eligible": true,
    "llm_eligible": true,
    "category": "adult_expansion",
    "income_pct": 95.2,
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
| **Database** | PostgreSQL (Render managed) — patients, renewals, documents, audit_log |
| **Memory** | Mem0 SDK (direct Python, not MCP) |
| **Tool Protocol** | MCP (stdio transport) |
| **Deployment** | Render (render.yaml blueprint) |
| **CI/CD** | GitHub Actions (eval-gated) |
| **Frontend** | Vanilla HTML/CSS/JS (inline, no build step) |
| **Testing** | pytest (94 Phase 2 tests) + custom eval suite (16 patients) |
| **Evals** | npm scripts + Python |

## Project Structure

```
.
├── router.py             # Multi-agent orchestrator (default)
├── agent.py              # Monolith agent (legacy, still functional)
├── eligibility.py        # Deterministic engine (single source of truth)
├── prompts.py            # System prompt + QA prompt with embedded FPL data
├── config.py             # Constants, MCP configs, model pinning, Phase 2 settings
├── mcp_manager.py        # Multi-server MCP connection manager with retry
├── server.py             # FastAPI endpoints (Phase 1 + Phase 2)
├── seed_db.py            # Database schema + 16 seed patients + 16 renewal scenarios
├── agents/
│   ├── __init__.py               # All agent exports
│   ├── base.py                   # AgentResult, EvalResult base types
│   ├── eligibility_agent.py      # ReAct loop + renewal eligibility check
│   ├── memory_agent.py           # Mem0 SDK integration
│   ├── knowledge_agent.py        # FPL tables + state rules
│   ├── risk_scoring_agent.py     # Deterministic 5-factor risk scoring
│   ├── outreach_agent.py         # TCPA-compliant SMS sequences
│   ├── document_agent.py         # LLM classification + deterministic validation
│   ├── workflow_orchestrator.py  # 11-state renewal state machine
│   ├── caseworker_copilot.py     # Dashboard summaries + alerts
│   ├── eval_correctness.py       # Guardrail: engine vs LLM
│   ├── eval_efficiency.py        # API call count + banned tool check
│   ├── eval_quality.py           # QA agent review
│   ├── eval_risk_scoring.py      # Risk score determinism eval
│   ├── eval_outreach_compliance.py  # TCPA compliance eval
│   └── eval_workflow.py          # State machine validity eval
├── static/
│   ├── index.html                # Marketing site
│   ├── copilot.html              # Eligibility copilot UI
│   └── dashboard.html            # Caseworker dashboard UI
├── tests/
│   ├── test_risk_scoring.py      # 16 tests
│   ├── test_outreach_agent.py    # 20 tests
│   ├── test_workflow_orchestrator.py  # 32 tests
│   ├── test_document_agent.py    # 18 tests
│   └── test_renewal_eligibility.py   # 8 tests
├── evals/
│   └── test_eligibility.py       # 3-dimension eval suite (16 patients)
├── .github/
│   └── workflows/
│       └── regression-evals.yml  # Deterministic on push + agent evals daily
├── reports/                      # Saved eligibility determination reports
├── render.yaml                   # Render blueprint (eval-gated deployment)
├── package.json                  # npm scripts: eval, eval:agent, start, build
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

Open http://localhost:8000 for the marketing site, http://localhost:8000/dashboard for the caseworker dashboard, or http://localhost:8000/copilot for the standalone eligibility copilot.

## Deploying to Render

1. Push to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com/select-repo?type=blueprint)
3. Connect the repo — Render detects `render.yaml`
4. Set `OPENAI_API_KEY` and `MEM0_API_KEY` when prompted
5. Deploy

The blueprint creates a free web service and a free PostgreSQL database. Deterministic evals run during build — if they fail, deployment is blocked.

## License

MIT
