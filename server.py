"""FastAPI web server wrapping the Medicaid Eligibility Agent."""

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import MedicaidAgent
from config import DB_PATH, REPORTS_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

agent = MedicaidAgent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start MCP connections on startup, clean up on shutdown."""
    await agent.setup()
    yield
    await agent.cleanup()


app = FastAPI(
    title="Medicaid Eligibility Checker",
    description="AI agent that determines Medicaid eligibility using MCP servers",
    lifespan=lifespan,
)


class CheckRequest(BaseModel):
    query: str
    session_id: str = "default"


class CheckResponse(BaseModel):
    determination: str
    session_id: str


@app.get("/health")
async def health():
    return {"status": "ok", "mcp_servers": list(agent.mcp.sessions.keys())}


@app.get("/patients")
async def list_patients():
    """List all patients from the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM patients").fetchall()
        return {"patients": [dict(row) for row in rows]}
    finally:
        conn.close()


class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: str
    age: int
    state: str
    county: str | None = None
    household_size: int
    annual_income: float
    income_source: str | None = None
    is_pregnant: bool = False
    has_disability: bool = False
    is_us_citizen: bool = True
    immigration_status: str = "citizen"
    current_insurance: str | None = None


@app.post("/patients", status_code=201)
async def create_patient(patient: PatientCreate):
    """Add a new patient to the database."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.execute(
            """INSERT INTO patients (
                first_name, last_name, date_of_birth, age, state, county,
                household_size, annual_income, income_source,
                is_pregnant, has_disability, is_us_citizen,
                immigration_status, current_insurance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                patient.first_name,
                patient.last_name,
                patient.date_of_birth,
                patient.age,
                patient.state,
                patient.county,
                patient.household_size,
                patient.annual_income,
                patient.income_source,
                int(patient.is_pregnant),
                int(patient.has_disability),
                int(patient.is_us_citizen),
                patient.immigration_status,
                patient.current_insurance,
            ),
        )
        conn.commit()
        return {"id": cursor.lastrowid, **patient.model_dump()}
    finally:
        conn.close()


@app.get("/patients/{patient_id}")
async def get_patient(patient_id: int):
    """Get a specific patient by ID."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Patient not found")
        return dict(row)
    finally:
        conn.close()


@app.post("/check", response_model=CheckResponse)
async def check_eligibility(request: CheckRequest):
    """Run an eligibility check through the AI agent."""
    determination = await agent.process_query(
        request.query, session_id=request.session_id
    )
    return CheckResponse(
        determination=determination, session_id=request.session_id
    )


@app.post("/check/stream")
async def check_eligibility_stream(request: CheckRequest):
    """Run an eligibility check with streaming response."""

    async def generate():
        async for chunk in agent.process_query_stream(
            request.query, session_id=request.session_id
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/plain")


@app.post("/check/{patient_id}", response_model=CheckResponse)
async def check_patient_by_id(patient_id: int, session_id: str = "default"):
    """Run an eligibility check for a specific patient by ID."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Patient not found")
    finally:
        conn.close()

    query = f"Check Medicaid eligibility for patient ID {patient_id}"
    determination = await agent.process_query(query, session_id=session_id)
    return CheckResponse(determination=determination, session_id=session_id)


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Retrieve conversation history for a session."""
    messages = agent.conversations.get(session_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Serialize messages — content blocks may be Anthropic objects
    history = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if isinstance(content, str):
            history.append({"role": role, "content": content})
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item)
                elif hasattr(item, "text"):
                    parts.append({"type": "text", "text": item.text})
                elif hasattr(item, "type"):
                    parts.append({"type": item.type, "name": getattr(item, "name", None)})
            history.append({"role": role, "content": parts})

    return {"session_id": session_id, "messages": history}


@app.get("/sessions")
async def list_sessions():
    """List all active session IDs."""
    return {
        "sessions": [
            {"session_id": sid, "message_count": len(msgs)}
            for sid, msgs in agent.conversations.items()
        ]
    }


@app.get("/reports")
async def list_reports():
    """List all saved determination reports."""
    reports = sorted(REPORTS_DIR.glob("*.md"))
    return {"reports": [r.name for r in reports]}


@app.get("/reports/{filename}")
async def get_report(filename: str):
    """Get a specific determination report."""
    # Prevent path traversal
    safe_name = Path(filename).name
    report_path = REPORTS_DIR / safe_name
    if not report_path.exists() or not report_path.suffix == ".md":
        raise HTTPException(status_code=404, detail="Report not found")
    return {"filename": safe_name, "content": report_path.read_text()}
