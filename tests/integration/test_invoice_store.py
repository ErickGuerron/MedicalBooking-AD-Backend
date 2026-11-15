"""The paid invoice read for a confirmed payment, against SQL Server."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.payments.application.billing_service import NewPayment
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from medicalbooking.modules.payments.infrastructure.invoice_store import SqlInvoiceStore
from tests.integration.checkout_support import new_id, seed_factura

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 15, 14, 30, tzinfo=UTC)


def test_paid_invoice_reads_number_patient_lines_and_totals(migrated_engine: Engine) -> None:
    store = SqlBillingStore(migrated_engine)
    numero = f"FAC-IT-{uuid.uuid4().hex[:8]}"
    document = str(uuid.uuid4().int)[:10]
    with migrated_engine.begin() as conn:
        factura = seed_factura(conn)
        conn.execute(
            text("UPDATE facturas SET numero_factura = :n WHERE id = :f"),
            {"n": numero, "f": factura},
        )
        conn.execute(
            text(
                "UPDATE pacientes SET tipo_documento = 'CEDULA', numero_documento = :d "
                "WHERE id = (SELECT paciente_id FROM facturas WHERE id = :f)"
            ),
            {"d": document, "f": factura},
        )
        conn.execute(
            text(
                "INSERT INTO facturas_detalle (id, factura_id, descripcion, cantidad, "
                "precio_unitario, subtotal) VALUES (:id, :f, N'Consulta general', 1, 25.50, 25.50)"
            ),
            {"id": new_id(), "f": factura},
        )
    pago = store.reserve_payment(
        NewPayment(
            id=new_id(),
            factura_id=factura,
            clave=f"invoice-key-{uuid.uuid4().hex[:12]}",
            monto=Decimal("25.50"),
            moneda="USD",
            creado_en=NOW,
        )
    ).payment.id
    store.confirm_payment(pago, factura, NOW)

    invoice = SqlInvoiceStore(migrated_engine).paid_invoice_for_payment(pago)

    assert invoice is not None
    assert invoice.numero_factura == numero
    assert invoice.paciente_nombre == "Nombre Apellido"
    assert invoice.documento == f"CEDULA {document}"
    assert invoice.moneda == "USD"
    assert invoice.total == Decimal("25.50")
    assert [line.descripcion for line in invoice.lineas] == ["Consulta general"]
    assert invoice.pagada_en is not None


def test_a_payment_without_an_invoice_number_has_no_invoice(migrated_engine: Engine) -> None:
    assert SqlInvoiceStore(migrated_engine).paid_invoice_for_payment(new_id()) is None
