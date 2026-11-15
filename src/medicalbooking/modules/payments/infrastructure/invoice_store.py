"""Reads the paid invoice of a payment for the patient's confirmation email.

Read only. Provider identifiers, idempotency keys and the full document number never leave
this query: the PDF receives the masked document through the invoice builder.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Engine, text

from medicalbooking.modules.payments.application.invoice_report import (
    InvoiceDocument,
    InvoiceLine,
)


class SqlInvoiceStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def paid_invoice_for_payment(self, pago_id: str) -> InvoiceDocument | None:
        with self._engine.connect() as conn:
            header = conn.execute(
                text(
                    "SELECT f.numero_factura, f.emitida_en, f.creada_en, g.pagado_en, "
                    "u.nombre, u.apellido, pa.tipo_documento, pa.numero_documento, "
                    "u.telefono, u.email, "
                    "f.moneda, f.subtotal, f.impuestos, f.total "
                    "FROM pagos g JOIN facturas f ON f.id = g.factura_id "
                    "JOIN pacientes pa ON pa.id = f.paciente_id "
                    "JOIN usuarios u ON u.id = pa.usuario_id WHERE g.id = :p"
                ),
                {"p": pago_id},
            ).first()
            if header is None or not header.numero_factura:
                return None
            detail = conn.execute(
                text(
                    "SELECT d.descripcion, d.cantidad, d.precio_unitario, d.subtotal "
                    "FROM facturas_detalle d JOIN pagos g ON g.factura_id = d.factura_id "
                    "WHERE g.id = :p ORDER BY d.descripcion"
                ),
                {"p": pago_id},
            ).all()
        document = (
            f"{header.tipo_documento} {header.numero_documento}"
            if header.numero_documento
            else None
        )
        return InvoiceDocument(
            numero_factura=header.numero_factura,
            emitida_en=_as_utc(header.emitida_en or header.creada_en),
            pagada_en=_as_utc(header.pagado_en) if header.pagado_en else None,
            paciente_nombre=f"{header.nombre} {header.apellido}".strip(),
            documento=document,
            moneda=header.moneda.strip(),
            lineas=tuple(
                InvoiceLine(
                    descripcion=row.descripcion,
                    cantidad=int(row.cantidad),
                    precio_unitario=Decimal(row.precio_unitario),
                    subtotal=Decimal(row.subtotal),
                )
                for row in detail
            ),
            subtotal=Decimal(header.subtotal),
            impuestos=Decimal(header.impuestos),
            total=Decimal(header.total),
            paciente_telefono=header.telefono or None,
            paciente_email=header.email or None,
        )


def _as_utc(moment: datetime) -> datetime:
    # SQL Server datetime2 values are stored in UTC without a zone.
    return moment.replace(tzinfo=UTC)
