"""
celery_app.py
Stage 2: Adds task lifecycle signals to auto-update TaskRecord table.
This is what makes GET /api/v1/tasks/{task_id} work in real-time.
"""
from celery import Celery
from celery.signals import task_prerun, task_postrun, task_failure, task_retry
from kombu import Queue, Exchange

app = Celery("sales_cadence")

app.conf.update(

    broker_url      = "redis://redis:6379/0",
    result_backend  = "redis://redis:6379/1",



    include = ["app.workers.tasks"],
    
    # ── Reliability ───────────────────────────────────────
    task_acks_late                = True,   # ack only after completion
    task_reject_on_worker_lost    = True,   # re-queue on worker crash
    worker_prefetch_multiplier    = 1,      # one task at a time per worker slot

    # ── Serialization ────────────────────────────────────
    task_serializer        = "json",
    result_serializer      = "json",
    accept_content         = ["json"],
    task_track_started     = True,
    result_expires         = 86400,         # keep results 24h in Redis
    timezone               = "UTC",

    # ── Retry defaults ───────────────────────────────────
    task_max_retries       = 3,

    # ── Named queues (scale independently) ───────────────
    task_queues = (
        Queue("validation", Exchange("validation"), routing_key="validation"),
        Queue("calls",      Exchange("calls"),      routing_key="calls"),
        Queue("emails",     Exchange("emails"),     routing_key="emails"),
        Queue("cadence",    Exchange("cadence"),    routing_key="cadence"),
        Queue("reporting",  Exchange("reporting"),  routing_key="reporting"),
    ),

    task_routes = {
        # Validation group (parallel)
        "app.workers.tasks.validate_lead":       {"queue": "validation"},
        "app.workers.tasks.check_dnc":           {"queue": "validation"},
        "app.workers.tasks.verify_email_addr":   {"queue": "validation"},

        # Call chain
        "app.workers.tasks.simulate_call":       {"queue": "calls"},

        # Email queue
        "app.workers.tasks.send_real_email":     {"queue": "emails"},

        # Orchestration
        "app.workers.tasks.aggregate_validation":{"queue": "cadence"},
        "app.workers.tasks.launch_campaign":     {"queue": "cadence"},
        "app.workers.tasks.run_lead_workflow":   {"queue": "cadence"},
        "app.workers.tasks.finalize_lead":       {"queue": "cadence"},

        # Reporting chord
        "app.workers.tasks.report_calls":        {"queue": "reporting"},
        "app.workers.tasks.report_emails":       {"queue": "reporting"},
        "app.workers.tasks.report_conversion":   {"queue": "reporting"},
        "app.workers.tasks.combine_reports":     {"queue": "reporting"},
    },
)


# ── Celery Signals → auto-update TaskRecord ───────────────────────────────────
# These fire on every task lifecycle event. We use them to keep our DB in sync
# with the actual Celery task state stored in Redis result backend.

def _get_db():
    from app.database import SessionLocal
    return SessionLocal()


@task_prerun.connect
def on_task_start(task_id, task, *args, **kwargs):
    """Fires when a worker picks up a task."""
    try:
        db = _get_db()
        from app.models import TaskRecord
        rec = db.query(TaskRecord).filter_by(task_id=task_id).first()
        if rec:
            rec.status     = "STARTED"
            rec.updated_at = __import__("datetime").datetime.utcnow()
            db.commit()
        db.close()
    except Exception:
        pass   # never let signal crash the worker


@task_postrun.connect
def on_task_done(task_id, task, retval, state, *args, **kwargs):
    """Fires when a task finishes (success or failure)."""
    try:
        db = _get_db()
        from app.models import TaskRecord
        import datetime
        rec = db.query(TaskRecord).filter_by(task_id=task_id).first()
        if rec:
            rec.status     = state   # SUCCESS or FAILURE
            rec.updated_at = datetime.datetime.utcnow()
            if state == "SUCCESS" and retval is not None:
                rec.result_payload = retval if isinstance(retval, dict) else {"result": str(retval)}
            db.commit()
        db.close()
    except Exception:
        pass


@task_failure.connect
def on_task_fail(task_id, exception, *args, **kwargs):
    """Fires on unhandled task exception."""
    try:
        db = _get_db()
        from app.models import TaskRecord
        import datetime
        rec = db.query(TaskRecord).filter_by(task_id=task_id).first()
        if rec:
            rec.status        = "FAILURE"
            rec.error_message = str(exception)
            rec.updated_at    = datetime.datetime.utcnow()
            db.commit()
        db.close()
    except Exception:
        pass


@task_retry.connect
def on_task_retry(request, reason, *args, **kwargs):
    """Fires when a task is retried."""
    try:
        db = _get_db()
        from app.models import TaskRecord
        import datetime
        rec = db.query(TaskRecord).filter_by(task_id=request.id).first()
        if rec:
            rec.status      = "RETRY"
            rec.retry_count = (rec.retry_count or 0) + 1
            rec.updated_at  = datetime.datetime.utcnow()
            db.commit()
        db.close()
    except Exception:
        pass

























