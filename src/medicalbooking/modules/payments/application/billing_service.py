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
from medicalbooking.modules.payments.domain.checkout_retry import (
    CHECKOUT_UNAVAILABLE_CODE,
    MAX_CHECKOUT_RETRIES,
    next_retry_at,
    retry_key,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ServiceUnavailable


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
    session_id: str | None = None
    reintentos_realizados: int = 0
    reintentos_agotados: bool = False
    proximo_reintento_en: datetime | None = None
    ultimo_error_codigo: str | None = None

    @property
    def retry_pending(self) -> bool:
        return self.proximo_reintento_en is not None


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
    customer_id: str


@dataclass(frozen=True)
class CheckoutSession:
    """An embedded Checkout Session. The client secret mounts it in the browser, never a URL."""

    id: str
    client_secret: str


@dataclass(frozen=True)
class CheckoutSnapshot:
    """What Stripe says about a session now. Read only: asking never charges anything."""

    id: str
    url: str | None
    status: str
    payment_status: str
    amount_cents: int | None
    currency: str | None


@dataclass(frozen=True)
class CheckoutStatus:
    pago_id: str
    estado: str
    reintentos_realizados: int
    reintentos_maximos: int
    reintentos_agotados: bool
    ultimo_error_codigo: str | None
    proximo_reintento_en: datetime | None


@dataclass(frozen=True)
class RetryRun:
    attempted: int
    succeeded: int
    failed: int


@dataclass(frozen=True)
class CheckoutStart:
    pago_id: str
    client_secret: str
    publishable_key: str
    replayed: bool


class BillingStore(Protocol):
    def find_appointment_billing(self, cita_id: str) -> AppointmentBilling | None: ...

    def create_invoice(self, invoice: NewInvoice) -> InvoiceRecord: ...

    def find_invoice(self, factura_id: str) -> InvoiceRecord | None: ...

    def find_payment_by_key(self, key: str) -> PaymentRecord | None: ...

    def find_pending_payment_for_invoice(self, factura_id: str) -> PaymentRecord | None: ...

    def reserve_payment(self, payment: NewPayment) -> PaymentReservation: ...

    def record_session(self, pago_id: str, session_id: str, retries_made: int) -> None: ...

    def mark_rejected(self, pago_id: str) -> None: ...

    def record_create_failure(
        self, pago_id: str, retries_made: int, next_retry_at: datetime | None, error_code: str
    ) -> None: ...

    def confirm_payment(self, pago_id: str, factura_id: str, now: datetime) -> None: ...

    def cancel_pending_payment(self, pago_id: str) -> None: ...

    def find_due_checkout_retries(self, now: datetime, limit: int) -> list[PaymentRecord]: ...

    def find_latest_payment_for_invoice(self, factura_id: str) -> PaymentRecord | None: ...


class CheckoutGateway(Protocol):
    publishable_key: str | None

    def is_configured(self) -> bool: ...

    def create_session(self, request: CheckoutRequest) -> CheckoutSession: ...

    def retrieve_session(self, session_id: str) -> CheckoutSnapshot: ...


class CustomerResolver(Protocol):
    """The user's Stripe customer. Created on first need (see customers.CustomerDirectory)."""

    def ensure(self, usuario_id: str) -> str: ...


class BillingService:
    def __init__(
        self,
        store: BillingStore,
        gateway: CheckoutGateway,
        clock: Callable[[], datetime],
        customers: CustomerResolver,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._clock = clock
        self._customers = customers

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
        self,
        actor: AccessProfile,
        *,
        factura_id: str,
        idempotency_key: str,
        reintentar_ahora: bool = False,
    ) -> CheckoutStart:
        _require(actor, PERM_CHECKOUT)
        validate_idempotency_key(idempotency_key)
        factura_id = str(factura_id)
        invoice = self._store.find_invoice(factura_id)
        if invoice is None or invoice.paciente_user_id != actor.user_id:
            raise Forbidden("Access to this resource is not allowed")

        # Replays run the same guards as a first request: a key can never outlive the invoice
        # state or the provider configuration that authorized it.
        if invoice.estado != FACTURA_PENDIENTE:
            raise Conflict("The invoice is not pending payment")
        if not self._gateway.is_configured():
            raise ServiceUnavailable("Payment provider is not configured")

        existing = self._store.find_payment_by_key(idempotency_key)
        if existing is not None:
            return self._replay(existing, invoice, factura_id, idempotency_key, reintentar_ahora)

        # A new key never opens a second checkout while an attempt is still pending. Stripe is
        # asked first (read only): an open session is reopened, a paid one is reconciled, an
        # expired one is cancelled and the request falls through to a new attempt.
        pending = self._store.find_pending_payment_for_invoice(factura_id)
        if pending is not None:
            resumed = self._resume_pending(invoice, pending, reintentar_ahora)
            if resumed is not None:
                return resumed

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
            # The unique index may hand back the attempt another request just committed.
            if reservation.payment.clave != idempotency_key:
                return self._reopen_pending(invoice, reservation.payment, reintentar_ahora)
            return self._replay(
                reservation.payment, invoice, factura_id, idempotency_key, reintentar_ahora
            )
        return self._open_session(
            invoice, reservation.payment, idempotency_key, replayed=False, retry=False
        )

    def _resume_pending(
        self, invoice: InvoiceRecord, payment: PaymentRecord, reintentar_ahora: bool
    ) -> CheckoutStart | None:
        """Return the checkout to resume, raise when the invoice is settled, or None when the
        attempt was cancelled and a new one must open. Stripe failures change nothing."""
        if payment.session_id is None:
            return self._reopen_pending(invoice, payment, reintentar_ahora)
        snapshot = self._retrieve(payment.session_id)
        if snapshot.status == "open":
            return self._reopen_pending(invoice, payment, reintentar_ahora)
        if snapshot.status == "expired":
            self._store.cancel_pending_payment(payment.id)
            return None
        if snapshot.status != "complete":
            raise ServiceUnavailable("Payment provider returned an unknown session state")
        if snapshot.payment_status != "paid":
            raise Conflict("The payment is still being processed")
        if (
            snapshot.amount_cents != to_cents(invoice.total)
            or snapshot.currency != invoice.moneda.upper()
        ):
            raise ServiceUnavailable("The paid session does not match the invoice")
        self._store.confirm_payment(payment.id, payment.factura_id, self._clock())
        raise Conflict("The invoice is already paid")

    def _retrieve(self, session_id: str) -> CheckoutSnapshot:
        try:
            return self._gateway.retrieve_session(session_id)
        except ProviderUnavailable as error:
            raise ServiceUnavailable("Payment provider is unavailable") from error

    def _reopen_pending(
        self, invoice: InvoiceRecord, payment: PaymentRecord, reintentar_ahora: bool
    ) -> CheckoutStart:
        # A recorded session is reopened with the key that created it (its attempt number), so
        # Stripe returns that same session and nothing new is recorded.
        if payment.session_id is not None:
            key = _attempt_number_key(payment, payment.reintentos_realizados)
            return self._open_session(invoice, payment, key, replayed=True, retry=False)
        # Without a recorded failure the attempt's own key returns its existing session.
        if payment.reintentos_realizados == 0 and not payment.retry_pending:
            return self._open_session(invoice, payment, payment.clave, replayed=True, retry=False)
        # A scheduled retry runs only when the patient asks for it explicitly. A routine reopen
        # must not spend one of the five retries, or a burst would exhaust them in seconds.
        if not reintentar_ahora:
            raise Conflict("A checkout retry is scheduled for this payment attempt")
        # After a provider failure every retry sends a new key: Stripe would replay it otherwise.
        return self._open_session(
            invoice,
            payment,
            retry_key(payment.clave, payment.reintentos_realizados + 1),
            replayed=True,
            retry=True,
        )

    def _replay(
        self,
        payment: PaymentRecord,
        invoice: InvoiceRecord,
        factura_id: str,
        idempotency_key: str,
        reintentar_ahora: bool,
    ) -> CheckoutStart:
        if payment.factura_id != factura_id:
            raise Conflict("The Idempotency-Key was already used for another invoice")
        if payment.estado != PAGO_PENDIENTE:
            raise Conflict("The payment attempt is no longer pending")
        return self._reopen_pending(invoice, payment, reintentar_ahora)

    def checkout_status(self, actor: AccessProfile, *, factura_id: str) -> CheckoutStatus:
        _require(actor, PERM_CHECKOUT)
        invoice = self._store.find_invoice(str(factura_id))
        if invoice is None or invoice.paciente_user_id != actor.user_id:
            raise Forbidden("Access to this resource is not allowed")
        payment = self._store.find_latest_payment_for_invoice(str(factura_id))
        if payment is None:
            raise NotFound("This invoice has no checkout attempt")
        return CheckoutStatus(
            pago_id=payment.id,
            estado=payment.estado,
            reintentos_realizados=payment.reintentos_realizados,
            reintentos_maximos=MAX_CHECKOUT_RETRIES,
            reintentos_agotados=payment.reintentos_agotados,
            ultimo_error_codigo=payment.ultimo_error_codigo,
            proximo_reintento_en=payment.proximo_reintento_en,
        )

    def retry_due_checkouts(self, limit: int) -> RetryRun:
        """Run the scheduled retries that are due now (checkout-retry S5, S9). Each retry uses
        the key of its own number, so concurrent runs converge on the same provider session."""
        if not self._gateway.is_configured():
            raise ServiceUnavailable("Payment provider is not configured")
        attempted = succeeded = failed = 0
        for payment in self._store.find_due_checkout_retries(self._clock(), limit):
            attempted += 1
            invoice = self._store.find_invoice(payment.factura_id)
            if invoice is None or invoice.estado != FACTURA_PENDIENTE:
                self._store.cancel_pending_payment(payment.id)
                continue
            try:
                self._open_session(
                    invoice,
                    payment,
                    retry_key(payment.clave, payment.reintentos_realizados + 1),
                    replayed=True,
                    retry=True,
                )
            except ServiceUnavailable:
                failed += 1
            else:
                succeeded += 1
        return RetryRun(attempted=attempted, succeeded=succeeded, failed=failed)

    def _record_create_failure(self, payment: PaymentRecord, retry: bool) -> None:
        # The attempt stays pending with its next retry scheduled (checkout-retry S1, S8).
        made = payment.reintentos_realizados + 1 if retry else payment.reintentos_realizados
        self._store.record_create_failure(
            payment.id,
            made,
            next_retry_at(made, self._clock()),
            CHECKOUT_UNAVAILABLE_CODE,
        )

    def _open_session(
        self,
        invoice: InvoiceRecord,
        payment: PaymentRecord,
        idempotency_key: str,
        *,
        replayed: bool,
        retry: bool,
    ) -> CheckoutStart:
        # A customer failure leaves the attempt pending without a session: it is not rejected,
        # so the next request resumes it and the customer is asked again.
        try:
            customer_id = self._customers.ensure(invoice.paciente_user_id)
        except ServiceUnavailable:
            # A scheduled retry must count even when the customer step fails, or it never ends.
            if retry:
                self._record_create_failure(payment, retry)
            raise
        request = CheckoutRequest(
            idempotency_key=idempotency_key,
            amount_cents=to_cents(invoice.total),
            currency=invoice.moneda,
            description="Factura MedicalBooking",
            reference=payment.id,
            customer_id=customer_id,
        )
        try:
            session = self._gateway.create_session(request)
        except ProviderUnavailable as error:
            self._record_create_failure(payment, retry)
            raise ServiceUnavailable("Payment provider is unavailable") from error
        made = payment.reintentos_realizados + 1 if retry else payment.reintentos_realizados
        self._store.record_session(payment.id, session.id, made)
        return CheckoutStart(
            pago_id=payment.id,
            client_secret=session.client_secret,
            publishable_key=self._publishable_key(),
            replayed=replayed,
        )

    def _publishable_key(self) -> str:
        key = self._gateway.publishable_key
        if not key:
            raise ServiceUnavailable("Payment provider is not configured")
        return key


def _attempt_number_key(payment: PaymentRecord, retries_made: int) -> str:
    """The key of the create call numbered `retries_made`: the attempt's own key for the first
    call, then one derived key per retry."""
    if retries_made == 0:
        return payment.clave
    return retry_key(payment.clave, retries_made)


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
