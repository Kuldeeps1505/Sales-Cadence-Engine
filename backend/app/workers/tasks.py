"""
tasks.py — Stage 2: Full Celery Workflow Orchestration
═══════════════════════════════════════════════════════

WORKFLOW ARCHITECTURE:
─────────────────────
When "Run Outreach Workflow" is clicked for a campaign:

  launch_campaign(campaign_id)
      └── FOR EACH lead:
              run_lead_workflow(campaign_lead_id)
                  │
                  ├─ STEP 1: chord (parallel validation → aggregate)
                  │     group(
                  │       validate_lead.s(),        ← checks phone/email format
                  │       check_dnc.s(),            ← checks DNC list
                  │       verify_email_addr.s()     ← checks email validity
                  │     ) → aggregate_validation.s()
                  │
                  ├─ STEP 2: chain (sequential call pipeline)
                  │     simulate_call.s()           ← attempt 1
                  │     → [no answer] countdown → simulate_call.s() again (up to max_retries)
                  │     → [answered]  → send_real_email.s("post_call_followup")
                  │     → [exhausted] → send_real_email.s("retry_exhausted")
                  │
                  └─ STEP 3: finalize_lead.s()      ← update state + write audit

REPORTING CHORD (triggered per campaign):
  chord(
    group(
      report_calls.s(),
      report_emails.s(),
      report_conversion.s()
    ),
    combine_reports.s()
  )

CELERY PRIMITIVES USED:
  ✔ chain   — sequential steps pass result to next
  ✔ group   — parallel tasks (all fire simultaneously)
  ✔ chord   — parallel tasks → single callback when all done
  ✔ retry   — exponential backoff on failures
  ✔ signals — auto-update TaskRecord (result backend)
"""

import random
import time
import datetime
from celery import chain, group, chord
from celery.utils.log import get_task_logger
from app.workers.celery_app import app
from app.database import get_db_session
from app.models import CampaignLead, AuditLog, Campaign, TaskRecord, Lead

logger = get_task_logger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _audit(db, cl, attempt_type: str, status: str, meta: dict = {}):
    db.add(AuditLog(
        campaign_lead_id=cl.id,
        lead_id=cl.lead_id,
        campaign_id=cl.campaign_id,
        attempt_type=attempt_type,
        status=status,
        metadata_=meta,
    ))
    db.commit()
    logger.info(f"[AUDIT] {cl.lead.name} | {attempt_type} | {status}")


def _set_state(db, cl, state: str):
    cl.current_state = state
    cl.updated_at    = datetime.datetime.utcnow()
    db.commit()


def _register_task(db, task_id: str, task_name: str, campaign_id=None, lead_id=None):
    """Insert a TaskRecord so it's visible via GET /api/v1/tasks/{task_id}."""
    existing = db.query(TaskRecord).filter_by(task_id=task_id).first()
    if not existing:
        db.add(TaskRecord(
            task_id=task_id,
            task_name=task_name,
            campaign_id=campaign_id,
            lead_id=lead_id,
            status="PENDING",
        ))
        db.commit()


# ─── VALIDATION GROUP (parallel) ──────────────────────────────────────────────

@app.task(bind=True, max_retries=2, default_retry_delay=5,
          queue="validation", name="validate_lead")
def validate_lead(self, campaign_lead_id: str) -> dict:
    """
    Validates lead data format.
    Part of the parallel validation group.
    """
    db = get_db_session()
    try:
        cl   = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        lead = cl.lead
        logger.info(f"[VALIDATE] Checking lead data for {lead.name}")
        time.sleep(0.5)

        issues = []
        if not lead.phone or len(lead.phone) < 10:
            issues.append("invalid_phone")
        if not lead.email or "@" not in lead.email:
            issues.append("invalid_email")

        result = {
            "check": "validate_lead",
            "lead_id": lead.id,
            "passed": len(issues) == 0,
            "issues": issues,
        }
        logger.info(f"[VALIDATE] {lead.name} → {'PASS' if result['passed'] else 'FAIL'}")
        return result
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, max_retries=2, default_retry_delay=5,
          queue="validation", name="check_dnc")
def check_dnc(self, campaign_lead_id: str) -> dict:
    """
    Checks DNC list in parallel with other validations.
    """
    db = get_db_session()
    try:
        cl   = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        lead = cl.lead
        logger.info(f"[DNC CHECK] Checking DNC for {lead.name}")
        time.sleep(0.3)

        result = {
            "check":    "check_dnc",
            "lead_id":  lead.id,
            "passed":   not lead.is_dnc,
            "is_dnc":   lead.is_dnc,
        }
        logger.info(f"[DNC CHECK] {lead.name} → {'BLOCKED' if lead.is_dnc else 'CLEAR'}")
        return result
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, max_retries=2, default_retry_delay=5,
          queue="validation", name="verify_email_addr")
