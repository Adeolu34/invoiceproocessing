"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.config import Settings


@pytest.fixture
def sample_invoice_text() -> str:
    """Realistic invoice text for OCR parsing tests."""
    return """
    ACME Supplies Ltd
    123 Business Park, New York, NY 10001
    Tel: +1 (212) 555-0100

    INVOICE

    Invoice Number: INV-2024-00847
    Invoice Date:   15/03/2024
    Due Date:       14/04/2024

    Bill To:
    Globex Corporation
    742 Evergreen Terrace, Springfield

    Description                     Qty   Unit Price   Total
    -------------------------------------------------------
    Office Supplies - Q1 Bundle      1    $1,250.00   $1,250.00
    Printer Cartridges (x10)         2      $85.00     $170.00
    Delivery & Handling              1      $25.00      $25.00

    Subtotal:                                        $1,445.00
    Tax (8%):                                          $115.60
    Total Amount Due:                               USD 1,560.60

    Payment Terms: Net 30
    Bank: First National Bank | Account: 1234567890 | Routing: 021000021
    """


@pytest.fixture
def minimal_invoice_text() -> str:
    """Minimal invoice text — tests graceful handling of sparse data."""
    return """
    Invoice #A-001
    Total: $500.00
    Date: 2024-01-10
    """


@pytest.fixture
def mock_settings() -> Settings:
    """Return a Settings instance with safe test defaults (no real credentials)."""
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test_db",
        redis_url="redis://localhost:6379/15",
        imap_user="",
        imap_pass="",
        smtp_user="",
        smtp_pass="",
        slack_webhook_url="",
        accounting_portal_url="https://test.example.com",
        accounting_user="testuser",
        accounting_pass="testpass",
        invoice_watch_folder="/tmp/test_invoices",
        screenshots_dir="/tmp/test_screenshots",
        log_level="DEBUG",
    )
