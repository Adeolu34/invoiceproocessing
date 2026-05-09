"""SQLAlchemy ORM models for the invoice processing system."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ── Enums ─────────────────────────────────────────────────────────────────────


class InvoiceStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    duplicate = "duplicate"


class LogStatus(str, enum.Enum):
    started = "started"
    success = "success"
    failed = "failed"
    skipped = "skipped"


# ── Models ────────────────────────────────────────────────────────────────────


class Invoice(Base):
    """Represents a single invoice file moving through the processing pipeline."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    vendor_name: Mapped[str | None] = mapped_column(String(256))
    invoice_number: Mapped[str | None] = mapped_column(String(128), index=True)
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    invoice_date: Mapped[str | None] = mapped_column(String(32))
    due_date: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus),
        default=InvoiceStatus.pending,
        nullable=False,
        index=True,
    )
    raw_text: Mapped[str | None] = mapped_column(Text)
    extracted_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    logs: Mapped[list[ProcessingLog]] = relationship(
        "ProcessingLog", back_populates="invoice", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} number={self.invoice_number} status={self.status}>"


class ProcessingLog(Base):
    """Audit log entry for each pipeline stage of an invoice."""

    __tablename__ = "processing_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[LogStatus] = mapped_column(
        Enum(LogStatus), nullable=False
    )
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="logs")

    def __repr__(self) -> str:
        return f"<ProcessingLog id={self.id} invoice_id={self.invoice_id} stage={self.stage} status={self.status}>"
