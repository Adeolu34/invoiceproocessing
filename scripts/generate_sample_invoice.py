"""Generate realistic sample invoice PDFs for testing the pipeline.

Usage:
    python scripts/generate_sample_invoice.py              # generates 5 invoices
    python scripts/generate_sample_invoice.py --count 10   # generates 10 invoices
    python scripts/generate_sample_invoice.py --out ./my_folder
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

VENDORS = [
    "Acme Supplies Ltd",
    "TechParts Inc",
    "Global Logistics Co",
    "Office Depot Business",
    "CloudInfra Solutions",
    "PrintWorks Agency",
    "Catering Express LLC",
]

CURRENCIES = ["USD", "EUR", "GBP"]
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}

LINE_ITEMS = [
    ("Office Supplies Bundle", 1, 250.00),
    ("Software Licence (Annual)", 1, 1200.00),
    ("Cloud Hosting - Pro Plan", 1, 399.00),
    ("Marketing Materials (500 units)", 500, 0.85),
    ("Consulting Hours (x8)", 8, 175.00),
    ("Printer Cartridges x10", 10, 28.50),
    ("Delivery & Handling", 1, 25.00),
    ("Training Workshop", 1, 650.00),
    ("Data Storage 1TB/month", 1, 89.99),
]


def _generate_invoice_number() -> str:
    prefix = random.choice(["INV", "BILL", "REF"])
    year = date.today().year
    seq = random.randint(1000, 9999)
    return f"{prefix}-{year}-{seq:05d}"


def _format_amount(amount: float, symbol: str) -> str:
    return f"{symbol}{amount:,.2f}"


def generate_pdf(output_path: Path, vendor: str, currency: str) -> None:
    """Create a single invoice PDF at *output_path*."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        print("reportlab not installed. Run: pip install reportlab")
        sys.exit(1)

    invoice_date = date.today() - timedelta(days=random.randint(0, 30))
    due_date = invoice_date + timedelta(days=30)
    invoice_number = _generate_invoice_number()
    symbol = CURRENCY_SYMBOLS[currency]

    # Pick 2–4 random line items
    items = random.sample(LINE_ITEMS, k=random.randint(2, 4))
    subtotal = sum(qty * price for _, qty, price in items)
    tax_rate = 0.08
    tax = subtotal * tax_rate
    total = subtotal + tax

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvoiceTitle", parent=styles["Heading1"], fontSize=24, textColor=colors.HexColor("#2C3E50")
    )
    normal = styles["Normal"]
    label_style = ParagraphStyle(
        "Label", parent=normal, fontSize=9, textColor=colors.grey
    )
    value_style = ParagraphStyle(
        "Value", parent=normal, fontSize=10, fontName="Helvetica-Bold"
    )

    story = []

    # Header
    story.append(Paragraph(vendor, title_style))
    story.append(Paragraph("123 Business Avenue, New York, NY 10001", normal))
    story.append(Paragraph("Tel: +1 (212) 555-0100  |  billing@vendor.example.com", normal))
    story.append(Spacer(1, 8 * mm))

    # Invoice metadata table
    meta_data = [
        ["INVOICE", "", "", ""],
        [
            Paragraph("Invoice Number:", label_style),
            Paragraph(invoice_number, value_style),
            Paragraph("Invoice Date:", label_style),
            Paragraph(invoice_date.strftime("%d/%m/%Y"), value_style),
        ],
        [
            Paragraph("Currency:", label_style),
            Paragraph(currency, value_style),
            Paragraph("Due Date:", label_style),
            Paragraph(due_date.strftime("%d/%m/%Y"), value_style),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[45 * mm, 55 * mm, 40 * mm, 45 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (3, 0)),
                ("FONTSIZE", (0, 0), (3, 0), 16),
                ("FONTNAME", (0, 0), (3, 0), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (3, 0), colors.HexColor("#2C3E50")),
                ("BOTTOMPADDING", (0, 0), (3, 0), 6),
                ("LINEBELOW", (0, 0), (3, 0), 1, colors.HexColor("#BDC3C7")),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 6 * mm))

    # Bill-to section
    story.append(Paragraph("Bill To:", label_style))
    story.append(Paragraph("Globex Corporation", value_style))
    story.append(Paragraph("742 Evergreen Terrace, Springfield, IL 62701", normal))
    story.append(Spacer(1, 8 * mm))

    # Line items table
    table_data = [["Description", "Qty", "Unit Price", "Total"]]
    for desc, qty, unit_price in items:
        line_total = qty * unit_price
        table_data.append([
            desc,
            str(qty),
            _format_amount(unit_price, symbol),
            _format_amount(line_total, symbol),
        ])

    # Subtotals
    table_data.append(["", "", "Subtotal:", _format_amount(subtotal, symbol)])
    table_data.append(["", "", f"Tax ({int(tax_rate * 100)}%):", _format_amount(tax, symbol)])
    table_data.append(["", "", f"Total Amount Due ({currency}):", _format_amount(total, symbol)])

    col_widths = [95 * mm, 15 * mm, 40 * mm, 35 * mm]
    items_table = Table(table_data, colWidths=col_widths)
    items_table.setStyle(
        TableStyle(
            [
                # Header row
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("ROWBACKGROUNDS", (0, 1), (-1, -4), [colors.white, colors.HexColor("#F8F9FA")]),
                # Totals section
                ("LINEABOVE", (2, -3), (-1, -3), 1, colors.HexColor("#BDC3C7")),
                ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (2, -1), (-1, -1), 11),
                ("LINEABOVE", (2, -1), (-1, -1), 1, colors.HexColor("#2C3E50")),
                # Grid
                ("GRID", (0, 0), (-1, -4), 0.5, colors.HexColor("#DEE2E6")),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 10 * mm))

    # Payment terms
    story.append(Paragraph("Payment Terms: Net 30", normal))
    story.append(
        Paragraph(
            "Bank: First National Bank  |  Account: 1234567890  |  Routing: 021000021",
            normal,
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            "Please include the Invoice Number as your payment reference.",
            label_style,
        )
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Invoice {invoice_number}",
        author=vendor,
    )
    doc.build(story)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sample invoice PDFs for testing")
    parser.add_argument("--count", type=int, default=5, help="Number of invoices to generate")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sample_invoices"),
        help="Output directory (created if it does not exist)",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Generating {args.count} sample invoice(s) in '{args.out}/'...")
    for i in range(1, args.count + 1):
        vendor = random.choice(VENDORS)
        currency = random.choice(CURRENCIES)
        invoice_num = f"sample_invoice_{i:03d}.pdf"
        out_path = args.out / invoice_num
        generate_pdf(out_path, vendor, currency)
        print(f"  [{i:>3}/{args.count}] {invoice_num}  ({vendor}, {currency})")

    print(f"\nDone. {args.count} invoice(s) saved to '{args.out}/'")
    print("\nTo process them, drop the folder path into INVOICE_WATCH_FOLDER in .env")
    print("or upload via POST /invoices/process")


if __name__ == "__main__":
    main()