def verify_email_addr(self, campaign_lead_id: str) -> dict:
    """
    Simulates email address verification (MX lookup etc.).
    Runs in parallel with other validation tasks.
    """
    db = get_db_session()
    try:
        cl   = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        lead = cl.lead
        logger.info(f"[EMAIL VERIFY] Verifying {lead.email}")
        time.sleep(0.4)

        # Simulate: 95% emails are valid
        valid = random.random() > 0.05
        result = {
            "check":   "verify_email",
            "lead_id": lead.id,
            "passed":  valid,
            "email":   lead.email,
        }
        logger.info(f"[EMAIL VERIFY] {lead.email} → {'VALID' if valid else 'INVALID'}")
        return result
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        db.close()


@app.task(bind=True, max_retries=1, queue="cadence", name="aggregate_validation")
def aggregate_validation(self, validation_results: list, campaign_lead_id: str) -> dict:
    """
    CHORD CALLBACK — fires after ALL 3 parallel validation tasks complete.
    Receives list of results from group(validate_lead, check_dnc, verify_email).
    Decides: proceed to call pipeline OR block lead.

    This is the 'chord' pattern:
      chord(group(t1, t2, t3), aggregate_validation.s())
    """
    db = get_db_session()
    try:
        cl   = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        lead = cl.lead

        logger.info(f"[AGGREGATE] All 3 validations done for {lead.name}. Results: {validation_results}")

        # Check if ALL validations passed
        all_passed = all(r.get("passed", False) for r in validation_results if r)
        failed     = [r["check"] for r in validation_results if r and not r.get("passed", True)]

        if not all_passed:
            # Lead blocked — log and stop
            _set_state(db, cl, "validation_failed")
            cl.is_active = False
            _audit(db, cl, "validation", "blocked", {
                "failed_checks": failed,
                "all_results": validation_results
            })
            logger.info(f"[AGGREGATE] {lead.name} BLOCKED — failed: {failed}")
            return {"proceed": False, "reason": failed, "lead_id": lead.id}

        # All passed → proceed to call pipeline
        _set_state(db, cl, "validated")
        _audit(db, cl, "validation", "passed", {"checks": ["validate_lead", "check_dnc", "verify_email"]})
        logger.info(f"[AGGREGATE] {lead.name} VALIDATED ✓ — proceeding to call pipeline")
        return {"proceed": True, "lead_id": lead.id, "campaign_lead_id": campaign_lead_id}

    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        db.close()


# ─── CALL PIPELINE (chain with retry loop) ────────────────────────────────────

@app.task(bind=True, max_retries=3, default_retry_delay=10,
          queue="calls", name="simulate_call")
