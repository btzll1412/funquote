"""Branded quote PDF generation (ReportLab). Purely deterministic."""

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app import models


def _money(v) -> str:
    return f"${float(v or 0):,.2f}"


def render_quote_pdf(
    quote: models.Quote,
    profile: models.BusinessProfile | None,
    customer: models.Customer | None,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=f"Quote {quote.quote_number}",
    )
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=12)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=20, spaceAfter=2)

    story = []

    # Header: logo + company block on the left, quote meta on the right
    company_bits = []
    if profile:
        if profile.logo_path and Path(profile.logo_path).exists():
            try:
                img = Image(profile.logo_path)
                img._restrictSize(2.2 * inch, 1.0 * inch)
                company_bits.append(img)
            except Exception:
                pass
        name = profile.company_name or ""
        if name:
            company_bits.append(Paragraph(f"<b>{name}</b>", styles["Normal"]))
        for line in (profile.address or "").splitlines():
            company_bits.append(Paragraph(line, small))
        contact = " · ".join(x for x in (profile.phone, profile.email, profile.website) if x)
        if contact:
            company_bits.append(Paragraph(contact, small))

    meta_bits = [Paragraph("QUOTE", h1),
                 Paragraph(f"<b>{quote.quote_number}</b>", styles["Normal"]),
                 Paragraph(f"Date: {quote.created_at.strftime('%Y-%m-%d')}", small),
                 Paragraph(f"Status: {quote.status.title()}", small)]
    if quote.expiration_date:
        meta_bits.append(Paragraph(f"Valid until: {quote.expiration_date.isoformat()}", small))

    header = Table([[company_bits or [Paragraph("", small)], meta_bits]],
                   colWidths=[4.0 * inch, 3.0 * inch])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header)
    story.append(Spacer(1, 18))

    if customer:
        cust_lines = [f"<b>Quote for:</b> {customer.name}"]
        if customer.company:
            cust_lines.append(customer.company)
        if customer.address:
            cust_lines.extend(customer.address.splitlines())
        contact = " · ".join(x for x in (customer.phone, customer.email) if x)
        if contact:
            cust_lines.append(contact)
        for line in cust_lines:
            story.append(Paragraph(line, styles["Normal"]))
        story.append(Spacer(1, 14))

    # Line items
    rows = [["SKU", "Description", "Qty", "Unit Price", "Line Total"]]
    for item in quote.items:
        rows.append([
            item.sku_snapshot or "—",
            Paragraph(item.description_snapshot, small),
            f"{float(item.quantity):g}",
            _money(item.unit_price),
            _money(item.line_total),
        ])
    table = Table(rows, colWidths=[1.1 * inch, 3.2 * inch, 0.6 * inch, 1.0 * inch, 1.1 * inch],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))

    totals = [["Subtotal", _money(quote.subtotal)]]
    if float(quote.tax_rate or 0):
        totals.append([f"Tax ({float(quote.tax_rate):g}%)", _money(quote.tax)])
    totals.append(["Total", _money(quote.total)])
    tt = Table(totals, colWidths=[5.9 * inch, 1.1 * inch])
    tt.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#1f2937")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tt)

    if quote.notes:
        story.append(Spacer(1, 14))
        story.append(Paragraph("<b>Notes</b>", styles["Normal"]))
        for line in quote.notes.splitlines():
            story.append(Paragraph(line, small))

    if profile and profile.quote_footer_text:
        story.append(Spacer(1, 20))
        for line in profile.quote_footer_text.splitlines():
            story.append(Paragraph(line, ParagraphStyle(
                "footer", parent=small, textColor=colors.HexColor("#6b7280"))))

    doc.build(story)
    return buf.getvalue()
