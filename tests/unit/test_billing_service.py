"""Billing use cases against in-memory fakes: invoices, checkout, ownership and idempotency."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.billing_service import (
    AppointmentBilling,
    BillingService,
    CheckoutRequest,
    CheckoutSession,
    InvoiceRecord,
    NewInvoice,
    NewPayment,
    PaymentRecord,
    PaymentReservation,
    ProviderUnavailable,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, ServiceUnavailable

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
KEY = "checkout-key-0001"


def _id() -> str:
    return str(uuid.uuid4())


def _patient(user_id: str | None = None, permissions=None) -> AccessProfile:
    return AccessProfile(
        user_id=user_id or _id(),
        status="ACTIVO",
        roles=frozenset({"PACIENTE"}),
        permissions=frozenset({"facturas.crear", "pagos.iniciar"})
        if permissions is None
        else permissions,
    )


@dataclass
class FakeBillingStore:
    appointments: dict[str, AppointmentBilling] = field(default_factory=dict)
    invoices: dict[str, InvoiceRecord] = field(default_factory=dict)
    payments: dict[str, PaymentRecord] = field(default_factory=dict)
    created_invoices: list[NewInvoice] = field(default_factory=list)
    reserve_calls: int = 0
    sessions: dict[str, str] = field(default_factory=dict)
    rejected: set[str] = field(default_factory=set)

    def find_appointment_billing(self, cita_id):
        return self.appointments.get(cita_id)

    def create_invoice(self, invoice: NewInvoice):
        self.created_invoices.append(invoice)
        self.invoices[invoice.id] = InvoiceRecord(
            id=invoice.id,
            paciente_user_id=invoice.paciente_user_id,
            estado="PENDIENTE",
            total=invoice.total,
            moneda=invoice.moneda,
        )
        self.appointments[invoice.cita_id] = self.appointments[invoice.cita_id].with_factura(
            invoice.id
        )
        return self.invoices[invoice.id]

    def find_invoice(self, factura_id):
        return self.invoices.get(factura_id)

    def find_payment_by_key(self, key):
        return next((p for p in self.payments.values() if p.clave == key), None)

    def reserve_payment(self, payment: NewPayment) -> PaymentReservation:
        existing = self.find_payment_by_key(payment.clave)
        if existing is not None:
            return PaymentReservation(payment=existing, created=False)
        self.reserve_calls += 1
        record = PaymentRecord(
            id=payment.id,
            factura_id=payment.factura_id,
            clave=payment.clave,
            estado="PENDIENTE",
        )
        self.payments[payment.id] = record
        return PaymentReservation(payment=record, created=True)

    def record_session(self, pago_id, session_id):
        self.sessions[pago_id] = session_id

    def mark_rejected(self, pago_id):
        self.rejected.add(pago_id)


@dataclass
class FakeGateway:
    configured: bool = True
    fail: bool = False
    requests: list[CheckoutRequest] = field(default_factory=list)

    def is_configured(self) -> bool:
        return self.configured

    def create_session(self, request: CheckoutRequest) -> CheckoutSession:
        self.requests.append(request)
        if self.fail:
            raise ProviderUnavailable("provider down")
        return CheckoutSession(id=f"cs_{len(self.requests)}", url="https://checkout.test/x")


def _appointment(owner: str, estado: str = "PENDIENTE_PAGO", factura_id=None) -> AppointmentBilling:
    return AppointmentBilling(
        cita_id=_id(),
        paciente_user_id=owner,
        estado=estado,
        servicio_nombre="Consulta general",
        precio=Decimal("25.50"),
        moneda="USD",
        factura_id=factura_id,
    )


def _service(store: FakeBillingStore, gateway: FakeGateway) -> BillingService:
    return BillingService(store=store, gateway=gateway, clock=lambda: NOW)


def _seeded(owner: str, **kwargs) -> tuple[FakeBillingStore, AppointmentBilling]:
    store = FakeBillingStore()
    appointment = _appointment(owner, **kwargs)
    store.appointments[appointment.cita_id] = appointment
    return store, appointment


# --- invoices -----------------------------------------------------------------


def test_invoice_price_comes_from_the_medical_service_with_one_line():
    owner = _id()
    store, appointment = _seeded(owner)
    invoice = _service(store, FakeGateway()).create_invoice(
        _patient(owner), cita_id=appointment.cita_id
    )
    created = store.created_invoices[0]
    assert invoice.total == Decimal("25.50")
    assert created.subtotal == Decimal("25.50")
    assert created.impuestos == Decimal("0")
    assert created.moneda == "USD"
    assert created.descripcion == "Consulta general"
    assert created.numero_factura.startswith("FAC-")


def test_invoice_requires_the_permission():
    owner = _id()
    store, appointment = _seeded(owner)
    with pytest.raises(Forbidden):
        _service(store, FakeGateway()).create_invoice(
            _patient(owner, permissions=frozenset()), cita_id=appointment.cita_id
        )


def test_missing_and_foreign_appointments_answer_the_same_forbidden():
    owner = _id()
    store, appointment = _seeded(owner)
    service = _service(store, FakeGateway())
    with pytest.raises(Forbidden) as missing:
        service.create_invoice(_patient(owner), cita_id=_id())
    with pytest.raises(Forbidden) as foreign:
        service.create_invoice(_patient(_id()), cita_id=appointment.cita_id)
    assert missing.value.detail == foreign.value.detail
    assert store.created_invoices == []


@pytest.mark.parametrize("estado", ["CANCELADA", "EXPIRADA", "COMPLETADA", "NO_ASISTIO"])
def test_invoice_only_for_active_appointments(estado):
    owner = _id()
    store, appointment = _seeded(owner, estado=estado)
    with pytest.raises(Conflict):
        _service(store, FakeGateway()).create_invoice(_patient(owner), cita_id=appointment.cita_id)


@pytest.mark.parametrize("estado", ["PENDIENTE_PAGO", "CONFIRMADA"])
def test_invoice_allowed_for_pending_or_confirmed_appointments(estado):
    owner = _id()
    store, appointment = _seeded(owner, estado=estado)
    _service(store, FakeGateway()).create_invoice(_patient(owner), cita_id=appointment.cita_id)
    assert len(store.created_invoices) == 1


def test_second_invoice_for_the_same_appointment_conflicts():
    owner = _id()
    store, appointment = _seeded(owner, factura_id=_id())
    with pytest.raises(Conflict):
        _service(store, FakeGateway()).create_invoice(_patient(owner), cita_id=appointment.cita_id)


# --- checkout -----------------------------------------------------------------


def _invoiced(owner: str):
    store, appointment = _seeded(owner)
    service = _service(store, FakeGateway())
    invoice = service.create_invoice(_patient(owner), cita_id=appointment.cita_id)
    return store, invoice


def test_checkout_reserves_a_pending_payment_then_records_the_session():
    owner = _id()
    store, invoice = _invoiced(owner)
    gateway = FakeGateway()
    result = _service(store, gateway).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key=KEY
    )
    assert result.replayed is False
    assert result.checkout_url == "https://checkout.test/x"
    assert store.reserve_calls == 1
    request = gateway.requests[0]
    assert request.amount_cents == 2550
    assert request.currency == "USD"
    assert request.idempotency_key == KEY
    assert request.reference == result.pago_id
    assert store.sessions == {result.pago_id: "cs_1"}


def test_checkout_requires_the_permission():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(Forbidden):
        _service(store, FakeGateway()).start_checkout(
            _patient(owner, permissions=frozenset({"facturas.crear"})),
            factura_id=invoice.id,
            idempotency_key=KEY,
        )


def test_checkout_for_a_foreign_or_missing_invoice_is_forbidden_without_side_effects():
    owner = _id()
    store, invoice = _invoiced(owner)
    gateway = FakeGateway()
    service = _service(store, gateway)
    with pytest.raises(Forbidden):
        service.start_checkout(_patient(_id()), factura_id=invoice.id, idempotency_key=KEY)
    with pytest.raises(Forbidden):
        service.start_checkout(_patient(owner), factura_id=_id(), idempotency_key=KEY)
    assert store.reserve_calls == 0
    assert gateway.requests == []


def test_checkout_refuses_a_paid_invoice():
    owner = _id()
    store, invoice = _invoiced(owner)
    store.invoices[invoice.id] = InvoiceRecord(
        id=invoice.id,
        paciente_user_id=owner,
        estado="PAGADA",
        total=invoice.total,
        moneda="USD",
    )
    with pytest.raises(Conflict):
        _service(store, FakeGateway()).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )


def test_same_key_and_same_invoice_replays_without_a_second_payment():
    owner = _id()
    store, invoice = _invoiced(owner)
    gateway = FakeGateway()
    service = _service(store, gateway)
    first = service.start_checkout(_patient(owner), factura_id=invoice.id, idempotency_key=KEY)
    second = service.start_checkout(_patient(owner), factura_id=invoice.id, idempotency_key=KEY)
    assert second.replayed is True
    assert second.pago_id == first.pago_id
    assert store.reserve_calls == 1
    # Same provider idempotency key: Stripe returns the same session for a retry.
    assert [r.idempotency_key for r in gateway.requests] == [KEY, KEY]


def test_same_key_with_another_invoice_conflicts():
    owner = _id()
    store, invoice = _invoiced(owner)
    service = _service(store, FakeGateway())
    service.start_checkout(_patient(owner), factura_id=invoice.id, idempotency_key=KEY)
    other = _id()
    store.invoices[other] = InvoiceRecord(
        id=other, paciente_user_id=owner, estado="PENDIENTE", total=Decimal("1"), moneda="USD"
    )
    with pytest.raises(Conflict):
        service.start_checkout(_patient(owner), factura_id=other, idempotency_key=KEY)


def test_unconfigured_provider_fails_closed_before_reserving_anything():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(configured=False)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    assert store.reserve_calls == 0


def test_provider_failure_marks_the_attempt_rejected_and_answers_unavailable():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    assert len(store.rejected) == 1
    assert store.sessions == {}


def test_short_idempotency_key_is_rejected():
    owner = _id()
    store, invoice = _invoiced(owner)
    from medicalbooking.shared.domain.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        _service(store, FakeGateway()).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key="short"
        )


def test_replay_does_not_open_a_session_for_a_paid_invoice():
    owner = _id()
    store, invoice = _invoiced(owner)
    gateway = FakeGateway()
    service = _service(store, gateway)
    service.start_checkout(_patient(owner), factura_id=invoice.id, idempotency_key=KEY)
    service.start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key="checkout-key-0002"
    )
    store.invoices[invoice.id] = InvoiceRecord(
        id=invoice.id, paciente_user_id=owner, estado="PAGADA", total=invoice.total, moneda="USD"
    )
    calls = len(gateway.requests)
    with pytest.raises(Conflict):
        service.start_checkout(_patient(owner), factura_id=invoice.id, idempotency_key=KEY)
    assert len(gateway.requests) == calls


def test_replay_without_provider_creates_nothing_and_keeps_the_attempt_pending():
    owner = _id()
    store, invoice = _invoiced(owner)
    _service(store, FakeGateway()).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key=KEY
    )
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(configured=False)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    assert store.rejected == set()
