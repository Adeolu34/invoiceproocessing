"""Celery tasks for invoice processing pipeline."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy import select

from app.config import get_settings
from app.models import Invoice, InvoiceStatus, LogStatus, ProcessingLog
from app.tasks.celery_app import celery_app
from app.utils.logger import get_logger

log = get_logger(__name__)
settings = get_settings()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_sync_session():
    """Return a synchronous SQLAlchemy session for use inside Celery tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Convert async URL to sync (asyncpg → psycopg2)
    sync_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )
    engine = create_engine(sync_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()


def _add_log(
    session,
    invoice_id: int,
    stage: str,
    status: LogStatus,
    message: str | None = None,
) -> None:
    """Append a ProcessingLog row and flush immediately."""
    entry = ProcessingLog(
        invoice_id=invoice_id,
        stage=stage,
        status=status,
        message=message,
    )
    session.add(entry)
    session.flush()


def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
        return loop.run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)


# ── Tasks ─────────────────────────────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="app.tasks.invoice_tasks.process_invoice_task",
    max_retries=3,
    default_retry_delay=60,
)
def process_invoice_task(self: Task, invoice_id: int) -> dict[str, Any]:
    """Orchestrate the full invoice pipeline for a single invoice.

    Stages:
      1. OCR / text extraction
      2. Field parsing
      3. Duplicate detection
      4. Browser data entry into accounting portal
      5. Success notification
    """
    session = _get_sync_session()
    invoice: Invoice | None = session.get(Invoice, invoice_id)

    if invoice is None:
        log.error("Invoice {id} not found", id=invoice_id)
        return {"error": f"Invoice {invoice_id} not found"}

    log.info("Starting pipeline for invoice {id} ({file})", id=invoice_id, file=invoice.filename)

    try:
        # ── Update status to processing ───────────────────────────────────────
        invoice.status = InvoiceStatus.processing
        session.commit()

        # ── Stage 1: OCR ──────────────────────────────────────────────────────
        _add_log(session, invoice_id, "ocr", LogStatus.started, "Beginning OCR extraction")
        session.commit()

        from app.services.ocr_service import OCRService

        ocr = OCRService()
        file_path = Path(settings.invoice_watch_folder) / invoice.filename

        try:
            ocr_result = ocr.extract_from_pdf(str(file_path))
            invoice.raw_text = ocr_result.get("raw_text", "")
            session.commit()
            _add_log(session, invoice_id, "ocr", LogStatus.success, "OCR extraction complete")
            session.commit()
            log.info("OCR complete for invoice {id}", id=invoice_id)
        except Exception as exc:
            _add_log(session, invoice_id, "ocr", LogStatus.failed, str(exc))
            session.commit()
            raise

        # ── Stage 2: Field parsing ────────────────────────────────────────────
        _add_log(session, invoice_id, "parse", LogStatus.started)
        session.commit()

        try:
            parsed = ocr.parse_invoice_fields(invoice.raw_text or "")
            invoice.vendor_name = parsed.get("vendor_name")
            invoice.invoice_number = parsed.get("invoice_number")
            invoice.amount = parsed.get("amount")
            invoice.currency = parsed.get("currency", "USD")
            invoice.invoice_date = parsed.get("invoice_date")
            invoice.due_date = parsed.get("due_date")
            invoice.extracted_data = parsed
            session.commit()
            _add_log(
                session,
                invoice_id,
                "parse",
                LogStatus.success,
                f"Extracted {len([v for v in parsed.values() if v])} fields",
            )
            session.commit()
        except Exception as exc:
            _add_log(session, invoice_id, "parse", LogStatus.failed, str(exc))
            session.commit()
            raise

        # ── Stage 3: Duplicate detection ──────────────────────────────────────
        _add_log(session, invoice_id, "duplicate_check", LogStatus.started)
        session.commit()

        try:
            is_dup = ocr.detect_duplicate(
                {"invoice_number": invoice.invoice_number},
                session,
                exclude_id=invoice_id,
            )
            if is_dup:
                invoice.status = InvoiceStatus.duplicate
                session.commit()
                _add_log(
                    session,
                    invoice_id,
                    "duplicate_check",
                    LogStatus.skipped,
                    f"Duplicate of invoice number {invoice.invoice_number}",
                )
                session.commit()
                send_notification_task.apply_async(
                    args=[
                        invoice_id,
                        f"Duplicate invoice detected: {invoice.invoice_number} (id={invoice_id})",
                    ],
                    queue="low",
                )
                return {"status": "duplicate", "invoice_id": invoice_id}

            _add_log(session, invoice_id, "duplicate_check", LogStatus.success, "No duplicate found")
            session.commit()
        except Exception as exc:
            _add_log(session, invoice_id, "duplicate_check", LogStatus.failed, str(exc))
            session.commit()
            raise

        # ── Stage 4: Browser / portal entry ───────────────────────────────────
        _add_log(session, invoice_id, "portal_entry", LogStatus.started)
        session.commit()

        try:
            invoice_data = {
                "vendor_name": invoice.vendor_name,
                "invoice_number": invoice.invoice_number,
                "amount": invoice.amount,
                "currency": invoice.currency,
                "invoice_date": invoice.invoice_date,
                "due_date": invoice.due_date,
            }
            confirmation = _run_async(_enter_invoice_in_portal(invoice_id, invoice_data))
            _add_log(
                session,
                invoice_id,
                "portal_entry",
                LogStatus.success,
                f"Confirmation: {confirmation}",
            )
            session.commit()
        except Exception as exc:
            _add_log(session, invoice_id, "portal_entry", LogStatus.failed, str(exc))
            session.commit()
            raise

        # ── Stage 5: Mark complete, notify ────────────────────────────────────
        invoice.status = InvoiceStatus.completed
        invoice.error_message = None
        session.commit()

        send_notification_task.apply_async(
            args=[invoice_id, f"Invoice {invoice.invoice_number} processed successfully"],
            queue="low",
        )

        log.info("Invoice {id} completed successfully", id=invoice_id)
        return {"status": "completed", "invoice_id": invoice_id}

    except Exception as exc:
        log.exception("Pipeline failed for invoice {id}: {exc}", id=invoice_id, exc=exc)
        invoice.status = InvoiceStatus.failed
        invoice.error_message = str(exc)
        invoice.retry_count = (invoice.retry_count or 0) + 1
        session.commit()

        send_notification_task.apply_async(
            args=[invoice_id, f"Invoice processing FAILED: {exc}"],
            queue="low",
        )
        raise self.retry(exc=exc, countdown=60 * invoice.retry_count)

    finally:
        session.close()


