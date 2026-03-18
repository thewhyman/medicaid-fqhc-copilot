"""FastAPI web server wrapping the Medicaid Eligibility Agent."""

import decimal
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import DATABASE_URL, REPORTS_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

USE_ROUTER = os.environ.get("USE_ROUTER", "true").lower() == "true"
if USE_ROUTER:
    from router import Router
    agent = Router()
    logger.info("Using multi-agent Router")
else:
    from agent import MedicaidAgent
    agent = MedicaidAgent()
    logger.info("Using monolith MedicaidAgent")


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start MCP connections on startup, clean up on shutdown."""
    await agent.setup(db_url=DATABASE_URL)
    yield
    await agent.cleanup()


app = FastAPI(
    title="MediAssist AI",
    description="Coverage continuity infrastructure — AI copilot for FQHC caseworkers determining Medicaid eligibility",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Serve the MediAssist AI home page."""
    return FileResponse("static/index.html")


@app.get("/copilot")
async def copilot():
    """Serve the FQHC Copilot UI."""
    return FileResponse("static/copilot.html")


@app.get("/dashboard")
async def dashboard():
    """Serve the Renewal Dashboard UI."""
    return FileResponse("static/dashboard.html")


app.mount("/static", StaticFiles(directory="static"), name="static")


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
    """List all patients from the PostgreSQL database."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM patients")
            rows = cur.fetchall()
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
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO patients (
                    first_name, last_name, date_of_birth, age, state, county,
                    household_size, annual_income, income_source,
                    is_pregnant, has_disability, is_us_citizen,
                    immigration_status, current_insurance
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
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
                    patient.is_pregnant,
                    patient.has_disability,
                    patient.is_us_citizen,
                    patient.immigration_status,
                    patient.current_insurance,
                ),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return {"id": new_id, **patient.model_dump()}
    finally:
        conn.close()


@app.get("/patients/{patient_id}")
async def get_patient(patient_id: int):
    """Get a specific patient by ID."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Patient not found")
        return dict(row)
    finally:
        conn.close()


@app.post("/check")
async def check_eligibility(request: CheckRequest):
    """Run an eligibility check through the AI agent."""
    determination = await agent.process_query(
        request.query, session_id=request.session_id
    )
    return {
        "determination": determination,
        "session_id": request.session_id,
        "metrics": agent.last_query_metrics,
    }


@app.post("/check/stream")
async def check_eligibility_stream(request: CheckRequest):
    """Run an eligibility check with streaming response."""

    async def generate():
        async for chunk in agent.process_query_stream(
            request.query, session_id=request.session_id
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/plain")


@app.get("/metrics")
async def get_last_metrics():
    """Return metrics from the most recent query (guardrail, QA, latency, etc.)."""
    return agent.last_query_metrics


@app.post("/check/{patient_id}", response_model=CheckResponse)
async def check_patient_by_id(patient_id: int, session_id: str = "default"):
    """Run an eligibility check for a specific patient by ID."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Patient not found")
    finally:
        conn.close()

    query = f"Check Medicaid eligibility for patient ID {patient_id}"
    determination = await agent.process_query(query, session_id=session_id)
    return CheckResponse(determination=determination, session_id=session_id)


@app.get("/patients/{patient_id}/sessions")
async def get_patient_sessions(patient_id: int):
    """List saved conversation sessions for a patient."""
    return {"sessions": agent.list_patient_sessions(patient_id)}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Retrieve conversation history for a session (in-memory or Postgres)."""
    # Try in-memory first
    messages = agent.conversations.get(session_id)

    # Fall back to Postgres
    if messages is None:
        messages = agent.load_conversation(session_id)

    if messages is None:
        raise HTTPException(status_code=404, detail="Session not found")

    history = []
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
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
    safe_name = Path(filename).name
    report_path = REPORTS_DIR / safe_name
    if not report_path.exists() or not report_path.suffix == ".md":
        raise HTTPException(status_code=404, detail="Report not found")
    return {"filename": safe_name, "content": report_path.read_text()}


# ------------------------------------------------------------------
# Phase 2: Renewal endpoints
# ------------------------------------------------------------------

def _get_renewal(conn, renewal_id: int) -> dict:
    """Fetch a single renewal by ID."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM renewals WHERE id = %s", (renewal_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Renewal not found")
    return dict(row)


def _get_patient_by_id(conn, patient_id: int) -> dict:
    """Fetch a single patient by ID."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Patient not found")
    return dict(row)


def _serialize(obj: dict) -> dict:
    """Convert non-JSON-serializable values (date, datetime, Decimal) to strings."""
    result = {}
    for k, v in obj.items():
        if isinstance(v, (date, datetime)):
            result[k] = v.isoformat()
        elif isinstance(v, decimal.Decimal):
            result[k] = float(v)
        else:
            result[k] = v
    return result


