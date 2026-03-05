"""
models.py — SQLAlchemy ORM models
New in Stage 2: TaskRecord table for result backend tracking
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base

def new_uuid():
    return str(uuid.uuid4())


class Lead(Base):
    __tablename__ = "leads"

    id          = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    name        = Column(String(255), nullable=False)
    company     = Column(String(255))
    email       = Column(String(255), unique=True, nullable=False)
    phone       = Column(String(20), nullable=False)
    language    = Column(String(50), default="english")
    notes       = Column(Text)
    is_dnc      = Column(Boolean, default=False)
    dnc_reason  = Column(String(255))
    created_at  = Column(DateTime, default=datetime.utcnow)

    campaign_leads = relationship("CampaignLead", back_populates="lead")
    audit_logs     = relationship("AuditLog", back_populates="lead")


class Campaign(Base):
    __tablename__ = "campaigns"

    id             = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    name           = Column(String(255), nullable=False)
    status         = Column(String(50), default="draft")
    cadence_config = Column(JSON, default=lambda: {
        "max_call_retries": 3,
        "retry_delay_seconds": 15,
    })
    created_at   = Column(DateTime, default=datetime.utcnow)
    started_at   = Column(DateTime)
    completed_at = Column(DateTime)

    campaign_leads = relationship("CampaignLead", back_populates="campaign")


class CampaignLead(Base):
    """One row per (campaign × lead). Tracks FSM state."""
    __tablename__ = "campaign_leads"

    id            = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    campaign_id   = Column(UUID(as_uuid=False), ForeignKey("campaigns.id"), nullable=False)
    lead_id       = Column(UUID(as_uuid=False), ForeignKey("leads.id"), nullable=False)
    current_state = Column(String(100), default="pending")
    attempt_count = Column(Integer, default=0)
    is_active     = Column(Boolean, default=True)

    # Stage 2: track the root Celery task_id for this lead's workflow
    workflow_task_id = Column(String(255))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    campaign   = relationship("Campaign", back_populates="campaign_leads")
    lead       = relationship("Lead", back_populates="campaign_leads")
    audit_logs = relationship("AuditLog", back_populates="campaign_lead")


class AuditLog(Base):
    """Append-only. Never UPDATE rows here."""
    __tablename__ = "audit_logs"

    id               = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    campaign_lead_id = Column(UUID(as_uuid=False), ForeignKey("campaign_leads.id"))
    lead_id          = Column(UUID(as_uuid=False), ForeignKey("leads.id"))
    campaign_id      = Column(UUID(as_uuid=False), ForeignKey("campaigns.id"))
    attempt_type     = Column(String(50), nullable=False)
    status           = Column(String(50), nullable=False)
    metadata_        = Column("metadata", JSON, default=dict)
    attempted_at     = Column(DateTime, default=datetime.utcnow)

    campaign_lead = relationship("CampaignLead", back_populates="audit_logs")
    lead          = relationship("Lead", back_populates="audit_logs")


class TaskRecord(Base):
    """
    Stage 2 addition: persists every Celery task_id + its result.
    Allows UI to poll GET /api/v1/tasks/{task_id} for live status.
    This is the 'result backend usage' requirement.
    """
    __tablename__ = "task_records"

    id              = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    task_id         = Column(String(255), unique=True, nullable=False, index=True)
    task_name       = Column(String(255))          # e.g. "validate_lead"
    campaign_id     = Column(UUID(as_uuid=False), ForeignKey("campaigns.id"), nullable=True)
    lead_id         = Column(UUID(as_uuid=False), ForeignKey("leads.id"), nullable=True)

    # PENDING | STARTED | SUCCESS | FAILURE | RETRY
    status          = Column(String(50), default="PENDING")
    result_payload  = Column(JSON, default=dict)   # stores final result or error
    error_message   = Column(Text)
    retry_count     = Column(Integer, default=0)

    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow)







