def simulate_call(self, prev_result: dict, campaign_lead_id: str = None) -> dict:
    """
    CHAIN STEP — receives result from previous task via `prev_result`.
    This is how chain() works: each task's return value is passed
    as the first argument to the next task.

    Retry strategy: exponential backoff
      attempt 1 → wait 15s → attempt 2 → wait 30s → attempt 3
    """
    # Handle being called directly (not via chain)
    if campaign_lead_id is None and isinstance(prev_result, dict):
        campaign_lead_id = prev_result.get("campaign_lead_id")

    # Skip if validation said don't proceed
    if isinstance(prev_result, dict) and not prev_result.get("proceed", True):
        logger.info(f"[CALL] Skipping — validation failed for {campaign_lead_id}")
        return {**prev_result, "campaign_lead_id": campaign_lead_id}

    db = get_db_session()
    try:
        cl   = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        if not cl or not cl.is_active:
            return {"proceed": False, "reason": "inactive", "campaign_lead_id": campaign_lead_id}

        lead          = cl.lead
        config        = cl.campaign.cadence_config
        max_retries   = config.get("max_call_retries", 3)
        retry_delay   = config.get("retry_delay_seconds", 15)

        cl.attempt_count += 1
        _set_state(db, cl, "call_in_progress")
        logger.info(f"[CALL] 📞 Calling {lead.name} ({lead.phone}) — attempt #{cl.attempt_count}/{max_retries}")

        time.sleep(2)  # simulate call duration

        # Simulate outcome with exponential backoff on retries
        # More attempts = slightly higher answer probability (realistic)
        answer_prob = 0.30 + (cl.attempt_count * 0.05)
        outcome = random.choices(
            ["answered", "no_answer", "failed"],
            weights=[answer_prob * 100, (1 - answer_prob) * 90, 10],
            k=1
        )[0]

        logger.info(f"[CALL] {lead.name} → {outcome} (attempt #{cl.attempt_count})")

        if outcome == "answered":
            _set_state(db, cl, "call_answered")
            _audit(db, cl, "call", "success", {"attempt": cl.attempt_count})
            return {
                "proceed": True,
                "outcome": "answered",
                "campaign_lead_id": campaign_lead_id,
                "attempt": cl.attempt_count,
                "email_type": "post_call_followup",
            }

        elif cl.attempt_count >= max_retries:
            # Max retries reached — move to email fallback
            _set_state(db, cl, "call_no_answer")
            _audit(db, cl, "call", "no_answer", {"attempt": cl.attempt_count, "final": True})
            logger.info(f"[CALL] Max retries reached for {lead.name} → email fallback")
            return {
                "proceed": True,
                "outcome": "max_retries_exhausted",
                "campaign_lead_id": campaign_lead_id,
                "attempt": cl.attempt_count,
                "email_type": "retry_exhausted",
            }

        else:
            # No answer but retries remain
            _set_state(db, cl, "call_no_answer")
            _audit(db, cl, "call", "no_answer", {"attempt": cl.attempt_count})

            # Exponential backoff: delay * 2^(attempt-1)
            backoff = retry_delay * (2 ** (cl.attempt_count - 1))
            logger.info(f"[CALL] Retry #{cl.attempt_count + 1} in {backoff}s")

            # Re-queue this task with countdown (exponential backoff)
            simulate_call.apply_async(
                args=[{"proceed": True, "campaign_lead_id": campaign_lead_id}],
                kwargs={"campaign_lead_id": campaign_lead_id},
                countdown=backoff,
            )
            return {
                "proceed": False,
                "outcome": "retry_scheduled",
                "campaign_lead_id": campaign_lead_id,
                "next_attempt_in": backoff,
            }

    except Exception as exc:
        logger.error(f"[CALL] Exception for {campaign_lead_id}: {exc}")
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


# ─── EMAIL PIPELINE ───────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=3, default_retry_delay=30,
          queue="emails", name="send_real_email")
def send_real_email(self, prev_result: dict, campaign_lead_id: str = None) -> dict:
    """
    CHAIN STEP — receives call outcome from simulate_call.
    Sends the appropriate email based on outcome type.
    Calls real SMTP (falls back to simulation if no credentials).
    """
    if campaign_lead_id is None and isinstance(prev_result, dict):
        campaign_lead_id = prev_result.get("campaign_lead_id")

    # Don't send email if retry was scheduled (call not done yet)
    if isinstance(prev_result, dict) and prev_result.get("outcome") == "retry_scheduled":
        return {**prev_result, "email_skipped": True}

    # Don't proceed if blocked
    if isinstance(prev_result, dict) and not prev_result.get("proceed", True):
        return {**prev_result, "email_skipped": True}

    db = get_db_session()
    try:
        cl        = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        if not cl:
            return {"proceed": False, "reason": "not_found"}

        lead      = cl.lead
        email_type = prev_result.get("email_type", "post_call_followup") if isinstance(prev_result, dict) else "outreach"
        attempt   = prev_result.get("attempt", cl.attempt_count) if isinstance(prev_result, dict) else cl.attempt_count

        _set_state(db, cl, "email_in_progress")
        logger.info(f"[EMAIL] 📧 Sending '{email_type}' to {lead.email} ({lead.name})")

        # Import here to avoid circular
        from app.services.email_service import (
            send_outreach_email, send_followup_email, send_retry_exhausted_email
        )

        if email_type == "post_call_followup":
            result = send_followup_email(
                lead_name=lead.name,
                lead_email=lead.email,
                call_outcome=f"Productive call on attempt #{attempt}"
            )
        elif email_type == "retry_exhausted":
            result = send_retry_exhausted_email(
                lead_name=lead.name,
                lead_email=lead.email,
                attempt_count=attempt
            )
        else:
            result = send_outreach_email(
                lead_name=lead.name,
                lead_email=lead.email,
                company=lead.company or "",
                notes=lead.notes or ""
            )

        if result.get("success"):
            _set_state(db, cl, "completed")
            cl.is_active = False
            db.commit()
            _audit(db, cl, "email", "success", {
                "email_type": email_type,
                "to": lead.email,
                "simulated": result.get("simulated", False),
            })
            logger.info(f"[EMAIL] ✅ Sent to {lead.name} ({email_type})")
            return {
                "proceed": True,
                "email_sent": True,
                "email_type": email_type,
                "to": lead.email,
                "campaign_lead_id": campaign_lead_id,
            }
        else:
            _audit(db, cl, "email", "failed", {"error": result.get("error")})
            raise self.retry(exc=Exception(result.get("error", "send failed")), countdown=60)

    except Exception as exc:
        if "retry" not in str(type(exc).__name__).lower():
            logger.error(f"[EMAIL] Exception: {exc}")
        raise
    finally:
        db.close()


