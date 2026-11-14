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

from sqlalchemy import Connection, Engine, Row, text
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
from medicalbooking.shared.infrastructure.outbox import append_outbox
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
                detalle_id = str(uuid.uuid4())
                conn.execute(
                    text(
                        "INSERT INTO facturas_detalle (id, factura_id, servicio_id, descripcion, "
                        "cantidad, precio_unitario, subtotal) "
                        "SELECT :detalle, :id, c.servicio_id, :descripcion, 1, :precio, :precio "
                        "FROM citas AS c WHERE c.id = :cita"
                    ),
                    {
                        "detalle": detalle_id,
                        "id": invoice.id,
                        "descripcion": invoice.descripcion,
                        "precio": invoice.subtotal,
                        "cita": invoice.cita_id,
                    },
                )
                # Replica snapshot: the invoice and its detail line. No contact data.
                paciente_row = conn.execute(
                    text("SELECT paciente_id, servicio_id FROM citas WHERE id = :c"),
                    {"c": invoice.cita_id},
                ).one()
                append_outbox(
                    conn,
                    "factura",
                    str(invoice.id),
                    "factura.creada",
                    {
                        "factura": {
                            "id": str(invoice.id).lower(),
                            "paciente_id": str(paciente_row.paciente_id).lower(),
                            "cita_id": str(invoice.cita_id).lower(),
                            "numero_factura": invoice.numero_factura,
                            "subtotal": str(invoice.subtotal),
                            "impuestos": str(invoice.impuestos),
                            "total": str(invoice.total),
                            "moneda": invoice.moneda,
                            "estado": "PENDIENTE",
                            "creada_en": _utc_naive(invoice.creada_en).isoformat(),
                        },
                        "detalles": [
                            {
                                "id": detalle_id,
                                "servicio_id": str(paciente_row.servicio_id).lower(),
                                "descripcion": invoice.descripcion,
                                "cantidad": 1,
                                "precio_unitario": str(invoice.subtotal),
                                "subtotal": str(invoice.subtotal),
                            }
                        ],
                    },
                    _utc_naive(invoice.creada_en),
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
                    "SELECT id, factura_id, clave_idempotencia, estado, "
                    "stripe_checkout_session_id, reintentos_realizados, reintentos_agotados, "
                    "proximo_reintento_en, ultimo_error_codigo FROM pagos "
                    "WHERE clave_idempotencia = :clave"
                ),
                {"clave": key},
            ).first()
        return _payment(row) if row is not None else None

    def find_pending_payment_for_invoice(self, factura_id: str) -> PaymentRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT TOP (1) id, factura_id, clave_idempotencia, estado, "
                    "stripe_checkout_session_id, reintentos_realizados, reintentos_agotados, "
                    "proximo_reintento_en, ultimo_error_codigo FROM pagos "
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
                emit_pago_snapshot(conn, payment.id, _utc_naive(payment.creado_en))
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

    def find_latest_payment_for_invoice(self, factura_id: str) -> PaymentRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT TOP (1) id, factura_id, clave_idempotencia, estado, "
                    "stripe_checkout_session_id, reintentos_realizados, reintentos_agotados, "
                    "proximo_reintento_en, ultimo_error_codigo FROM pagos "
                    "WHERE factura_id = :factura ORDER BY creado_en DESC"
                ),
                {"factura": factura_id},
            ).first()
        return _payment(row) if row is not None else None

    def find_due_checkout_retries(self, now: datetime, limit: int) -> list[PaymentRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT TOP (:limite) id, factura_id, clave_idempotencia, estado, "
                    "stripe_checkout_session_id, reintentos_realizados, reintentos_agotados, "
                    "proximo_reintento_en, ultimo_error_codigo FROM pagos "
                    "WHERE estado = 'PENDIENTE' AND stripe_checkout_session_id IS NULL "
                    "AND reintentos_agotados = 0 AND proximo_reintento_en <= :ahora "
                    "ORDER BY proximo_reintento_en"
                ),
                {"limite": limit, "ahora": _utc_naive(now)},
            ).all()
        return [_payment(row) for row in rows]

    def record_session(self, pago_id: str, session_id: str, retries_made: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pagos SET stripe_checkout_session_id = :sesion, "
                    "reintentos_realizados = :reintentos, proximo_reintento_en = NULL "
                    "WHERE id = :pago AND stripe_checkout_session_id IS NULL"
                ),
                {"sesion": session_id, "pago": pago_id, "reintentos": retries_made},
            )
            emit_pago_snapshot(conn, pago_id, _utc_naive(datetime.now(UTC)))

    def record_create_failure(
        self, pago_id: str, retries_made: int, next_retry_at: datetime | None, error_code: str
    ) -> None:
        # Exhausted (no next retry) attempts are rejected, which frees the invoice for a new
        # attempt. The guard keeps a racing request from overwriting a session that now exists.
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pagos SET reintentos_realizados = :reintentos, "
                    "ultimo_error_codigo = :codigo, proximo_reintento_en = :proximo, "
                    "reintentos_agotados = CASE WHEN :proximo IS NULL THEN 1 ELSE 0 END, "
                    "estado = CASE WHEN :proximo IS NULL THEN 'RECHAZADO' ELSE estado END "
                    "WHERE id = :pago AND estado = 'PENDIENTE' "
                    "AND stripe_checkout_session_id IS NULL"
                ),
                {
                    "reintentos": retries_made,
                    "codigo": error_code,
                    "proximo": _utc_naive(next_retry_at) if next_retry_at is not None else None,
                    "pago": pago_id,
                },
            )
            emit_pago_snapshot(conn, pago_id, _utc_naive(datetime.now(UTC)))

    def record_payment_intent(self, pago_id: str, payment_intent_id: str) -> None:
        with self._engine.begin() as conn:
            set_payment_intent(conn, pago_id, payment_intent_id)

    def mark_rejected(self, pago_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pagos SET estado = 'RECHAZADO' "
                    "WHERE id = :pago AND estado = 'PENDIENTE'"
                ),
                {"pago": pago_id},
            )
            emit_pago_snapshot(conn, pago_id, _utc_naive(datetime.now(UTC)))

    def confirm_payment(self, pago_id: str, factura_id: str, now: datetime) -> None:
        # Same guarded updates as the verified webhook: a racing event changes nothing twice.
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pagos SET estado = 'CONFIRMADO', pagado_en = :ahora "
                    "WHERE id = :pago AND estado = 'PENDIENTE'"
                ),
                {"pago": pago_id, "ahora": _utc_naive(now)},
            )
            conn.execute(
                text(
                    "UPDATE facturas SET estado = 'PAGADA' "
                    "WHERE id = :factura AND estado = 'PENDIENTE'"
                ),
                {"factura": factura_id},
            )
            emit_pago_snapshot(conn, pago_id, _utc_naive(now))

    def cancel_pending_payment(self, pago_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE pagos SET estado = 'CANCELADO' "
                    "WHERE id = :pago AND estado = 'PENDIENTE'"
                ),
                {"pago": pago_id},
            )
            emit_pago_snapshot(conn, pago_id, _utc_naive(datetime.now(UTC)))


