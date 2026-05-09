"""Celery application factory with Redis broker, beat schedule, and task routing."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "invoice_processing",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.invoice_tasks",
    ],
)

# ── Serialisation ─────────────────────────────────────────────────────────────
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "UTC"
celery_app.conf.enable_utc = True

# ── Result handling ───────────────────────────────────────────────────────────
celery_app.conf.result_expires = 60 * 60 * 24  # 24 hours
celery_app.conf.task_track_started = True
celery_app.conf.task_acks_late = True          # Only ack after task completes
celery_app.conf.worker_prefetch_multiplier = 1  # Fair scheduling

# ── Task routing / priority queues ───────────────────────────────────────────
celery_app.conf.task_queues = {
    "high": {"exchange": "high", "routing_key": "high"},
    "default": {"exchange": "default", "routing_key": "default"},
    "low": {"exchange": "low", "routing_key": "low"},
}
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_default_exchange = "default"
celery_app.conf.task_default_routing_key = "default"

celery_app.conf.task_routes = {
    # Manual triggers and retries get high priority
    "app.tasks.invoice_tasks.process_invoice_task": {"queue": "high"},
    "app.tasks.invoice_tasks.retry_failed_invoices_task": {"queue": "high"},
    # Scheduled inbox scans are normal priority
    "app.tasks.invoice_tasks.scan_inbox_task": {"queue": "default"},
    # Notifications are lower priority
    "app.tasks.invoice_tasks.send_notification_task": {"queue": "low"},
}

# ── Beat schedule ─────────────────────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    "scan-inbox-every-5-minutes": {
        "task": "app.tasks.invoice_tasks.scan_inbox_task",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "default"},
    },
    "retry-failed-invoices-every-15-minutes": {
        "task": "app.tasks.invoice_tasks.retry_failed_invoices_task",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "high"},
    },
}

# ── Worker settings ───────────────────────────────────────────────────────────
celery_app.conf.worker_max_tasks_per_child = 50   # Prevent memory leaks
celery_app.conf.task_soft_time_limit = 300         # 5 min soft limit
celery_app.conf.task_time_limit = 600              # 10 min hard limit
