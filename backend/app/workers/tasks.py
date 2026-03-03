"""
THE HEART OF STAGE 1 — Read this file carefully.

Task flow for each lead:
  launch_campaign
      └── process_cadence_step  (per lead)
              └── simulate_call
                      ├── [answered]    → simulate_email (follow-up)
                      ├── [no answer]   → process_cadence_step (retry after delay)
                      └── [max retries] → simulate_email (fallback)
"""
import random
import time
from datetime import datetime
from celery.utils.log import get_task_logger
from app.workers.celery_app import celery
from app.database import get_db_session
from app.models import CampaignLead, AuditLog, Campaign
from app.services.complainace_service import ComplianceService
from app.config import settings

logger = get_task_logger(__name__)
compliance = ComplianceService()


def _write_audit_log(db, campaign_lead, attempt_type: str, status: str, metadata: dict = {}):
    """Helper: always use this to write audit logs. Never skip logging."""
    log = AuditLog(
        campaign_lead_id=campaign_lead.id,
        lead_id=campaign_lead.lead_id,
        campaign_id=campaign_lead.campaign_id,
        attempt_type=attempt_type,
        status=status,
        metadata_=metadata,
    )
    db.add(log)
    db.commit()
    logger.info(f"[AUDIT] lead={campaign_lead.lead_id} type={attempt_type} status={status}")


def _update_state(db, campaign_lead, new_state: str):
    """Helper: update the FSM state for a campaign_lead row."""
    campaign_lead.current_state = new_state
    campaign_lead.updated_at = datetime.utcnow()
    db.commit()


@celery.task(
    bind=True,
    max_retries=0,           # We handle retries manually via new task dispatch
    queue="cadence",
    name="launch_campaign"
)
def launch_campaign(self, campaign_id: str):
    """
    Entry point: called once per campaign start.
    Fans out — creates one process_cadence_step task per lead.
    """
    db = get_db_session()
    try:
        campaign = db.query(Campaign).filter_by(id=campaign_id).first()
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found")
            return

        campaign.status = "active"
        campaign.started_at = datetime.utcnow()
        db.commit()

        campaign_leads = campaign.campaign_leads
        logger.info(f"Launching campaign '{campaign.name}' for {len(campaign_leads)} leads")

        for cl in campaign_leads:
            # Fan out: one task per lead, staggered by 2s to avoid thundering herd
            process_cadence_step.apply_async(
                args=[cl.id],
                countdown=2 * campaign_leads.index(cl)  # stagger start times
            )
    finally:
        db.close()


@celery.task(
    bind=True,
    max_retries=0,
    queue="cadence",
    name="process_cadence_step"
)
def process_cadence_step(self, campaign_lead_id: str):
    """
    Orchestrator task: decides what to do next for a specific lead.
    This is the state machine executor.
    """
    db = get_db_session()
    try:
        cl = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        if not cl or not cl.is_active:
            return {"status": "skipped"}

        lead = cl.lead
        cadence_config = cl.campaign.cadence_config
        max_retries = cadence_config.get("max_call_retries", settings.MAX_CALL_RETRIES)

        logger.info(f"Processing lead={lead.name} state={cl.current_state} attempt={cl.attempt_count}")

        # ── COMPLIANCE CHECK 1: DNC ──────────────────────────────────────────
        if compliance.is_dnc(lead):
            _update_state(db, cl, "dnc_blocked")
            _write_audit_log(db, cl, "call", "skipped_dnc",
                             {"reason": "Lead is on Do Not Call list"})
            return {"status": "blocked_dnc"}

        # ── COMPLIANCE CHECK 2: Call Hours ───────────────────────────────────
        if not compliance.is_within_call_hours():
            next_window = compliance.next_call_window_start()
            _write_audit_log(db, cl, "call", "skipped_hours",
                             {"reason": "Outside call hours", "rescheduled_for": str(next_window)})
            # Re-queue for next valid window using eta
            process_cadence_step.apply_async(
                args=[campaign_lead_id],
                eta=next_window
            )
            logger.info(f"Lead {lead.name} rescheduled to {next_window} (outside hours)")
            return {"status": "rescheduled", "eta": str(next_window)}

        # ── DECIDE NEXT ACTION ───────────────────────────────────────────────
        if cl.current_state in ("pending", "call_scheduled", "call_no_answer"):
            if cl.attempt_count < max_retries:
                # Schedule a call attempt
                _update_state(db, cl, "call_scheduled")
                simulate_call.apply_async(args=[campaign_lead_id])
            else:
                # Max call attempts reached → move to email
                logger.info(f"Max retries reached for {lead.name}, sending email")
                _update_state(db, cl, "email_scheduled")
                simulate_email.apply_async(
                    args=[campaign_lead_id, "max_retries_followup"]
                )

        elif cl.current_state == "call_answered":
            # Lead answered → send follow-up email
            _update_state(db, cl, "email_scheduled")
            simulate_email.apply_async(
                args=[campaign_lead_id, "post_call_followup"]
            )

    finally:
        db.close()