def _payment(row: Row[Any]) -> PaymentRecord:
    session_id = row.stripe_checkout_session_id
    next_retry = row.proximo_reintento_en
    return PaymentRecord(
        session_id=str(session_id) if session_id is not None else None,
        id=str(row.id).lower(),
        factura_id=str(row.factura_id).lower(),
        clave=row.clave_idempotencia,
        estado=row.estado,
        reintentos_realizados=int(row.reintentos_realizados),
        reintentos_agotados=bool(row.reintentos_agotados),
        proximo_reintento_en=next_retry.replace(tzinfo=UTC) if next_retry is not None else None,
        ultimo_error_codigo=row.ultimo_error_codigo,
    )


def set_payment_intent(conn: Connection, pago_id: str, payment_intent_id: str) -> None:
    """Store the provider's payment intent id once, inside the caller's transaction. The NULL
    guard makes a repeat (a redelivered webhook, a second event) change nothing. Deliberately
    no outbox emission: provider identifiers never leave SQL (see emit_pago_snapshot)."""
    conn.execute(
        text(
            "UPDATE pagos SET stripe_payment_intent_id = :intent "
            "WHERE id = :pago AND stripe_payment_intent_id IS NULL"
        ),
        {"intent": payment_intent_id, "pago": pago_id},
    )