# ─── FINALIZE (end of chain) ──────────────────────────────────────────────────

@app.task(bind=True, max_retries=1, queue="cadence", name="finalize_lead")
def finalize_lead(self, prev_result: dict, campaign_lead_id: str = None) -> dict:
    """
    Final step in the chain for each lead.
    Writes completion audit log and marks lead workflow done.
    """
    if campaign_lead_id is None and isinstance(prev_result, dict):
        campaign_lead_id = prev_result.get("campaign_lead_id")

    db = get_db_session()
    try:
        cl = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        if not cl:
            return {"finalized": True}

        lead = cl.lead
        logger.info(f"[FINALIZE] ✅ Workflow complete for {lead.name}")

        # Write final summary audit
        _audit(db, cl, "workflow", "completed", {
            "total_attempts": cl.attempt_count,
            "final_state": cl.current_state,
            "result": prev_result if isinstance(prev_result, dict) else {},
        })

        return {
            "finalized": True,
            "lead": lead.name,
            "final_state": cl.current_state,
            "attempts": cl.attempt_count,
        }
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        db.close()


# ─── REPORTING CHORD ──────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=1, queue="reporting", name="report_calls")
def report_calls(self, campaign_id: str) -> dict:
    """Parallel report: call stats for a campaign."""
    db = get_db_session()
    try:
        logs = db.query(AuditLog).filter_by(campaign_id=campaign_id, attempt_type="call").all()
        total      = len(logs)
        answered   = sum(1 for l in logs if l.status == "success")
        no_answer  = sum(1 for l in logs if l.status == "no_answer")
        failed     = sum(1 for l in logs if l.status == "failed")
        time.sleep(0.5)
        return {
            "report": "calls",
            "total": total, "answered": answered,
            "no_answer": no_answer, "failed": failed,
            "answer_rate": round(answered / total * 100, 1) if total else 0,
        }
    finally:
        db.close()


@app.task(bind=True, max_retries=1, queue="reporting", name="report_emails")
def report_emails(self, campaign_id: str) -> dict:
    """Parallel report: email stats for a campaign."""
    db = get_db_session()
    try:
        logs    = db.query(AuditLog).filter_by(campaign_id=campaign_id, attempt_type="email").all()
        total   = len(logs)
        sent    = sum(1 for l in logs if l.status == "success")
        failed  = sum(1 for l in logs if l.status == "failed")
        time.sleep(0.4)
        return {
            "report": "emails",
            "total": total, "sent": sent, "failed": failed,
            "delivery_rate": round(sent / total * 100, 1) if total else 0,
        }
    finally:
        db.close()


@app.task(bind=True, max_retries=1, queue="reporting", name="report_conversion")
def report_conversion(self, campaign_id: str) -> dict:
    """Parallel report: conversion stats for a campaign."""
    db = get_db_session()
    try:
        cls        = db.query(CampaignLead).filter_by(campaign_id=campaign_id).all()
        total      = len(cls)
        completed  = sum(1 for c in cls if c.current_state == "completed")
        blocked    = sum(1 for c in cls if c.current_state in ("dnc_blocked", "validation_failed"))
        time.sleep(0.3)
        return {
            "report": "conversion",
            "total_leads": total, "completed": completed, "blocked": blocked,
            "completion_rate": round(completed / total * 100, 1) if total else 0,
        }
    finally:
        db.close()


