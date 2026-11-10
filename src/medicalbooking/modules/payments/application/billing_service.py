"""Billing use cases: invoices for appointments and Stripe checkout for invoices (E4).

Business rules live here. The provider call happens outside any database transaction: the
attempt is reserved first, Stripe is called, then the session id is recorded (spec 7.3).
Missing and foreign resources answer the same Forbidden, so identifiers do not leak.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.domain.billing import (
    FACTURA_PENDIENTE,
    INVOICEABLE_APPOINTMENT_STATES,
    PAGO_PENDIENTE,
    PERM_CHECKOUT,
    PERM_INVOICE_CREATE,
    to_cents,
    validate_idempotency_key,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, ServiceUnavailable


class ProviderUnavailable(Exception):
    """The payment provider did not create a session. Raised by the gateway adapter."""


@dataclass(frozen=True)
class AppointmentBilling:
    cita_id: str
    paciente_user_id: str
    estado: str
    servicio_nombre: str
    precio: Decimal
    moneda: str
    factura_id: str | None

    def with_factura(self, factura_id: str) -> AppointmentBilling:
        return replace(self, factura_id=factura_id)


@dataclass(frozen=True)
class NewInvoice:
    id: str
    numero_factura: str
    cita_id: str
    paciente_user_id: str
    descripcion: str
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    moneda: str
    creada_en: datetime


@dataclass(frozen=True)
class InvoiceRecord:
    id: str
    paciente_user_id: str
    estado: str
    total: Decimal
    moneda: str


@dataclass(frozen=True)
class NewPayment:
    id: str
    factura_id: str
    clave: str
    monto: Decimal
    moneda: str
    creado_en: datetime


@dataclass(frozen=True)
class PaymentRecord:
    id: str
    factura_id: str
    clave: str
    estado: str


@dataclass(frozen=True)
class PaymentReservation:
    payment: PaymentRecord
    created: bool


@dataclass(frozen=True)
class CheckoutRequest:
    idempotency_key: str
    amount_cents: int
    currency: str
    description: str
    reference: str


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str


@dataclass(frozen=True)
class CheckoutStart:
    pago_id: str
    checkout_url: str
    replayed: bool


class BillingStore(Protocol):
    def find_appointment_billing(self, cita_id: str) -> AppointmentBilling | None: ...

    def create_invoice(self, invoice: NewInvoice) -> InvoiceRecord: ...

    def find_invoice(self, factura_id: str) -> InvoiceRecord | None: ...

    def find_payment_by_key(self, key: str) -> PaymentRecord | None: ...

    def reserve_payment(self, payment: NewPayment) -> PaymentReservation: ...

    def record_session(self, pago_id: str, session_id: str) -> None: ...

    def mark_rejected(self, pago_id: str) -> None: ...


class CheckoutGateway(Protocol):
    def is_configured(self) -> bool: ...

    def create_session(self, request: CheckoutRequest) -> CheckoutSession: ...


class BillingService:
    def __init__(
        self,
        store: BillingStore,
        gateway: CheckoutGateway,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._clock = clock

    def create_invoice(self, actor: AccessProfile, *, cita_id: str) -> InvoiceRecord:
        _require(actor, PERM_INVOICE_CREATE)
        appointment = self._store.find_appointment_billing(str(cita_id))
        if appointment is None or appointment.paciente_user_id != actor.user_id:
            raise Forbidden("Access to this resource is not allowed")
        if appointment.estado not in INVOICEABLE_APPOINTMENT_STATES:
            raise Conflict("The appointment cannot be invoiced in its current state")
        if appointment.factura_id is not None:
            raise Conflict("The appointment already has an invoice")
        price = appointment.precio
        return self._store.create_invoice(
            NewInvoice(
                id=str(uuid.uuid4()),
                numero_factura=_invoice_number(self._clock()),
                cita_id=appointment.cita_id,
                paciente_user_id=appointment.paciente_user_id,
                descripcion=appointment.servicio_nombre,
                subtotal=price,
                impuestos=Decimal("0"),
                total=price,
                moneda=appointment.moneda,
                creada_en=self._clock(),
            )
        )

    def start_checkout(
        self, actor: AccessProfile, *, factura_id: str, idempotency_key: str
    ) -> CheckoutStart:
        _require(actor, PERM_CHECKOUT)
        validate_idempotency_key(idempotency_key)
        factura_id = str(factura_id)
        invoice = self._store.find_invoice(factura_id)
        if invoice is None or invoice.paciente_user_id != actor.user_id:
            raise Forbidden("Access to this resource is not allowed")

        existing = self._store.find_payment_by_key(idempotency_key)
        if existing is not None:
            return self._replay(existing, invoice, factura_id, idempotency_key)

        if invoice.estado != FACTURA_PENDIENTE:
            raise Conflict("The invoice is not pending payment")
        if not self._gateway.is_configured():
            raise ServiceUnavailable("Payment provider is not configured")

        reservation = self._store.reserve_payment(
            NewPayment(
                id=str(uuid.uuid4()),
                factura_id=factura_id,
                clave=idempotency_key,
                monto=invoice.total,
                moneda=invoice.moneda,
                creado_en=self._clock(),
            )
        )
        if not reservation.created:
            return self._replay(reservation.payment, invoice, factura_id, idempotency_key)
        return self._open_session(invoice, reservation.payment, idempotency_key, replayed=False)

    def _replay(
        self,
        payment: PaymentRecord,
        invoice: InvoiceRecord,
        factura_id: str,
        idempotency_key: str,
    ) -> CheckoutStart:
        if payment.factura_id != factura_id:
            raise Conflict("The Idempotency-Key was already used for another invoice")
        if payment.estado != PAGO_PENDIENTE:
            raise Conflict("The payment attempt is no longer pending")
        return self._open_session(invoice, payment, idempotency_key, replayed=True)

    def _open_session(
        self,
        invoice: InvoiceRecord,
        payment: PaymentRecord,
        idempotency_key: str,
        *,
        replayed: bool,
    ) -> CheckoutStart:
        request = CheckoutRequest(
            idempotency_key=idempotency_key,
            amount_cents=to_cents(invoice.total),
            currency=invoice.moneda,
            description="Factura MedicalBooking",
            reference=payment.id,
        )
        try:
            session = self._gateway.create_session(request)
        except ProviderUnavailable as error:
            self._store.mark_rejected(payment.id)
            raise ServiceUnavailable("Payment provider is unavailable") from error
        self._store.record_session(payment.id, session.id)
        return CheckoutStart(pago_id=payment.id, checkout_url=session.url, replayed=replayed)


def _require(actor: AccessProfile, permission: str) -> None:
    if permission not in actor.permissions:
        raise Forbidden("Access to this resource is not allowed")


def _invoice_number(moment: datetime) -> str:
    return f"FAC-{moment:%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


__all__ = [
    "AppointmentBilling",
    "BillingService",
    "BillingStore",
    "CheckoutGateway",
    "CheckoutRequest",
    "CheckoutSession",
    "CheckoutStart",
    "InvoiceRecord",
    "NewInvoice",
    "NewPayment",
    "PaymentRecord",
    "PaymentReservation",
    "ProviderUnavailable",
]
