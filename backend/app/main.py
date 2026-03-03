from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
import csv
import io

from app.database import get_db, engine, Base
from app.models import Lead, Campaign, CampaignLead, AuditLog
from app.schemas import LeadOut, CampaignCreate, CampaignOut, AuditLogOut
from app.services.lead_service import LeadService
from app.workers.tasks import launch_campaign as launch_campaign_task

# Create all tables on startup (use Alembic in production)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Sales Cadence Engine - Stage 1", version="1.0.0")

@app.get("/", include_in_schema=False)
def root():
    return {"message": "Sales Cadence Engine backend"}

@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── LEADS ────────────────────────────────────────────────────────────────────

@app.post("/api/v1/leads/upload")
async def upload_leads(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload CSV file, parse leads, store in DB."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Only CSV files accepted")

    content = await file.read()
    service = LeadService(db)
    result = service.ingest_csv(content)
    return result


@app.get("/api/v1/leads/", response_model=List[LeadOut])
def list_leads(db: Session = Depends(get_db)):
    return db.query(Lead).all()


@app.post("/api/v1/leads/{lead_id}/dnc")
def mark_dnc(lead_id: str, reason: str = "user_request", db: Session = Depends(get_db)):
    lead = db.query(Lead).filter_by(id=lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    lead.is_dnc = True
    lead.dnc_reason = reason
    db.commit()
    return {"message": f"{lead.name} marked as DNC"}


# ── CAMPAIGNS ────────────────────────────────────────────────────────────────

@app.post("/api/v1/campaigns/", response_model=CampaignOut)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    """Create a campaign and assign leads to it."""
    campaign = Campaign(
        name=payload.name,
        cadence_config={
            "max_call_retries": payload.max_call_retries,
            "retry_delay_seconds": payload.retry_delay_seconds,
            "send_email_after_calls": True,
        }
    )
    db.add(campaign)
    db.flush()  # get campaign.id before commit

    # Attach leads
    for lead_id in payload.lead_ids:
        lead = db.query(Lead).filter_by(id=lead_id).first()
        if lead:
            cl = CampaignLead(campaign_id=campaign.id, lead_id=lead.id)
            db.add(cl)

    db.commit()
    db.refresh(campaign)
    return campaign


@app.post("/api/v1/campaigns/{campaign_id}/start")
def start_campaign(campaign_id: str, db: Session = Depends(get_db)):
    """Start cadence execution for all leads in a campaign."""
    campaign = db.query(Campaign).filter_by(id=campaign_id).first()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if campaign.status == "active":
        raise HTTPException(400, "Campaign already running")

    # Fire the Celery task — returns immediately, work happens async
    task = launch_campaign_task.delay(campaign_id)

    return {
        "message": f"Campaign '{campaign.name}' started",
        "task_id": task.id,
        "tip": f"Monitor at http://localhost:5555"
    }


@app.get("/api/v1/campaigns/", response_model=List[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    return db.query(Campaign).all()


@app.get("/api/v1/campaigns/{campaign_id}/logs", response_model=List[AuditLogOut])
def get_campaign_logs(campaign_id: str, db: Session = Depends(get_db)):
    """Get all audit logs for a campaign, newest first."""
    logs = (
        db.query(AuditLog)
        .filter_by(campaign_id=campaign_id)
        .order_by(AuditLog.attempted_at.desc())
        .all()
    )
    return logs