@celery.task(
    bind=True,
    max_retries=2,           # Celery will retry this task on exception
    default_retry_delay=10,
    queue="calls",
    name="simulate_call"
)
def simulate_call(self, campaign_lead_id: str):
    """
    Simulates a phone call.
    In Stage 2, replace this with: Twilio/Bland.ai API call.
    Randomly returns: answered | no_answer | failed
    """
    db = get_db_session()
    try:
        cl = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        if not cl:
            return

        lead = cl.lead
        cl.attempt_count += 1
        _update_state(db, cl, "call_in_progress")

        logger.info(f"📞 Calling {lead.name} at {lead.phone} (attempt #{cl.attempt_count})")

        # Simulate call duration
        time.sleep(2)

        # Simulate random outcome
        # In real system: use webhook from call provider to update this
        outcome = random.choices(
            ["answered", "no_answer", "failed"],
            weights=[30, 60, 10],    # 30% answer, 60% no answer, 10% fail
            k=1
        )[0]

        logger.info(f"Call outcome for {lead.name}: {outcome}")

        if outcome == "answered":
            _update_state(db, cl, "call_answered")
            _write_audit_log(db, cl, "call", "success",
                             {"attempt": cl.attempt_count, "duration_seconds": 2})
            # Trigger follow-up email via cadence orchestrator
            process_cadence_step.apply_async(args=[campaign_lead_id], countdown=5)

        elif outcome == "no_answer":
            _update_state(db, cl, "call_no_answer")
            _write_audit_log(db, cl, "call", "no_answer",
                             {"attempt": cl.attempt_count})
            # Schedule retry via cadence orchestrator
            retry_delay = cl.campaign.cadence_config.get(
                "retry_delay_seconds", settings.RETRY_DELAY_SECONDS
            )
            process_cadence_step.apply_async(
                args=[campaign_lead_id],
                countdown=retry_delay   # ← THIS is how Celery delays work
            )
            logger.info(f"Will retry {lead.name} in {retry_delay}s")

        else:  # failed
            _write_audit_log(db, cl, "call", "failed",
                             {"attempt": cl.attempt_count, "error": "call_failed"})
            # Treat as no_answer for retry purposes
            _update_state(db, cl, "call_no_answer")
            process_cadence_step.apply_async(
                args=[campaign_lead_id],
                countdown=settings.RETRY_DELAY_SECONDS
            )

    except Exception as exc:
        logger.error(f"simulate_call failed: {exc}")
        raise self.retry(exc=exc)    # Celery auto-retry with backoff
    finally:
        db.close()


@celery.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="emails",
    name="simulate_email"
)
def simulate_email(self, campaign_lead_id: str, email_type: str = "followup"):
    """
    Simulates sending an email.
    In Stage 2, replace with: SendGrid / Resend API call.
    """
    db = get_db_session()
    try:
        cl = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        if not cl:
            return

        lead = cl.lead
        logger.info(f"📧 Sending {email_type} email to {lead.email} ({lead.name})")

        # Simulate email sending
        time.sleep(1)

        # Simulate random outcome
        success = random.random() > 0.05  # 95% success rate

        if success:
            _update_state(db, cl, "completed")
            cl.is_active = False    # Stop cadence for this lead
            _write_audit_log(db, cl, "email", "success",
                             {"email_type": email_type, "to": lead.email})
            logger.info(f"✅ Cadence complete for {lead.name}")
        else:
            _write_audit_log(db, cl, "email", "failed",
                             {"email_type": email_type, "error": "smtp_error"})
            raise self.retry(exc=Exception("Email send failed"))

    except Exception as exc:
        if not exc.__class__.__name__ == "Retry":
            logger.error(f"simulate_email failed: {exc}")
        raise
    finally:
        db.close()