@app.post("/renewals/{patient_id}/start")
async def start_renewal(patient_id: int):
    """Initiate a renewal workflow for a patient."""
    if not USE_ROUTER:
        raise HTTPException(status_code=501, detail="Renewal requires multi-agent Router")
    conn = get_db()
    try:
        patient = _get_patient_by_id(conn, patient_id)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM renewals WHERE patient_id = %s AND current_step NOT IN ('COMPLETED', 'EXPIRED') ORDER BY created_at DESC LIMIT 1",
                (patient_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No active renewal found for patient")
        renewal = dict(row)

        result = agent.start_renewal(patient, renewal)

        risk = result.get("risk", {})
        transition = result.get("transition", {})
        new_state = transition.get("new_state", renewal["current_step"])
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE renewals SET risk_score = %s, risk_tier = %s, risk_factors = %s::jsonb, current_step = %s, updated_at = NOW() WHERE id = %s",
                (risk.get("score"), risk.get("tier"),
                 json.dumps(risk.get("factors", [])), new_state, renewal["id"]),
            )
        conn.commit()
        return result
    finally:
        conn.close()


@app.get("/renewals/{patient_id}/status")
async def get_renewal_status(patient_id: int):
    """Get current renewal state + timeline for a patient."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM renewals WHERE patient_id = %s ORDER BY created_at DESC LIMIT 1",
                (patient_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No renewal found for patient")
        renewal = dict(row)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM audit_log WHERE renewal_id = %s ORDER BY timestamp",
                (renewal["id"],),
            )
            timeline = [dict(r) for r in cur.fetchall()]

        return {"renewal": _serialize(renewal), "timeline": [_serialize(t) for t in timeline]}
    finally:
        conn.close()


@app.get("/renewals/pipeline")
async def get_renewal_pipeline():
    """Get all patients grouped by workflow state."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT r.*, p.first_name, p.last_name, p.state, p.age
                FROM renewals r JOIN patients p ON r.patient_id = p.id
                ORDER BY r.renewal_due_date
            """)
            rows = [dict(r) for r in cur.fetchall()]

        pipeline = {}
        for row in rows:
            step = row.get("current_step", "IDENTIFIED")
            pipeline.setdefault(step, []).append(_serialize(row))

        return {"pipeline": pipeline, "total": len(rows)}
    finally:
        conn.close()


class RenewalEventRequest(BaseModel):
    event: str
    data: dict | None = None


@app.post("/renewals/{patient_id}/event")
async def process_renewal_event(patient_id: int, request: RenewalEventRequest):
    """Trigger a state transition in the renewal workflow."""
    if not USE_ROUTER:
        raise HTTPException(status_code=501, detail="Renewal requires multi-agent Router")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM renewals WHERE patient_id = %s AND current_step NOT IN ('COMPLETED', 'EXPIRED') ORDER BY created_at DESC LIMIT 1",
                (patient_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No active renewal found")
        renewal = dict(row)

        result = agent.process_renewal_event(renewal, request.event, request.data)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        new_state = result.get("new_state", renewal["current_step"])
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE renewals SET current_step = %s, updated_at = NOW() WHERE id = %s",
                (new_state, renewal["id"]),
            )
            cur.execute(
                "INSERT INTO audit_log (patient_id, renewal_id, actor, action, details) VALUES (%s, %s, %s, %s, %s::jsonb)",
                (patient_id, renewal["id"], "workflow_orchestrator", "state_transition",
                 json.dumps({"from": renewal["current_step"], "to": new_state, "event": request.event})),
            )
        conn.commit()
        return result
    finally:
        conn.close()


class DocumentUploadRequest(BaseModel):
    document_text: str


@app.post("/renewals/{patient_id}/documents")
async def upload_document(patient_id: int, request: DocumentUploadRequest):
    """Upload and process a document for a patient's renewal."""
    if not USE_ROUTER:
        raise HTTPException(status_code=501, detail="Renewal requires multi-agent Router")
    conn = get_db()
    try:
        patient = _get_patient_by_id(conn, patient_id)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM renewals WHERE patient_id = %s AND current_step NOT IN ('COMPLETED', 'EXPIRED') ORDER BY created_at DESC LIMIT 1",
                (patient_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No active renewal found")
        renewal = dict(row)

        result = agent.process_document(request.document_text, patient)

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents (renewal_id, document_type, status, extracted_data, confidence)
                   VALUES (%s, %s, %s, %s::jsonb, %s) RETURNING id""",
                (renewal["id"], result.get("document_type"), result.get("status"),
                 json.dumps(result.get("extracted_data", {})), result.get("confidence")),
            )
            doc_id = cur.fetchone()[0]
        conn.commit()

        return {"document_id": doc_id, **result}
    finally:
        conn.close()


@app.get("/renewals/{patient_id}/documents")
async def list_documents(patient_id: int):
    """List all documents for a patient's renewal."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT d.* FROM documents d
                JOIN renewals r ON d.renewal_id = r.id
                WHERE r.patient_id = %s
                ORDER BY d.upload_timestamp
            """, (patient_id,))
            rows = [dict(r) for r in cur.fetchall()]
        return {"documents": [_serialize(r) for r in rows]}
    finally:
        conn.close()


class RenewalEligibilityRequest(BaseModel):
    updated_info: dict


@app.post("/renewals/{patient_id}/check_renewal")
async def check_renewal_eligibility(patient_id: int, request: RenewalEligibilityRequest):
    """Check renewed eligibility with updated patient information."""
    if not USE_ROUTER:
        raise HTTPException(status_code=501, detail="Renewal requires multi-agent Router")
    conn = get_db()
    try:
        patient = _get_patient_by_id(conn, patient_id)
        from agents.eligibility_agent import EligibilityAgent
        result = EligibilityAgent.check_renewal_eligibility(patient, request.updated_info)
        return result.data
    finally:
        conn.close()


# ------------------------------------------------------------------
# Phase 2: Dashboard endpoints
# ------------------------------------------------------------------

@app.get("/dashboard/portfolio")
async def get_dashboard_portfolio():
    """Get caseworker portfolio summary with risk scores."""
    if not USE_ROUTER:
        raise HTTPException(status_code=501, detail="Dashboard requires multi-agent Router")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT r.*, p.first_name, p.last_name, p.state, p.age
                FROM renewals r JOIN patients p ON r.patient_id = p.id
            """)
            renewals = [dict(r) for r in cur.fetchall()]
        result = agent.get_dashboard(renewals)
        return result
    finally:
        conn.close()


