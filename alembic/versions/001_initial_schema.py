"""Initial schema: invoices and processing_logs tables.

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types
    invoice_status = sa.Enum(
        "pending", "processing", "completed", "failed", "duplicate",
        name="invoicestatus",
    )
    log_status = sa.Enum(
        "started", "success", "failed", "skipped",
        name="logstatus",
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("vendor_name", sa.String(256), nullable=True),
        sa.Column("invoice_number", sa.String(128), nullable=True, index=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("invoice_date", sa.String(32), nullable=True),
        sa.Column("due_date", sa.String(32), nullable=True),
        sa.Column("status", invoice_status, nullable=False, server_default="pending"),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("extracted_data", sa.JSON(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_invoices_id", "invoices", ["id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"])

    op.create_table(
        "processing_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "invoice_id",
            sa.Integer(),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("status", log_status, nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_processing_logs_id", "processing_logs", ["id"])
    op.create_index("ix_processing_logs_invoice_id", "processing_logs", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("processing_logs")
    op.drop_table("invoices")
    op.execute("DROP TYPE IF EXISTS invoicestatus")
    op.execute("DROP TYPE IF EXISTS logstatus")
