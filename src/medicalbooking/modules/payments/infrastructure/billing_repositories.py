"""SQL Server adapter for billing: invoices and checkout attempts.

Parameterized SQL only. The invoice and its single detail line commit together. A checkout
attempt is reserved in its own short transaction: the provider is called afterwards, outside
any open transaction (spec 7.3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, Row, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.payments.application.billing_service import (
    AppointmentBilling,
    InvoiceRecord,
    NewInvoice,
    NewPayment,
    PaymentRecord,
    PaymentReservation,
)
from medicalbooking.shared.domain.errors import Conflict
from medicalbooking.shared.infrastructure.sql_errors import is_unique_violation


class SqlBillingStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def find_appointment_billing(self, cita_id: str) -> AppointmentBilling | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT c.id, p.usuario_id, c.estado, s.nombre, s.precio_base, s.moneda, "
                    "(SELECT TOP 1 f.id FROM facturas AS f WHERE f.cita_id = c.id) AS factura_id "
                    "FROM citas AS c "
                    "JOIN pacientes AS p ON p.id = c.paciente_id "
                    "JOIN servicios_medicos AS s ON s.id = c.servicio_id "
                    "WHERE c.id = :cita"
                ),
                {"cita": cita_id},
            ).first()
        if row is None:
            return None
        return AppointmentBilling(
            cita_id=str(row.id).lower(),
            paciente_user_id=str(row.usuario_id).lower(),
            estado=row.estado,
            servicio_nombre=row.nombre,
            precio=Decimal(row.precio_base),
            moneda=row.moneda.strip(),
            factura_id=str(row.factura_id).lower() if row.factura_id else None,
        )

    def create_invoice(self, invoice: NewInvoice) -> InvoiceRecord:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO facturas (id, paciente_id, cita_id, numero_factura, "
                        "subtotal, impuestos, total, moneda, estado, creada_en) "
                        "SELECT :id, c.paciente_id, c.id, :numero, :subtotal, :impuestos, "
                        ":total, :moneda, 'PENDIENTE', :creada "
                        "FROM citas AS c WHERE c.id = :cita"
                    ),
                    {
                        "id": invoice.id,
                        "numero": invoice.numero_factura,
                        "subtotal": invoice.subtotal,
                        "impuestos": invoice.impuestos,
                        "total": invoice.total,
                        "moneda": invoice.moneda,
                        "creada": _utc_naive(invoice.creada_en),
                        "cita": invoice.cita_id,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO facturas_detalle (id, factura_id, servicio_id, descripcion, "
                        "cantidad, precio_unitario, subtotal) "
                        "SELECT :detalle, :id, c.servicio_id, :descripcion, 1, :precio, :precio "
                        "FROM citas AS c WHERE c.id = :cita"
                    ),
                    {
                        "detalle": str(uuid.uuid4()),
                        "id": invoice.id,
                        "descripcion": invoice.descripcion,
                        "precio": invoice.subtotal,
                        "cita": invoice.cita_id,
                    },
                )
        except IntegrityError as error:
            if is_unique_violation(str(error.orig)):
                raise Conflict("The appointment already has an invoice") from error
            raise
        return InvoiceRecord(
            id=invoice.id,
            paciente_user_id=invoice.paciente_user_id,
            estado="PENDIENTE",
            total=invoice.total,
            moneda=invoice.moneda,
        )

    def find_invoice(self, factura_id: str) -> InvoiceRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT f.id, p.usuario_id, f.estado, f.total, f.moneda "
                    "FROM facturas AS f JOIN pacientes AS p ON p.id = f.paciente_id "
                    "WHERE f.id = :factura"
                ),
                {"factura": factura_id},
            ).first()
        if row is None:
            return None
        return InvoiceRecord(
            id=str(row.id).lower(),
            paciente_user_id=str(row.usuario_id).lower(),
            estado=row.estado,
            total=Decimal(row.total),
            moneda=row.moneda.strip(),
        )

    def find_payment_by_key(self, key: str) -> PaymentRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, factura_id, clave_idempotencia, estado FROM pagos "
                    "WHERE clave_idempotencia = :clave"
                ),
                {"clave": key},
            ).first()
        return _payment(row) if row is not None else None

    def find_pending_payment_for_invoice(self, factura_id: str) -> PaymentRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT TOP (1) id, factura_id, clave_idempotencia, estado FROM pagos "
                    "WHERE factura_id = :factura AND estado = 'PENDIENTE' "
                    "ORDER BY creado_en"
                ),
                {"factura": factura_id},
            ).first()
        return _payment(row) if row is not None else None

    def reserve_payment(self, payment: NewPayment) -> PaymentReservation:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO pagos (id, factura_id, proveedor, clave_idempotencia, "
                        "monto, moneda, estado, creado_en) "
                        "VALUES (:id, :factura, 'STRIPE', :clave, :monto, :moneda, "
                        "'PENDIENTE', :creado)"
                    ),
                    {
                        "id": payment.id,
                        "factura": payment.factura_id,
                        "clave": payment.clave,
                        "monto": payment.monto,
                        "moneda": payment.moneda,
                        "creado": _utc_naive(payment.creado_en),
                    },
                )
        except IntegrityError as error:
            if not is_unique_violation(str(error.orig)):
                raise
            existing = self.find_payment_by_key(payment.clave)
            if existing is None:
                # Another request committed a pending attempt for this invoice first.
                existing = self.find_pending_payment_for_invoice(payment.factura_id)
            if existing is None:
                raise
            return PaymentReservation(payment=existing, created=False)
        return PaymentReservation(
            payment=PaymentRecord(
                id=payment.id,
                factura_id=payment.factura_id,
                clave=payment.clave,
                estado="PENDIENTE",
            ),
            created=True,
        )

    def record_session(self, pago_id: str, session_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pagos SET stripe_checkout_session_id = :sesion "
                    "WHERE id = :pago AND stripe_checkout_session_id IS NULL"
                ),
                {"sesion": session_id, "pago": pago_id},
            )

    def mark_rejected(self, pago_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pagos SET estado = 'RECHAZADO' "
                    "WHERE id = :pago AND estado = 'PENDIENTE'"
                ),
                {"pago": pago_id},
            )


def _payment(row: Row[Any]) -> PaymentRecord:
    return PaymentRecord(
        id=str(row.id).lower(),
        factura_id=str(row.factura_id).lower(),
        clave=row.clave_idempotencia,
        estado=row.estado,
    )


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)
