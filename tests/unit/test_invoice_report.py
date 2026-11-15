from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from medicalbooking.modules.payments.application.invoice_report import (
    InvoiceDocument,
    InvoiceLine,
    build_invoice_pdf,
)


def _invoice(**overrides) -> InvoiceDocument:
    values = dict(
        numero_factura="FAC-000123",
        emitida_en=datetime(2026, 11, 15, 14, 30, tzinfo=UTC),
        pagada_en=datetime(2026, 11, 15, 14, 35, tzinfo=UTC),
        paciente_nombre="Maria Lopez",
        documento="CEDULA 1712345678",
        moneda="USD",
        lineas=(
            InvoiceLine(
                descripcion="Consulta de medicina general",
                cantidad=1,
                precio_unitario=Decimal("25.00"),
                subtotal=Decimal("25.00"),
            ),
        ),
        subtotal=Decimal("25.00"),
        impuestos=Decimal("0.00"),
        total=Decimal("25.00"),
    )
    values.update(overrides)
    return InvoiceDocument(**values)


def _text(pdf: bytes) -> str:
    return pdf.decode("latin-1")


def test_invoice_is_a_pdf_with_its_number_patient_and_totals() -> None:
    pdf = build_invoice_pdf(_invoice())

    text = _text(pdf)
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert "FAC-000123" in text
    assert "Maria Lopez" in text
    assert "Consulta de medicina general" in text
    assert "25.00 USD" in text


def test_invoice_masks_the_document_to_its_last_four_digits() -> None:
    pdf = build_invoice_pdf(_invoice())

    text = _text(pdf)
    assert "1712345678" not in text
    assert "5678" in text


def test_invoice_lists_every_detail_line_in_order() -> None:
    lines = (
        InvoiceLine("Consulta", 1, Decimal("25.00"), Decimal("25.00")),
        InvoiceLine("Laboratorio basico", 2, Decimal("10.00"), Decimal("20.00")),
    )

    text = _text(build_invoice_pdf(_invoice(lineas=lines, total=Decimal("45.00"))))

    assert text.index("Consulta") < text.index("Laboratorio basico")
    assert "45.00 USD" in text
