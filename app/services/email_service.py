"""Email service: IMAP fetching and SMTP sending for invoice processing."""

from __future__ import annotations

import email
import imaplib
import os
import smtplib
import ssl
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from app.config import Settings
from app.utils.logger import get_logger

log = get_logger(__name__)


class EmailService:
    """Handles all email operations: IMAP ingestion and SMTP delivery."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._imap: imaplib.IMAP4_SSL | None = None

    # ── IMAP connection ───────────────────────────────────────────────────────

    def connect_imap(self) -> imaplib.IMAP4_SSL:
        """Connect to IMAP server over SSL and authenticate.

        Returns:
            An authenticated IMAP4_SSL client.

        Raises:
            imaplib.IMAP4.error: On authentication failure.
        """
        context = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(
            host=self.settings.imap_host,
            port=self.settings.imap_port,
            ssl_context=context,
        )
        conn.login(self.settings.imap_user, self.settings.imap_pass)
        log.info(
            "IMAP connected to {host}:{port} as {user}",
            host=self.settings.imap_host,
            port=self.settings.imap_port,
            user=self.settings.imap_user,
        )
        self._imap = conn
        return conn

    def disconnect_imap(self) -> None:
        """Logout and close the IMAP connection if open."""
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    # ── Fetch invoices ────────────────────────────────────────────────────────

    def fetch_invoice_emails(self) -> list[dict[str, Any]]:
        """Search INBOX for unseen emails with PDF attachments.

        Downloads each PDF attachment to the configured watch folder.

        Returns:
            List of dicts: {subject, sender, msg_id, attachment_paths}.
        """
        save_dir = Path(self.settings.invoice_watch_folder)
        save_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict[str, Any]] = []
        conn = self.connect_imap()

        try:
            conn.select("INBOX")
            # Search for unseen messages
            _, msg_ids_raw = conn.search(None, "UNSEEN")
            msg_ids = msg_ids_raw[0].split()
            log.info("Found {n} unseen messages", n=len(msg_ids))

            for msg_id_bytes in msg_ids:
                msg_id = msg_id_bytes.decode()
                try:
                    _, msg_data = conn.fetch(msg_id, "(RFC822)")
                    raw_email = msg_data[0][1]  # type: ignore[index]
                    msg = email.message_from_bytes(raw_email)

                    # Check for PDF attachment
                    attachment_paths = self.download_attachments(msg, save_dir)
                    if not attachment_paths:
                        continue  # Skip emails without PDF attachments

                    results.append(
                        {
                            "msg_id": msg_id,
                            "subject": msg.get("Subject", ""),
                            "sender": msg.get("From", ""),
                            "attachment_paths": attachment_paths,
                        }
                    )
                    self.mark_processed(conn, msg_id)
                    log.info(
                        "Fetched invoice email from {sender} with {n} attachments",
                        sender=msg.get("From", ""),
                        n=len(attachment_paths),
                    )
                except Exception as exc:
                    log.warning("Failed to process message {id}: {exc}", id=msg_id, exc=exc)

        finally:
            self.disconnect_imap()

        return results

    def download_attachments(self, msg: Message, save_dir: Path) -> list[str]:
        """Save all PDF attachments from an email message to disk.

        Args:
            msg: Parsed email.message.Message object.
            save_dir: Directory where attachments should be saved.

        Returns:
            List of absolute file paths of saved PDFs.
        """
        saved_paths: list[str] = []

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            is_pdf = (
                content_type == "application/pdf"
                or (content_type == "application/octet-stream" and "attachment" in content_disposition)
            )
            if not is_pdf:
                continue

            filename = part.get_filename()
            if not filename:
                filename = "attachment.pdf"

            # Sanitise filename
            safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
            if not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"

            dest = save_dir / safe_name
            # Avoid overwriting — suffix with counter if needed
            counter = 1
            while dest.exists():
                stem = Path(safe_name).stem
                dest = save_dir / f"{stem}_{counter}.pdf"
                counter += 1

            payload = part.get_payload(decode=True)
            if payload:
                dest.write_bytes(payload)
                saved_paths.append(str(dest))
                log.debug("Saved attachment to {path}", path=dest)

        return saved_paths

    def mark_processed(self, conn: imaplib.IMAP4_SSL, msg_id: str) -> None:
        """Mark an email as Seen so it is not re-fetched on the next scan.

        Args:
            conn: Active IMAP connection.
            msg_id: IMAP message sequence number string.
        """
        try:
            conn.store(msg_id, "+FLAGS", "\\Seen")
            log.debug("Marked message {id} as Seen", id=msg_id)
        except Exception as exc:
            log.warning("Failed to mark message {id} as Seen: {exc}", id=msg_id, exc=exc)

    # ── SMTP sending ──────────────────────────────────────────────────────────

    def send_confirmation(self, to_email: str, invoice_data: dict[str, Any]) -> None:
        """Send an HTML confirmation email for a processed invoice.

        Args:
            to_email: Recipient email address.
            invoice_data: Dict of invoice fields to include in the email body.
        """
        subject = f"Invoice Processed: {invoice_data.get('invoice_number', 'N/A')}"
        html_body = self._build_confirmation_html(invoice_data)

        self._send_smtp(to_email, subject, html_body)
        log.info("Confirmation email sent to {to}", to=to_email)

    def _send_smtp(self, to_email: str, subject: str, html_body: str) -> None:
        """Send an email via SMTP with STARTTLS."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.settings.smtp_user
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(self.settings.smtp_user, self.settings.smtp_pass)
            server.sendmail(self.settings.smtp_user, to_email, msg.as_string())

    @staticmethod
    def _build_confirmation_html(invoice_data: dict[str, Any]) -> str:
        """Build a simple HTML confirmation email."""
        rows = "".join(
            f"<tr><td style='padding:6px 12px;font-weight:bold;'>{k.replace('_', ' ').title()}</td>"
            f"<td style='padding:6px 12px;'>{v}</td></tr>"
            for k, v in invoice_data.items()
            if k != "confidence" and v is not None
        )
        return f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
          <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;
                      padding:30px;box-shadow:0 2px 8px rgba(0,0,0,.1);">
            <h2 style="color:#2c7be5;">Invoice Processed Successfully</h2>
            <p>Your invoice has been received and entered into the accounting system.</p>
            <table border="0" cellpadding="0" cellspacing="0"
                   style="width:100%;border-collapse:collapse;margin-top:16px;">
              {rows}
            </table>
            <p style="color:#888;font-size:12px;margin-top:24px;">
              This is an automated notification. Please do not reply.
            </p>
          </div>
        </body>
        </html>
        """
