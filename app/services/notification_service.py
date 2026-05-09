"""Slack notification service for invoice processing events."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.utils.logger import get_logger

log = get_logger(__name__)

# Slack attachment colours
COLOR_SUCCESS = "#36a64f"   # green
COLOR_FAILURE = "#e01e5a"   # red
COLOR_WARNING = "#ffcc00"   # yellow


class NotificationService:
    """Posts structured notifications to Slack via incoming webhooks."""

    def __init__(self, settings: Settings) -> None:
        self.webhook_url = settings.slack_webhook_url
        self.timeout = 10.0

    # ── Generic sender ────────────────────────────────────────────────────────

    def send_slack(
        self,
        message: str,
        color: str = COLOR_SUCCESS,
        fields: list[dict[str, str]] | None = None,
        title: str | None = None,
        footer: str = "Invoice Processing System",
    ) -> bool:
        """POST a Slack message with a rich attachment block.

        Args:
            message: Main text of the notification (markdown supported).
            color: Hex colour string for the attachment sidebar.
            fields: Optional list of {title, value, short} dicts for the attachment.
            title: Optional bold title for the attachment.
            footer: Footer text shown at the bottom of the attachment.

        Returns:
            True on success, False on any error (non-raising for resilience).
        """
        if not self.webhook_url:
            log.debug("Slack webhook not configured — skipping notification")
            return False

        attachment: dict[str, Any] = {
            "color": color,
            "text": message,
            "footer": footer,
            "mrkdwn_in": ["text", "pretext"],
        }
        if title:
            attachment["title"] = title
        if fields:
            attachment["fields"] = fields

        payload = {"attachments": [attachment]}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self.webhook_url,
                    content=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
            log.debug("Slack notification sent: {msg}", msg=message[:80])
            return True
        except httpx.HTTPStatusError as exc:
            log.warning(
                "Slack webhook returned HTTP {code}: {body}",
                code=exc.response.status_code,
                body=exc.response.text[:200],
            )
        except Exception as exc:
            log.warning("Slack notification failed: {exc}", exc=exc)
        return False

    # ── Typed helpers ─────────────────────────────────────────────────────────

    def send_slack_invoice_processed(self, invoice: Any) -> bool:
        """Send a formatted success notification for a completed invoice.

        Args:
            invoice: An Invoice ORM model instance.

        Returns:
            True on success, False on error.
        """
        fields = [
            {"title": "Invoice #", "value": invoice.invoice_number or "N/A", "short": True},
            {"title": "Vendor", "value": invoice.vendor_name or "Unknown", "short": True},
            {
                "title": "Amount",
                "value": f"{invoice.currency or 'USD'} {invoice.amount:.2f}" if invoice.amount else "N/A",
                "short": True,
            },
            {"title": "Invoice ID", "value": str(invoice.id), "short": True},
        ]
        return self.send_slack(
            message=f"Invoice *{invoice.invoice_number or invoice.filename}* has been processed and entered into the accounting system.",
            color=COLOR_SUCCESS,
            title="Invoice Processed",
            fields=fields,
        )

    def send_slack_invoice_failed(self, invoice: Any, error: str) -> bool:
        """Send a formatted failure notification for a failed invoice.

        Args:
            invoice: An Invoice ORM model instance.
            error: Human-readable error description.

        Returns:
            True on success, False on error.
        """
        # Truncate long error messages
        error_display = error[:300] + "..." if len(error) > 300 else error

        fields = [
            {"title": "Invoice #", "value": invoice.invoice_number or "N/A", "short": True},
            {"title": "File", "value": invoice.filename, "short": True},
            {"title": "Retry Count", "value": str(invoice.retry_count), "short": True},
            {"title": "Invoice ID", "value": str(invoice.id), "short": True},
            {"title": "Error", "value": f"```{error_display}```", "short": False},
        ]
        return self.send_slack(
            message=f"Failed to process invoice *{invoice.filename}* after {invoice.retry_count} retries.",
            color=COLOR_FAILURE,
            title="Invoice Processing FAILED",
            fields=fields,
        )

    def send_slack_duplicate_detected(self, invoice: Any) -> bool:
        """Send a warning notification for a duplicate invoice.

        Args:
            invoice: An Invoice ORM model instance with status=duplicate.

        Returns:
            True on success, False on error.
        """
        fields = [
            {"title": "Invoice #", "value": invoice.invoice_number or "N/A", "short": True},
            {"title": "File", "value": invoice.filename, "short": True},
            {"title": "Invoice ID", "value": str(invoice.id), "short": True},
        ]
        return self.send_slack(
            message=f"Duplicate invoice detected: *{invoice.invoice_number}* already exists in the system.",
            color=COLOR_WARNING,
            title="Duplicate Invoice",
            fields=fields,
        )