@app.task(bind=True, max_retries=1, queue="reporting", name="combine_reports")
def combine_reports(self, reports: list, campaign_id: str) -> dict:
    """
    CHORD CALLBACK — fires after ALL 3 parallel report tasks complete.
    Aggregates all reports into a single campaign summary.

    chord(
        group(report_calls.s(), report_emails.s(), report_conversion.s()),
        combine_reports.s(campaign_id=campaign_id)
    )
    """
    logger.info(f"[REPORT] Combining {len(reports)} reports for campaign {campaign_id}")
    combined = {"campaign_id": campaign_id, "generated_at": str(datetime.datetime.utcnow())}
    for r in reports:
        if isinstance(r, dict):
            combined[r.get("report", "unknown")] = r
    logger.info(f"[REPORT] ✅ Campaign report ready: {combined}")
    return combined


# ─── ORCHESTRATOR: run_lead_workflow ─────────────────────────────────────────

@app.task(bind=True, max_retries=0, queue="cadence", name="run_lead_workflow")
def run_lead_workflow(self, campaign_lead_id: str):
    """
    Full workflow for ONE lead using chain + chord.

    Architecture:
    ┌─────────────────────────────────────────────────────────┐
    │  chord(                                                  │
    │    group(validate_lead, check_dnc, verify_email),        │
    │    aggregate_validation                                  │
    │  ) → chain(simulate_call, send_real_email, finalize)    │
    └─────────────────────────────────────────────────────────┘

    Note: We build the chord first, then chain the call+email pipeline
    after aggregate_validation returns its result.
    """
    db = get_db_session()
    try:
        cl = db.query(CampaignLead).filter_by(id=campaign_lead_id).first()
        if not cl:
            return

        lead = cl.lead
        logger.info(f"[WORKFLOW] Starting full workflow for {lead.name}")
        _set_state(db, cl, "workflow_started")

        # ── STEP 1: Parallel validation chord ────────────────────────────────
        # group() runs all 3 simultaneously; aggregate_validation fires when all done
        validation_chord = chord(
            group(
                validate_lead.s(campaign_lead_id),
                check_dnc.s(campaign_lead_id),
                verify_email_addr.s(campaign_lead_id),
            ),
            aggregate_validation.s(campaign_lead_id)  # callback with full results list
        )

        # ── STEP 2: Sequential call → email → finalize chain ─────────────────
        # Each task receives the previous task's return value as first arg
        call_email_chain = chain(
            simulate_call.s(campaign_lead_id=campaign_lead_id),
            send_real_email.s(campaign_lead_id=campaign_lead_id),
            finalize_lead.s(campaign_lead_id=campaign_lead_id),
        )

        # ── LINK: chord result feeds into chain ───────────────────────────────
        # When validation chord completes, its result flows into simulate_call
        full_workflow = chain(validation_chord, call_email_chain)

        result = full_workflow.apply_async()

        # Store the root task_id so UI can track it
        cl.workflow_task_id = result.id
        db.commit()

        # Register in TaskRecord table
        _register_task(db, result.id, "run_lead_workflow",
                       campaign_id=cl.campaign_id, lead_id=cl.lead_id)

        logger.info(f"[WORKFLOW] {lead.name} workflow dispatched. task_id={result.id}")
        return {"task_id": result.id, "lead": lead.name}

    finally:
        db.close()


# ─── CAMPAIGN LAUNCHER ────────────────────────────────────────────────────────

@app.task(bind=True, max_retries=0, queue="cadence", name="launch_campaign")
def launch_campaign(self, campaign_id: str):
    """
    Entry point: fans out one run_lead_workflow per lead.
    Also schedules the reporting chord to fire after all leads complete.
    """
    db = get_db_session()
    try:
        campaign = db.query(Campaign).filter_by(id=campaign_id).first()
        if not campaign:
            return

        campaign.status     = "active"
        campaign.started_at = datetime.datetime.utcnow()
        db.commit()

        cls = campaign.campaign_leads
        logger.info(f"[LAUNCH] 🚀 Campaign '{campaign.name}' — {len(cls)} leads")

        # Fan out: stagger launches by 3s to avoid thundering herd on Redis
        for i, cl in enumerate(cls):
            run_lead_workflow.apply_async(args=[cl.id], countdown=i * 3)

        # Schedule reporting chord after estimated completion time
        # (In production: use a Celery Beat periodic task or callback chain)
        estimated_completion = len(cls) * 30  # rough estimate
        reporting_chord = chord(
            group(
                report_calls.s(campaign_id),
                report_emails.s(campaign_id),
                report_conversion.s(campaign_id),
            ),
            combine_reports.s(campaign_id)
        )
        reporting_chord.apply_async(countdown=estimated_completion)

        logger.info(f"[LAUNCH] All workflows dispatched. Report in ~{estimated_completion}s")
        return {"campaign": campaign.name, "leads": len(cls)}

    finally:
        db.close()










































