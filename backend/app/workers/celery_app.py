from celery import Celery
from kombu import Queue, Exchange
from app.config import settings

# Create the Celery app
celery = Celery("sales_cadence")

celery.conf.update(
    broker_url=settings.CELERY_BROKER_URL,
    result_backend=settings.CELERY_RESULT_BACKEND,

    # ---- RELIABILITY: most important settings to understand ----
    # Only mark task as done AFTER it completes (not when picked up)
    # Without this: worker crash = task lost forever
    task_acks_late=True,

    # If worker dies mid-task, put the task back in the queue
    task_reject_on_worker_lost=True,

    # Don't grab extra tasks ahead of time
    # Without this: worker grabs 4 tasks, crashes, all 4 are lost
    worker_prefetch_multiplier=1,
    # ------------------------------------------------------------

    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    timezone="UTC",

    # Define named queues — each worker targets specific queues
    task_queues=(
        Queue("calls",   Exchange("calls"),   routing_key="calls"),
        Queue("emails",  Exchange("emails"),  routing_key="emails"),
        Queue("cadence", Exchange("cadence"), routing_key="cadence"),
    ),

    # Route tasks to queues automatically by module path
    task_routes={
        "app.workers.tasks.simulate_call":    {"queue": "calls"},
        "app.workers.tasks.simulate_email":   {"queue": "emails"},
        "app.workers.tasks.process_cadence_step": {"queue": "cadence"},
        "app.workers.tasks.launch_campaign":  {"queue": "cadence"},
    },
)

# import our task definitions so that the decorators execute when the
# celery app is initialized. without this, the worker has no knowledge
# of the custom tasks and you will see NotRegistered errors for names
# such as "launch_campaign".
# we import the module for side effects only (no names used), hence
# the noqa comment to keep linters happy.
from app.workers import tasks  # noqa: F401