async def _enter_invoice_in_portal(invoice_id: int, invoice_data: dict) -> str:
    """Async helper that drives Playwright to submit invoice data."""
    from app.automation.invoice_processor import InvoiceProcessor

    processor = InvoiceProcessor(settings)
    return await processor.process(invoice_id, invoice_data)


@celery_app.task(
    name="app.tasks.invoice_tasks.scan_inbox_task",
    max_retries=2,
    default_retry_delay=30,
)
def scan_inbox_task() -> dict[str, Any]:
    """Scan IMAP inbox AND watch folder for new invoice PDFs.

    Creates Invoice records and dispatches process_invoice_task for each new file.
    """
    session = _get_sync_session()
    dispatched: list[int] = []

    try:
        # ── 1. Scan email inbox ────────────────────────────────────────────────
        if settings.imap_user and settings.imap_pass:
            try:
                from app.services.email_service import EmailService

                email_svc = EmailService(settings)
                emails = email_svc.fetch_invoice_emails()
                log.info("Found {n} invoice emails", n=len(emails))

                for email_meta in emails:
                    for attachment_path in email_meta.get("attachment_paths", []):
                        filename = Path(attachment_path).name
                        invoice = Invoice(
                            filename=filename,
                            status=InvoiceStatus.pending,
                        )
                        session.add(invoice)
                        session.flush()
                        dispatched.append(invoice.id)
                        process_invoice_task.apply_async(
                            args=[invoice.id], queue="high"
                        )
                        log.info(
                            "Queued invoice {id} from email: {file}",
                            id=invoice.id,
                            file=filename,
                        )

                session.commit()
            except Exception as exc:
                log.warning("Email scan failed, continuing with folder scan: {exc}", exc=exc)

        # ── 2. Scan watch folder ───────────────────────────────────────────────
        watch_folder = Path(settings.invoice_watch_folder)
        if watch_folder.exists():
            # Collect filenames already in DB to avoid double-processing
            existing: set[str] = set()
            rows = session.execute(select(Invoice.filename)).scalars().all()
            existing.update(rows)

            for pdf_file in watch_folder.glob("*.pdf"):
                if pdf_file.name in existing:
                    continue

                invoice = Invoice(
                    filename=pdf_file.name,
                    status=InvoiceStatus.pending,
                )
                session.add(invoice)
                session.flush()
                session.commit()

                dispatched.append(invoice.id)
                process_invoice_task.apply_async(args=[invoice.id], queue="high")
                log.info(
                    "Queued invoice {id} from folder: {file}",
                    id=invoice.id,
                    file=pdf_file.name,
                )

        log.info("scan_inbox_task dispatched {n} invoices", n=len(dispatched))
        return {"dispatched": dispatched}

    except Exception as exc:
        session.rollback()
        log.exception("scan_inbox_task failed: {exc}", exc=exc)
        raise

    finally:
        session.close()