def emit_pago_snapshot(conn: Connection, pago_id: str, moment: datetime) -> None:
    """Replica snapshot of a payment and its invoice, written with the change itself.

    Gateway identifiers and idempotency keys are never read here, so they can never leave SQL.
    The snapshot is read inside the caller's transaction, after the change.
    """
    row = conn.execute(
        text(
            "SELECT p.id, p.factura_id, p.estado, p.monto, p.moneda, p.creado_en, p.pagado_en, "
            "f.paciente_id, f.estado AS factura_estado "
            "FROM pagos AS p JOIN facturas AS f ON f.id = p.factura_id WHERE p.id = :p"
        ),
        {"p": pago_id},
    ).one()
    append_outbox(
        conn,
        "pago",
        str(row.id),
        "pago.actualizado",
        {
            "pago": {
                "id": str(row.id).lower(),
                "factura_id": str(row.factura_id).lower(),
                "estado": row.estado,
                "monto": str(row.monto),
                "moneda": row.moneda.strip(),
                "creado_en": row.creado_en.isoformat(),
                "pagado_en": row.pagado_en.isoformat() if row.pagado_en else None,
            },
            "factura": {
                "id": str(row.factura_id).lower(),
                "paciente_id": str(row.paciente_id).lower(),
                "estado": row.factura_estado,
            },
        },
        moment,
    )


def emit_reembolso_snapshot(conn: Connection, reembolso_id: str, moment: datetime) -> None:
    """Replica snapshot of a refund, its payment and its invoice, written with the change itself.

    Only the closed reason code is projected; the free-text reason, the provider's refund id and
    the idempotency keys are never read here. Read inside the caller's transaction, after the
    change.
    """
    row = conn.execute(
        text(
            "SELECT r.id, r.pago_id, r.monto, r.estado, r.solicitado_en, r.procesado_en, "
            "r.motivo, p.factura_id, p.monto AS pago_monto, p.moneda, p.estado AS pago_estado, "
            "p.creado_en, p.pagado_en, f.paciente_id, f.estado AS factura_estado "
            "FROM reembolsos AS r "
            "JOIN pagos AS p ON p.id = r.pago_id "
            "JOIN facturas AS f ON f.id = p.factura_id "
            "WHERE r.id = :r"
        ),
        {"r": reembolso_id},
    ).one()
    append_outbox(
        conn,
        "reembolso",
        str(row.id),
        "reembolso.actualizado",
        {
            "reembolso": {
                "id": str(row.id).lower(),
                "pago_id": str(row.pago_id).lower(),
                "monto": str(row.monto),
                "estado": row.estado,
                "solicitado_en": row.solicitado_en.isoformat(),
                "procesado_en": row.procesado_en.isoformat() if row.procesado_en else None,
                "motivo_codigo": row.motivo,
            },
            "pago": {
                "id": str(row.pago_id).lower(),
                "factura_id": str(row.factura_id).lower(),
                "monto": str(row.pago_monto),
                "moneda": row.moneda.strip(),
                "estado": row.pago_estado,
                "creado_en": row.creado_en.isoformat(),
                "pagado_en": row.pagado_en.isoformat() if row.pagado_en else None,
            },
            "factura": {
                "id": str(row.factura_id).lower(),
                "paciente_id": str(row.paciente_id).lower(),
                "estado": row.factura_estado,
            },
        },
        moment,
    )


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)
