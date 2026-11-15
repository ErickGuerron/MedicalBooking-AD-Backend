from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from medicalbooking.modules.payments.application.invoice_report import (
    InvoiceDocument,
    InvoiceLine,
    build_invoice_pdf,
)

NOW = datetime(2026, 11, 15, 15, 0, tzinfo=UTC)


def _invoice(**overrides) -> InvoiceDocument:
    values = {
        "numero_factura": "FAC-000123",
        "emitida_en": NOW,
        "pagada_en": NOW,
        "paciente_nombre": "Maria Lopez",
        "documento": "CI 1712345678",
        "moneda": "USD",
        "lineas": (InvoiceLine("Consulta", 1, Decimal("25.00"), Decimal("25.00")),),
        "subtotal": Decimal("25.00"),
        "impuestos": Decimal("0.00"),
        "total": Decimal("25.00"),
    }
    values.update(overrides)
    return InvoiceDocument(**values)


def test_phone_and_email_are_printed_when_known() -> None:
    pdf = build_invoice_pdf(
        _invoice(paciente_telefono="0991234567", paciente_email="maria@example.com")
    )

    assert b"0991234567" in pdf
    assert b"maria@example.com" in pdf


def test_missing_contact_data_says_not_registered() -> None:
    pdf = build_invoice_pdf(_invoice())

    assert b"No registrado" in pdf


def test_address_is_printed_only_when_the_record_has_one() -> None:
    without = build_invoice_pdf(_invoice())
    with_address = build_invoice_pdf(_invoice(paciente_direccion="Calle Bolivar 12"))

    assert b"Calle Bolivar 12" not in without
    assert b"Calle Bolivar 12" in with_address


def test_the_document_stays_masked_with_its_type() -> None:
    pdf = build_invoice_pdf(_invoice())

    assert b"CI ******5678" in pdf
    assert b"1712345678" not in pdf