@celery_app.task(
    name="app.tasks.invoice_tasks.retry_failed_invoices_task",
)
def retry_failed_invoices_task() -> dict[str, Any]:
    """Re-dispatch invoices that failed and have not exceeded the retry limit."""
    session = _get_sync_session()

    try:
        failed_invoices = (
            session.execute(
                select(Invoice).where(
                    Invoice.status == InvoiceStatus.failed,
                    Invoice.retry_count < 3,
                )
            )
            .scalars()
            .all()
        )

        retried: list[int] = []
        for invoice in failed_invoices:
            invoice.status = InvoiceStatus.pending
            session.flush()
            process_invoice_task.apply_async(args=[invoice.id], queue="high")
            retried.append(invoice.id)
            log.info(
                "Re-dispatching failed invoice {id} (retry {n})",
                id=invoice.id,
                n=invoice.retry_count,
            )

        session.commit()
        log.info("retry_failed_invoices_task retried {n} invoices", n=len(retried))
        return {"retried": retried}

    except Exception as exc:
        session.rollback()
        log.exception("retry_failed_invoices_task failed: {exc}", exc=exc)
        raise

    finally:
        session.close()


@celery_app.task(
    name="app.tasks.invoice_tasks.send_notification_task",
    max_retries=3,
    default_retry_delay=30,
)
def send_notification_task(invoice_id: int, message: str) -> dict[str, Any]:
    """Post a Slack notification for an invoice event."""
    session = _get_sync_session()
    try:
        invoice: Invoice | None = session.get(Invoice, invoice_id)
        if invoice is None:
            log.warning("send_notification_task: invoice {id} not found", id=invoice_id)
            return {"sent": False}

        from app.services.notification_service import NotificationService

        svc = NotificationService(settings)

        if invoice.status == InvoiceStatus.completed:
            svc.send_slack_invoice_processed(invoice)
        elif invoice.status == InvoiceStatus.failed:
            svc.send_slack_invoice_failed(invoice, invoice.error_message or "Unknown error")
        else:
            svc.send_slack(message=message, color="#ffcc00", fields=[])

        log.info("Notification sent for invoice {id}", id=invoice_id)
        return {"sent": True}

    except Exception as exc:
        log.exception("send_notification_task failed: {exc}", exc=exc)
        raise

    finally:
        session.close()
