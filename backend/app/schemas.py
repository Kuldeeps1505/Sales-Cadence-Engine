from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime

class LeadOut(BaseModel):
    id: str
    name: str
    company: Optional[str]
    email: str
    phone: str
    language: str
    is_dnc: bool
    created_at: datetime

    class Config:
        from_attributes = True

class CampaignCreate(BaseModel):
    name: str
    lead_ids: List[str]
    max_call_retries: int = 3
    retry_delay_seconds: int = 30

class CampaignOut(BaseModel):
    id: str
    name: str
    status: str
    cadence_config: dict
    created_at: datetime

    class Config:
        from_attributes = True

class AuditLogOut(BaseModel):
    id: str
    lead_id: str
    attempt_type: str
    status: str
    metadata_: dict
    attempted_at: datetime

    class Config:
        from_attributes = True