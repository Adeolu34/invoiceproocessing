"""OCR service: PDF text extraction and invoice field parsing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.utils.logger import get_logger

log = get_logger(__name__)

# ── Regex patterns for common invoice fields ──────────────────────────────────

_VENDOR_PATTERNS = [
    r"(?:from|vendor|billed?\s+by|company)[:\s]+([A-Za-z0-9 ,.\-&']{3,80})",
    r"^([A-Z][A-Za-z0-9 ,.\-&']{2,60})\s*\n",  # first line heuristic
]

_INVOICE_NUMBER_PATTERNS = [
    r"invoice\s*(?:#|no\.?|number)[:\s]*([A-Z0-9\-/]{3,30})",
    r"inv[.\s#]*([A-Z0-9\-/]{3,30})",
    r"(?:ref|reference)[:\s]+([A-Z0-9\-/]{3,30})",
]

_AMOUNT_PATTERNS = [
    r"(?:total|amount\s+due|balance\s+due|grand\s+total)[:\s]*[\$£€]?\s*([\d,]+\.?\d*)",
    r"(?:subtotal|net\s+amount)[:\s]*[\$£€]?\s*([\d,]+\.?\d*)",
    r"[\$£€]\s*([\d,]+\.\d{2})",
]

_CURRENCY_PATTERNS = [
    r"\b(USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR|MXN)\b",
    r"([\$£€])",
]

_CURRENCY_SYMBOL_MAP = {"$": "USD", "£": "GBP", "€": "EUR"}

_DATE_PATTERNS = [
    r"(?:invoice\s+date|date\s+of\s+invoice|issued?)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
    r"(?:invoice\s+date|date\s+of\s+invoice|issued?)[:\s]*(\w+ \d{1,2},?\s+\d{4})",
    r"date[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
]

_DUE_DATE_PATTERNS = [
    r"(?:due\s+date|payment\s+due|pay\s+by|due\s+by)[:\s]*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})",
    r"(?:due\s+date|payment\s+due|pay\s+by|due\s+by)[:\s]*(\w+ \d{1,2},?\s+\d{4})",
]


class OCRService:
    """Handles PDF text extraction and structured field parsing."""

    # ── Extraction ─────────────────────────────────────────────────────────────

    def extract_from_pdf(self, path: str) -> dict[str, Any]:
        """Extract text from a PDF file.

        Tries pdfplumber first (handles text-layer PDFs accurately).
        Falls back to pytesseract (rasterises pages for scanned PDFs).

        Args:
            path: Absolute path to the PDF file.

        Returns:
            dict with keys ``raw_text`` (str) and ``extraction_method`` (str).

        Raises:
            FileNotFoundError: If the PDF does not exist.
            RuntimeError: If both extraction methods fail.
        """
        pdf_path = Path(path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")

        log.debug("Extracting text from {path}", path=path)

        # Try pdfplumber (text layer)
        try:
            text = self._extract_with_pdfplumber(pdf_path)
            if text and len(text.strip()) > 50:
                log.debug("pdfplumber extraction successful ({n} chars)", n=len(text))
                return {"raw_text": text, "extraction_method": "pdfplumber"}
            log.debug("pdfplumber returned minimal text, falling back to OCR")
        except Exception as exc:
            log.warning("pdfplumber failed: {exc}", exc=exc)

        # Fall back to pytesseract
        try:
            text = self._extract_with_tesseract(pdf_path)
            log.debug("tesseract extraction successful ({n} chars)", n=len(text))
            return {"raw_text": text, "extraction_method": "tesseract"}
        except Exception as exc:
            log.error("tesseract failed: {exc}", exc=exc)
            raise RuntimeError(f"Both pdfplumber and tesseract failed for {path}") from exc

    def _extract_with_pdfplumber(self, path: Path) -> str:
        """Use pdfplumber to extract the text layer from a PDF."""
        import pdfplumber  # type: ignore

        pages_text: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    pages_text.append(page_text)
        return "\n".join(pages_text)

    def _extract_with_tesseract(self, path: Path) -> str:
        """Rasterise each PDF page and apply Tesseract OCR."""
        import pytesseract
        from pdf2image import convert_from_path  # type: ignore
        from PIL import Image

        images = convert_from_path(str(path), dpi=300)
        pages_text: list[str] = []
        for img in images:
            text = pytesseract.image_to_string(img, config="--psm 6")
            pages_text.append(text)
        return "\n".join(pages_text)

    # ── Field parsing ──────────────────────────────────────────────────────────

    def parse_invoice_fields(self, raw_text: str) -> dict[str, Any]:
        """Extract structured fields from raw invoice text using regex heuristics.

        Args:
            raw_text: The full text content of the invoice.

        Returns:
            dict containing: vendor_name, invoice_number, amount, currency,
            invoice_date, due_date, confidence (dict of per-field scores 0–1).
        """
        text_lower = raw_text.lower()
        result: dict[str, Any] = {
            "vendor_name": None,
            "invoice_number": None,
            "amount": None,
            "currency": None,
            "invoice_date": None,
            "due_date": None,
            "confidence": {},
        }

        # Vendor name
        result["vendor_name"], result["confidence"]["vendor_name"] = self._extract_first(
            raw_text, _VENDOR_PATTERNS, flags=re.IGNORECASE | re.MULTILINE
        )

        # Invoice number
        result["invoice_number"], result["confidence"]["invoice_number"] = self._extract_first(
            text_lower, _INVOICE_NUMBER_PATTERNS, flags=re.IGNORECASE
        )
        if result["invoice_number"]:
            result["invoice_number"] = result["invoice_number"].upper()

        # Amount
        raw_amount, conf = self._extract_first(text_lower, _AMOUNT_PATTERNS, flags=re.IGNORECASE)
        if raw_amount:
            try:
                result["amount"] = float(raw_amount.replace(",", ""))
                result["confidence"]["amount"] = conf
            except ValueError:
                result["confidence"]["amount"] = 0.0
        else:
            result["confidence"]["amount"] = 0.0

        # Currency
        result["currency"], result["confidence"]["currency"] = self._extract_currency(raw_text)

        # Invoice date
        result["invoice_date"], result["confidence"]["invoice_date"] = self._extract_first(
            text_lower, _DATE_PATTERNS, flags=re.IGNORECASE
        )

        # Due date
        result["due_date"], result["confidence"]["due_date"] = self._extract_first(
            text_lower, _DUE_DATE_PATTERNS, flags=re.IGNORECASE
        )

        log.debug(
            "Parsed fields: vendor={v} number={n} amount={a} currency={c}",
            v=result["vendor_name"],
            n=result["invoice_number"],
            a=result["amount"],
            c=result["currency"],
        )
        return result

    # ── Duplicate detection ────────────────────────────────────────────────────

    def detect_duplicate(
        self,
        invoice_data: dict[str, Any],
        db_session,
        exclude_id: int | None = None,
    ) -> bool:
        """Check whether an invoice with the same invoice_number already exists.

        Args:
            invoice_data: Dict containing at least ``invoice_number``.
            db_session: A synchronous SQLAlchemy Session.
            exclude_id: Invoice id to exclude from the check (the current record).

        Returns:
            True if a duplicate exists, False otherwise.
        """
        from sqlalchemy import select

        from app.models import Invoice

        invoice_number = invoice_data.get("invoice_number")
        if not invoice_number:
            # Cannot determine duplicate without an invoice number
            return False

        stmt = select(Invoice).where(Invoice.invoice_number == invoice_number)
        if exclude_id is not None:
            from sqlalchemy import and_

            stmt = stmt.where(Invoice.id != exclude_id)

        existing = db_session.execute(stmt).scalars().first()
        if existing:
            log.info(
                "Duplicate found: invoice_number={num} already exists as id={id}",
                num=invoice_number,
                id=existing.id,
            )
            return True
        return False

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_first(
        text: str, patterns: list[str], flags: int = 0
    ) -> tuple[str | None, float]:
        """Try each pattern in order; return (match_group_1, confidence_score)."""
        for i, pattern in enumerate(patterns):
            m = re.search(pattern, text, flags)
            if m:
                value = m.group(1).strip()
                # Higher confidence for earlier (more specific) patterns
                confidence = 1.0 - (i * 0.15)
                return value, max(confidence, 0.1)
        return None, 0.0

    @staticmethod
    def _extract_currency(text: str) -> tuple[str, float]:
        """Return (ISO currency code, confidence)."""
        # Try ISO codes first
        m = re.search(r"\b(USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR|MXN)\b", text, re.IGNORECASE)
        if m:
            return m.group(1).upper(), 0.95

        # Fall back to currency symbols
        m = re.search(r"([$£€])", text)
        if m:
            return _CURRENCY_SYMBOL_MAP.get(m.group(1), "USD"), 0.70

        # Default
        return "USD", 0.30