@app.get("/dashboard/alerts")
async def get_dashboard_alerts():
    """Get patients needing attention, sorted by priority."""
    if not USE_ROUTER:
        raise HTTPException(status_code=501, detail="Dashboard requires multi-agent Router")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT r.*, p.first_name, p.last_name, p.state, p.age
                FROM renewals r JOIN patients p ON r.patient_id = p.id
                WHERE r.current_step NOT IN ('COMPLETED', 'EXPIRED')
            """)
            renewals = [dict(r) for r in cur.fetchall()]
        alerts_result = agent.caseworker_copilot.get_alerts(renewals)
        return alerts_result.data
    finally:
        conn.close()


@app.get("/dashboard/metrics")
async def get_dashboard_metrics():
    """Get pipeline health metrics."""
    if not USE_ROUTER:
        raise HTTPException(status_code=501, detail="Dashboard requires multi-agent Router")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM renewals")
            renewals = [dict(r) for r in cur.fetchall()]
        portfolio = agent.caseworker_copilot.get_portfolio_summary(renewals)
        return portfolio.data
    finally:
        conn.close()


class OverrideRequest(BaseModel):
    caseworker: str
    reason: str
    new_state: str | None = None


@app.post("/dashboard/override/{renewal_id}")
async def override_renewal(renewal_id: int, request: OverrideRequest):
    """Caseworker overrides an agent decision."""
    if not USE_ROUTER:
        raise HTTPException(status_code=501, detail="Dashboard requires multi-agent Router")
    conn = get_db()
    try:
        renewal = _get_renewal(conn, renewal_id)
        result = agent.caseworker_copilot.process_override(
            renewal_id, request.model_dump()
        )
        if not result.success:
            raise HTTPException(status_code=400, detail=result.error)

        if request.new_state:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE renewals SET current_step = %s, updated_at = NOW() WHERE id = %s",
                    (request.new_state, renewal_id),
                )
                cur.execute(
                    "INSERT INTO audit_log (patient_id, renewal_id, actor, action, details) VALUES (%s, %s, %s, %s, %s::jsonb)",
                    (renewal["patient_id"], renewal_id, request.caseworker, "caseworker_override",
                     json.dumps({"reason": request.reason, "new_state": request.new_state})),
                )
            conn.commit()
        return result.data
    finally:
        conn.close()


# ------------------------------------------------------------------
# Phase 2: Outreach endpoints
# ------------------------------------------------------------------

@app.get("/outreach/history/{patient_id}")
async def get_outreach_history(patient_id: int):
    """Get communication history for a patient."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT communication_log FROM renewals WHERE patient_id = %s ORDER BY created_at DESC LIMIT 1",
                (patient_id,),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No renewal found for patient")
        return {"communication_log": row["communication_log"]}
    finally:
        conn.close()


@app.post("/outreach/opt_out/{patient_id}")
async def process_opt_out(patient_id: int):
    """Process a TCPA opt-out for a patient."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE patients SET consent_status = 'opted_out' WHERE id = %s",
                (patient_id,),
            )
            cur.execute(
                "UPDATE renewals SET consent_status = 'opted_out' WHERE patient_id = %s AND current_step NOT IN ('COMPLETED', 'EXPIRED')",
                (patient_id,),
            )
            cur.execute(
                "INSERT INTO audit_log (patient_id, actor, action, details) VALUES (%s, %s, %s, %s::jsonb)",
                (patient_id, "outreach_agent", "opt_out_processed",
                 json.dumps({"reason": "Patient requested opt-out"})),
            )
        conn.commit()
        return {"status": "opted_out", "patient_id": patient_id}
    finally:
        conn.close()
