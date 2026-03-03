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

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    name = Column(String(255), nullable=False)
    company = Column(String(255))
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20), nullable=False)       # normalized: +91XXXXXXXXXX
    language = Column(String(50), default="english")
    notes = Column(Text)
    is_dnc = Column(Boolean, default=False)          # Do Not Call flag
    dnc_reason = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    campaign_leads = relationship("CampaignLead", back_populates="lead")
    audit_logs = relationship("AuditLog", back_populates="lead")


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    name = Column(String(255), nullable=False)
    status = Column(String(50), default="draft")     # draft | active | paused | completed
    # Cadence config stored as JSON — no migration needed to change rules
    cadence_config = Column(JSON, default={
        "max_call_retries": 3,
        "retry_delay_seconds": 30,
        "send_email_after_calls": True
    })
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    campaign_leads = relationship("CampaignLead", back_populates="campaign")


class CampaignLead(Base):
    """
    Junction table: one row per (campaign, lead) pair.
    Tracks the FSM state for each lead within a campaign.
    """
    __tablename__ = "campaign_leads"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    campaign_id = Column(UUID(as_uuid=False), ForeignKey("campaigns.id"), nullable=False)
    lead_id = Column(UUID(as_uuid=False), ForeignKey("leads.id"), nullable=False)

    # FSM state for this lead in this campaign
    current_state = Column(String(100), default="pending")
    # pending | call_scheduled | call_in_progress | call_no_answer
    # call_answered | email_scheduled | completed | dnc_blocked

    attempt_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    campaign = relationship("Campaign", back_populates="campaign_leads")
    lead = relationship("Lead", back_populates="campaign_leads")
    audit_logs = relationship("AuditLog", back_populates="campaign_lead")


class AuditLog(Base):
    """
    Append-only log. NEVER update rows here — only INSERT.
    This is your source of truth for what happened.
    """
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    campaign_lead_id = Column(UUID(as_uuid=False), ForeignKey("campaign_leads.id"))
    lead_id = Column(UUID(as_uuid=False), ForeignKey("leads.id"))
    campaign_id = Column(UUID(as_uuid=False), ForeignKey("campaigns.id"))

    attempt_type = Column(String(50), nullable=False)   # call | email
    status = Column(String(50), nullable=False)          # success | no_answer | failed | skipped_dnc | skipped_hours
    metadata_ = Column("metadata", JSON, default={})     # extra info: error msg, duration, etc.
    attempted_at = Column(DateTime, default=datetime.utcnow)

    campaign_lead = relationship("CampaignLead", back_populates="audit_logs")
    lead = relationship("Lead", back_populates="audit_logs")