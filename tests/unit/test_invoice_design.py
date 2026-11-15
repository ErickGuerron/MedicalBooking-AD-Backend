from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from medicalbooking.modules.payments.application.invoice_report import (
    InvoiceDocument,
    InvoiceLine,
    build_invoice_pdf,
)


def _invoice(lines: int = 1, paid: bool = True) -> InvoiceDocument:
    items = tuple(
        InvoiceLine(
            descripcion=f"Servicio {index}",
            cantidad=1,
            precio_unitario=Decimal("10.00"),
            subtotal=Decimal("10.00"),
        )
        for index in range(lines)
    )
    total = Decimal("10.00") * lines
    return InvoiceDocument(
        numero_factura="FAC-000123",
        emitida_en=datetime(2026, 11, 15, 14, 30, tzinfo=UTC),
        pagada_en=datetime(2026, 11, 15, 14, 35, tzinfo=UTC) if paid else None,
        paciente_nombre="Maria Lopez",
        documento="CEDULA 1712345678",
        moneda="USD",
        lineas=items,
        subtotal=total,
        impuestos=Decimal("0.00"),
        total=total,
    )


def _text(pdf: bytes) -> str:
    return pdf.decode("latin-1")


def test_the_footer_is_the_invoice_footer_not_the_laboratory_one() -> None:
    text = _text(build_invoice_pdf(_invoice()))

    assert "Laboratorio clínico" not in text
    assert "Conserva este comprobante." in text
    assert "Av. Los Andes y Mera" in text


def test_a_paid_invoice_shows_the_stamp_and_the_payment_date() -> None:
    text = _text(build_invoice_pdf(_invoice(paid=True)))

    assert "(PAGADA) Tj" in text
    assert "Fecha de pago" in text


def test_an_unpaid_invoice_has_no_stamp_and_no_payment_date() -> None:
    text = _text(build_invoice_pdf(_invoice(paid=False)))

    assert "PAGADA" not in text
    assert "Fecha de pago" not in text


def test_a_long_invoice_continues_on_a_second_page_with_its_footer() -> None:
    pdf = build_invoice_pdf(_invoice(lines=20))

    text = _text(pdf)
    assert pdf.count(b"/Type /Page ") == 2
    assert "Página 1 de 2" in text
    assert "Página 2 de 2" in text
