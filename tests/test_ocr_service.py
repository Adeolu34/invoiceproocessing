"""Tests for OCRService field parsing and duplicate detection."""

from __future__ import annotations

import pytest

from app.services.ocr_service import OCRService


@pytest.fixture
def ocr() -> OCRService:
    return OCRService()


class TestParseInvoiceFields:
    def test_extracts_invoice_number(self, ocr: OCRService, sample_invoice_text: str) -> None:
        result = ocr.parse_invoice_fields(sample_invoice_text)
        assert result["invoice_number"] == "INV-2024-00847"

    def test_extracts_amount(self, ocr: OCRService, sample_invoice_text: str) -> None:
        result = ocr.parse_invoice_fields(sample_invoice_text)
        assert result["amount"] == pytest.approx(1560.60, rel=1e-2)

    def test_extracts_currency_usd(self, ocr: OCRService, sample_invoice_text: str) -> None:
        result = ocr.parse_invoice_fields(sample_invoice_text)
        assert result["currency"] == "USD"

    def test_extracts_invoice_date(self, ocr: OCRService, sample_invoice_text: str) -> None:
        result = ocr.parse_invoice_fields(sample_invoice_text)
        assert result["invoice_date"] is not None
        assert "2024" in result["invoice_date"]

    def test_extracts_due_date(self, ocr: OCRService, sample_invoice_text: str) -> None:
        result = ocr.parse_invoice_fields(sample_invoice_text)
        assert result["due_date"] is not None

    def test_confidence_scores_present(self, ocr: OCRService, sample_invoice_text: str) -> None:
        result = ocr.parse_invoice_fields(sample_invoice_text)
        conf = result["confidence"]
        assert isinstance(conf, dict)
        assert all(isinstance(v, float) for v in conf.values())

    def test_high_confidence_for_explicit_fields(
        self, ocr: OCRService, sample_invoice_text: str
    ) -> None:
        result = ocr.parse_invoice_fields(sample_invoice_text)
        assert result["confidence"].get("invoice_number", 0) >= 0.7
        assert result["confidence"].get("amount", 0) >= 0.5

    def test_minimal_invoice_returns_partial_results(
        self, ocr: OCRService, minimal_invoice_text: str
    ) -> None:
        result = ocr.parse_invoice_fields(minimal_invoice_text)
        # Should not raise — missing fields should be None not errors
        assert result["amount"] == pytest.approx(500.0, rel=1e-2)
        assert result["invoice_number"] is not None

    def test_empty_text_returns_none_fields(self, ocr: OCRService) -> None:
        result = ocr.parse_invoice_fields("")
        assert result["vendor_name"] is None
        assert result["invoice_number"] is None
        assert result["amount"] is None

    def test_currency_fallback_to_usd(self, ocr: OCRService) -> None:
        result = ocr.parse_invoice_fields("Total: 100.00\nInvoice: TEST-001")
        assert result["currency"] == "USD"
        assert result["confidence"]["currency"] <= 0.5

    def test_invoice_number_uppercased(self, ocr: OCRService, sample_invoice_text: str) -> None:
        result = ocr.parse_invoice_fields(sample_invoice_text)
        if result["invoice_number"]:
            assert result["invoice_number"] == result["invoice_number"].upper()

    def test_amount_strips_commas(self, ocr: OCRService) -> None:
        text = "Total Amount Due: $1,234,567.89\nInvoice No: X-001"
        result = ocr.parse_invoice_fields(text)
        assert result["amount"] == pytest.approx(1_234_567.89, rel=1e-2)


class TestExtractCurrency:
    def test_iso_code_detected(self, ocr: OCRService) -> None:
        val, conf = ocr._extract_currency("Amount: EUR 500.00")
        assert val == "EUR"
        assert conf > 0.9

    def test_pound_symbol_maps_to_gbp(self, ocr: OCRService) -> None:
        val, conf = ocr._extract_currency("Total: £250.00")
        assert val == "GBP"

    def test_euro_symbol_maps_to_eur(self, ocr: OCRService) -> None:
        val, conf = ocr._extract_currency("Betrag: €750")
        assert val == "EUR"

    def test_dollar_symbol_maps_to_usd(self, ocr: OCRService) -> None:
        val, conf = ocr._extract_currency("$100")
        assert val == "USD"


class TestDuplicateDetection:
    def test_no_invoice_number_returns_false(self, ocr: OCRService) -> None:
        mock_session = object()
        result = ocr.detect_duplicate({"invoice_number": None}, mock_session)
        assert result is False

    def test_empty_invoice_number_returns_false(self, ocr: OCRService) -> None:
        result = ocr.detect_duplicate({"invoice_number": ""}, object())
        assert result is False
