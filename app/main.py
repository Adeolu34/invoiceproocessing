"""FastAPI application for the Invoice Processing system."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, init_db
from app.models import Invoice, InvoiceStatus, LogStatus, ProcessingLog
from app.utils.logger import get_logger

log = get_logger(__name__)
settings = get_settings()

app = FastAPI(
    title="Invoice Processing API",
    description="RPA pipeline: ingest PDF invoices → OCR → parse → enter into accounting portal.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup ───────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def on_startup() -> None:
    """Initialise database tables and required filesystem directories."""
    await init_db()
    settings.screenshots_path.mkdir(parents=True, exist_ok=True)
    settings.watch_folder_path.mkdir(parents=True, exist_ok=True)
    Path("logs").mkdir(parents=True, exist_ok=True)
    log.info("Invoice Processing API started")


# ── Pydantic schemas ─────────────────────────────────────────────────────────


class InvoiceOut(BaseModel):
    id: int
    filename: str
    vendor_name: str | None
    invoice_number: str | None
    amount: float | None
    currency: str | None
    invoice_date: str | None
    due_date: str | None
    status: InvoiceStatus
    retry_count: int
    error_message: str | None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_instance(cls, inv: Invoice) -> "InvoiceOut":
        return cls(
            id=inv.id,
            filename=inv.filename,
            vendor_name=inv.vendor_name,
            invoice_number=inv.invoice_number,
            amount=inv.amount,
            currency=inv.currency,
            invoice_date=inv.invoice_date,
            due_date=inv.due_date,
            status=inv.status,
            retry_count=inv.retry_count,
            error_message=inv.error_message,
            created_at=inv.created_at.isoformat(),
            updated_at=inv.updated_at.isoformat(),
        )


class ProcessingLogOut(BaseModel):
    id: int
    invoice_id: int
    stage: str
    status: LogStatus
    message: str | None
    created_at: str

    model_config = {"from_attributes": True}


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    success_rate: float


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Return a simple liveness check."""
    return {"status": "ok", "service": "invoice-processing"}


@app.get("/invoices", response_model=list[InvoiceOut], tags=["invoices"])
async def list_invoices(
    db: AsyncSession = Depends(get_db),
    status_filter: InvoiceStatus | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> list[InvoiceOut]:
    """List invoices with optional status filter and pagination."""
    stmt = select(Invoice).order_by(Invoice.created_at.desc())
    if status_filter is not None:
        stmt = stmt.where(Invoice.status == status_filter)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    invoices = result.scalars().all()
    return [InvoiceOut.from_orm_instance(inv) for inv in invoices]


@app.get("/invoices/stats", response_model=StatsOut, tags=["invoices"])
async def invoice_stats(db: AsyncSession = Depends(get_db)) -> StatsOut:
    """Return aggregate counts by status and overall success rate."""
    # Total count
    total_result = await db.execute(select(func.count(Invoice.id)))
    total: int = total_result.scalar_one() or 0

    # Count per status
    status_result = await db.execute(
        select(Invoice.status, func.count(Invoice.id)).group_by(Invoice.status)
    )
    by_status: dict[str, int] = {row[0].value: row[1] for row in status_result.all()}

    completed = by_status.get("completed", 0)
    success_rate = round(completed / total * 100, 2) if total > 0 else 0.0

    return StatsOut(total=total, by_status=by_status, success_rate=success_rate)


@app.get("/invoices/{invoice_id}", response_model=InvoiceOut, tags=["invoices"])
async def get_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)) -> InvoiceOut:
    """Fetch a single invoice by ID."""
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    return InvoiceOut.from_orm_instance(invoice)


@app.post(
    "/invoices/process",
    response_model=InvoiceOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["invoices"],
)
async def process_invoice_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> InvoiceOut:
    """Accept a PDF upload, save it, create an Invoice record, and dispatch processing.

    Returns the Invoice record immediately; processing is async via Celery.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Save uploaded file
    save_dir = settings.watch_folder_path
    dest = save_dir / file.filename
    counter = 1
    while dest.exists():
        stem = Path(file.filename).stem
        dest = save_dir / f"{stem}_{counter}.pdf"
        counter += 1

    content = await file.read()
    dest.write_bytes(content)
    log.info("Uploaded invoice saved: {path}", path=dest)

    # Create DB record
    invoice = Invoice(filename=dest.name, status=InvoiceStatus.pending)
    db.add(invoice)
    await db.commit()
    await db.refresh(invoice)

    # Dispatch Celery task
    from app.tasks.invoice_tasks import process_invoice_task

    process_invoice_task.apply_async(args=[invoice.id], queue="high")
    log.info("Dispatched processing for uploaded invoice id={id}", id=invoice.id)

    return InvoiceOut.from_orm_instance(invoice)


@app.post(
    "/invoices/{invoice_id}/retry",
    response_model=InvoiceOut,
    tags=["invoices"],
)
async def retry_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)) -> InvoiceOut:
    """Manually trigger a retry for a failed or duplicate invoice."""
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")

    if invoice.status not in (InvoiceStatus.failed, InvoiceStatus.duplicate):
        raise HTTPException(
            status_code=400,
            detail=f"Invoice is in status '{invoice.status}'; only failed/duplicate invoices can be retried",
        )

    invoice.status = InvoiceStatus.pending
    await db.commit()
    await db.refresh(invoice)

    from app.tasks.invoice_tasks import process_invoice_task

    process_invoice_task.apply_async(args=[invoice.id], queue="high")
    log.info("Manual retry dispatched for invoice id={id}", id=invoice_id)

    return InvoiceOut.from_orm_instance(invoice)


@app.get("/logs", response_model=list[ProcessingLogOut], tags=["logs"])
async def list_logs(
    db: AsyncSession = Depends(get_db),
    invoice_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[ProcessingLogOut]:
    """Return recent processing log entries, optionally filtered by invoice."""
    stmt = select(ProcessingLog).order_by(ProcessingLog.created_at.desc()).limit(limit)
    if invoice_id is not None:
        stmt = stmt.where(ProcessingLog.invoice_id == invoice_id)
    result = await db.execute(stmt)
    logs = result.scalars().all()
    return [
        ProcessingLogOut(
            id=lg.id,
            invoice_id=lg.invoice_id,
            stage=lg.stage,
            status=lg.status,
            message=lg.message,
            created_at=lg.created_at.isoformat(),
        )
        for lg in logs
    ]
