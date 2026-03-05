"""
main.py — Stage 2 FastAPI
New endpoints:
  POST /api/v1/campaigns/{id}/run-workflow  → triggers full chain/group/chord
  GET  /api/v1/tasks/{task_id}              → polls Celery result backend
  GET  /api/v1/campaigns/{id}/report        → fetches combined report
  GET  /api/v1/campaigns/{id}/workflow-status → per-lead workflow states
"""
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional
from celery.result import AsyncResult

from app.database import get_db, engine, Base
from app.models import Lead, Campaign, CampaignLead, AuditLog, TaskRecord
from app.schemas import LeadOut, CampaignCreate, CampaignOut, AuditLogOut
from app.services.lead_service import LeadService
from app.workers.tasks import launch_campaign as launch_campaign_task
from app.workers.celery_app import app as celery_app

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sales Cadence Engine — Stage 2", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://frontend:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── HEALTH ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0", "features": ["chain", "group", "chord", "result_backend"]}


# ── LEADS ─────────────────────────────────────────────────────────────────────
@app.post("/api/v1/leads/upload")
async def upload_leads(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Only CSV files accepted")
    content = await file.read()
    return LeadService(db).ingest_csv(content)

@app.get("/api/v1/leads/", response_model=List[LeadOut])
def list_leads(db: Session = Depends(get_db)):
    return db.query(Lead).order_by(Lead.created_at.desc()).all()

@app.post("/api/v1/leads/{lead_id}/dnc")
def mark_dnc(lead_id: str, reason: str = "user_request", db: Session = Depends(get_db)):
    lead = db.query(Lead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    lead.is_dnc     = True
    lead.dnc_reason = reason
    db.commit()
    return {"message": f"{lead.name} marked as DNC"}


# ── CAMPAIGNS ─────────────────────────────────────────────────────────────────
@app.post("/api/v1/campaigns/", response_model=CampaignOut)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    campaign = Campaign(
        name=payload.name,
        cadence_config={
            "max_call_retries":    payload.max_call_retries,
            "retry_delay_seconds": payload.retry_delay_seconds,
        }
    )
    db.add(campaign)
    db.flush()
    for lead_id in payload.lead_ids:
        if db.query(Lead).filter_by(id=lead_id).first():
            db.add(CampaignLead(campaign_id=campaign.id, lead_id=lead_id))
    db.commit()
    db.refresh(campaign)
    return campaign

@app.get("/api/v1/campaigns/", response_model=List[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    return db.query(Campaign).order_by(Campaign.created_at.desc()).all()

@app.post("/api/v1/campaigns/{campaign_id}/start")
def start_campaign(campaign_id: str, db: Session = Depends(get_db)):
    """Original simple launch (Stage 1 compatible)."""
    campaign = db.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if campaign.status == "active":
        raise HTTPException(400, "Already running")
    task = launch_campaign_task.delay(campaign_id)
    return {"message": f"Campaign '{campaign.name}' started", "task_id": task.id}

@app.post("/api/v1/campaigns/{campaign_id}/run-workflow")
def run_full_workflow(campaign_id: str, db: Session = Depends(get_db)):
    """
    Stage 2: Triggers full chain → group → chord orchestration.
    Each lead gets: chord(parallel validations) → chain(call → email → finalize)
    """
    campaign = db.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if campaign.status == "active":
        raise HTTPException(400, "Already running")

    task = launch_campaign_task.delay(campaign_id)

    # Register root task
    db.add(TaskRecord(
        task_id=task.id,
        task_name="launch_campaign",
        campaign_id=campaign_id,
        status="PENDING",
    ))
    db.commit()

    return {
        "message": f"Full workflow started for '{campaign.name}'",
        "task_id": task.id,
        "workflow": {
            "step1": "chord(validate_lead + check_dnc + verify_email) → aggregate",
            "step2": "chain(simulate_call → send_real_email → finalize_lead)",
            "step3": "chord(report_calls + report_emails + report_conversion) → combine",
        },
        "monitor": "http://localhost:5555",
    }

@app.get("/api/v1/campaigns/{campaign_id}/logs")
def get_logs(campaign_id: str, db: Session = Depends(get_db)):
    logs = (
        db.query(AuditLog)
        .filter_by(campaign_id=campaign_id)
        .order_by(AuditLog.attempted_at.desc())
        .all()
    )
    return [
        {
            "id":           l.id,
            "lead_id":      l.lead_id,
            "attempt_type": l.attempt_type,
            "status":       l.status,
            "metadata_":    l.metadata_ or {},
            "attempted_at": l.attempted_at.isoformat() if l.attempted_at else None,
        }
        for l in logs
    ]

@app.get("/api/v1/campaigns/{campaign_id}/workflow-status")
def get_workflow_status(campaign_id: str, db: Session = Depends(get_db)):
    """Per-lead workflow states for the live workflow tracker UI."""
    cls = (
        db.query(CampaignLead)
        .filter_by(campaign_id=campaign_id)
        .all()
    )
    return [
        {
            "campaign_lead_id": cl.id,
            "lead_id":          cl.lead_id,
            "lead_name":        cl.lead.name,
            "lead_email":       cl.lead.email,
            "current_state":    cl.current_state,
            "attempt_count":    cl.attempt_count,
            "is_active":        cl.is_active,
            "workflow_task_id": cl.workflow_task_id,
        }
        for cl in cls
    ]

@app.get("/api/v1/campaigns/{campaign_id}/report")
def get_campaign_report(campaign_id: str, db: Session = Depends(get_db)):
    """Manually trigger & return a campaign report (sync version for UI)."""
    logs = db.query(AuditLog).filter_by(campaign_id=campaign_id).all()
    cls  = db.query(CampaignLead).filter_by(campaign_id=campaign_id).all()

    call_logs  = [l for l in logs if l.attempt_type == "call"]
    email_logs = [l for l in logs if l.attempt_type == "email"]

    return {
        "calls": {
            "total":       len(call_logs),
            "answered":    sum(1 for l in call_logs if l.status == "success"),
            "no_answer":   sum(1 for l in call_logs if l.status == "no_answer"),
            "skipped_dnc": sum(1 for l in call_logs if l.status == "skipped_dnc"),
            "failed":      sum(1 for l in call_logs if l.status == "failed"),
        },
        "emails": {
            "total":  len(email_logs),
            "sent":   sum(1 for l in email_logs if l.status == "success"),
            "failed": sum(1 for l in email_logs if l.status == "failed"),
        },
        "leads": {
            "total":     len(cls),
            "completed": sum(1 for c in cls if c.current_state == "completed"),
            "active":    sum(1 for c in cls if c.is_active),
            "blocked":   sum(1 for c in cls if c.current_state in ("dnc_blocked", "validation_failed")),
        },
    }


# ── TASK RESULT BACKEND ───────────────────────────────────────────────────────
@app.get("/api/v1/tasks/{task_id}")
def get_task_status(task_id: str, db: Session = Depends(get_db)):
    """
    Polls BOTH Redis result backend (Celery) AND PostgreSQL TaskRecord.
    UI uses this to show real-time task state.

    States: PENDING | STARTED | SUCCESS | FAILURE | RETRY
    """
    # 1. Check Redis (real-time Celery state)
    celery_result = AsyncResult(task_id, app=celery_app)
    celery_state  = celery_result.state

    # 2. Check PostgreSQL (our persisted record)
    db_record = db.query(TaskRecord).filter_by(task_id=task_id).first()

    return {
        "task_id":      task_id,
        # Redis state is most up-to-date
        "status":       celery_state,
        "result":       celery_result.result if celery_state == "SUCCESS" else None,
        "error":        str(celery_result.result) if celery_state == "FAILURE" else None,
        # DB record has extra metadata
        "task_name":    db_record.task_name    if db_record else None,
        "retry_count":  db_record.retry_count  if db_record else 0,
        "db_status":    db_record.status       if db_record else "NOT_REGISTERED",
        "created_at":   db_record.created_at.isoformat() if db_record and db_record.created_at else None,
    }

@app.get("/api/v1/tasks/")
def list_tasks(
    campaign_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """List all tracked tasks, optionally filtered."""
    q = db.query(TaskRecord)
    if campaign_id:
        q = q.filter_by(campaign_id=campaign_id)
    if status:
        q = q.filter_by(status=status)
    tasks = q.order_by(TaskRecord.created_at.desc()).limit(limit).all()
    return [
        {
            "task_id":     t.task_id,
            "task_name":   t.task_name,
            "status":      t.status,
            "retry_count": t.retry_count,
            "lead_id":     t.lead_id,
            "created_at":  t.created_at.isoformat() if t.created_at else None,
            "updated_at":  t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in tasks
    ]


























