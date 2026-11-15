"""The patient's invoice as a PDF, styled after the results report.

Built at send time from the paid invoice and never stored. The document number is masked the
same way as in the results report: only the last four digits of the patient's document appear.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from medicalbooking.modules.laboratory.application.results_report import (
    BOTTOM,
    ECUADOR,
    LINE,
    MARGIN,
    MUTED,
    ORANGE,
    PAGE_H,
    PAGE_W,
    TINT,
    WHITE,
    _clip,
    _Document,
)


@dataclass(frozen=True)
class InvoiceLine:
    descripcion: str
    cantidad: int
    precio_unitario: Decimal
    subtotal: Decimal


@dataclass(frozen=True)
class InvoiceDocument:
    numero_factura: str
    emitida_en: datetime
    pagada_en: datetime | None
    paciente_nombre: str
    documento: str | None
    moneda: str
    lineas: tuple[InvoiceLine, ...]
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal


def masked_document(value: str | None) -> str:
    """Document type and the last four digits only; the full number never reaches the PDF."""
    if not value:
        return "No registrado"
    kind, _, number = value.rpartition(" ")
    if not number:
        return "******"
    return f"{kind} ******{number[-4:]}".strip()


def _money(amount: Decimal, currency: str) -> str:
    return f"{amount:.2f} {currency}"


def build_invoice_pdf(invoice: InvoiceDocument) -> bytes:
    document = _Document()
    page = document.page

    page.rect(0, PAGE_H - 120, PAGE_W, 120, ORANGE)
    page.text(MARGIN, PAGE_H - 62, "Factura", 24, bold=True, color=WHITE)
    page.text(MARGIN, PAGE_H - 88, f"Numero {invoice.numero_factura}", 11, color=WHITE)
    local = invoice.emitida_en.astimezone(ECUADOR)
    page.text(
        PAGE_W - MARGIN - 150, PAGE_H - 88, f"Emitida {local:%d/%m/%Y %H:%M}", 10, color=WHITE
    )

    y = PAGE_H - 150
    page.text(MARGIN, y, "Paciente", 9, bold=True, color=MUTED)
    page.text(MARGIN, y - 16, _clip(invoice.paciente_nombre, 70), 12, bold=True)
    page.text(MARGIN, y - 32, f"Documento: {masked_document(invoice.documento)}", 10)
    if invoice.pagada_en is not None:
        paid = invoice.pagada_en.astimezone(ECUADOR)
        page.text(PAGE_W - MARGIN - 200, y - 16, f"Pagada el {paid:%d/%m/%Y %H:%M}", 10)

    y -= 70
    page.rect(MARGIN, y, PAGE_W - 2 * MARGIN, 22, TINT)
    page.text(MARGIN + 8, y + 7, "Descripcion", 9, bold=True)
    page.text(PAGE_W - MARGIN - 230, y + 7, "Cant.", 9, bold=True)
    page.text(PAGE_W - MARGIN - 170, y + 7, "Precio", 9, bold=True)
    page.text(PAGE_W - MARGIN - 80, y + 7, "Subtotal", 9, bold=True)
    y -= 6

    for line in invoice.lineas:
        y -= 22
        if y < BOTTOM:
            document.new_page()
            page = document.page
            y = PAGE_H - 80
        page.hline(MARGIN, PAGE_W - MARGIN, y - 6, LINE)
        page.text(MARGIN + 8, y, _clip(line.descripcion, 58), 10)
        page.text(PAGE_W - MARGIN - 230, y, str(line.cantidad), 10)
        page.text(PAGE_W - MARGIN - 170, y, _money(line.precio_unitario, invoice.moneda), 10)
        page.text(PAGE_W - MARGIN - 80, y, _money(line.subtotal, invoice.moneda), 10)

    y -= 40
    for label, amount, bold in (
        ("Subtotal", invoice.subtotal, False),
        ("Impuestos", invoice.impuestos, False),
        ("Total", invoice.total, True),
    ):
        page.text(PAGE_W - MARGIN - 170, y, label, 10, bold=bold)
        page.text(PAGE_W - MARGIN - 80, y, _money(amount, invoice.moneda), 10, bold=bold)
        y -= 18

    page.text(
        MARGIN,
        y - 20,
        "Documento generado por Medilo. Conserva este comprobante.",
        9,
        color=MUTED,
    )
    return document.render()
