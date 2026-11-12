"""Billing use cases against in-memory fakes: invoices, checkout, ownership and idempotency."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.billing_service import (
    AppointmentBilling,
    BillingService,
    CheckoutRequest,
    CheckoutSession,
    CheckoutSnapshot,
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
    confirmed: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)

    def confirm_payment(self, pago_id, factura_id, now):
        self.confirmed.append(pago_id)
        self.payments[pago_id] = replace(self.payments[pago_id], estado="CONFIRMADO")
        self.invoices[factura_id] = replace(self.invoices[factura_id], estado="PAGADA")

    def cancel_pending_payment(self, pago_id):
        self.cancelled.append(pago_id)
        self.payments[pago_id] = replace(self.payments[pago_id], estado="CANCELADO")

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
        # Mirrors the unique index on pending attempts per invoice (migration 0019).
        pending = self._pending_for(payment.factura_id)
        if pending is not None:
            return PaymentReservation(payment=pending, created=False)
        self.reserve_calls += 1
        record = PaymentRecord(
            id=payment.id,
            factura_id=payment.factura_id,
            clave=payment.clave,
            estado="PENDIENTE",
        )
        self.payments[payment.id] = record
        return PaymentReservation(payment=record, created=True)

    def record_session(self, pago_id, session_id, retries_made):
        self.sessions[pago_id] = session_id
        self.payments[pago_id] = replace(
            self.payments[pago_id],
            session_id=session_id,
            reintentos_realizados=retries_made,
            proximo_reintento_en=None,
        )

    def record_create_failure(self, pago_id, retries_made, next_retry_at, error_code):
        payment = self.payments[pago_id]
        if next_retry_at is None:
            self.rejected.add(pago_id)
            self.payments[pago_id] = replace(
                payment,
                estado="RECHAZADO",
                reintentos_realizados=retries_made,
                reintentos_agotados=True,
                proximo_reintento_en=None,
            )
            return
        self.payments[pago_id] = replace(
            payment, reintentos_realizados=retries_made, proximo_reintento_en=next_retry_at
        )

    def find_pending_payment_for_invoice(self, factura_id):
        return self._pending_for(factura_id)

    def _pending_for(self, factura_id):
        return next(
            (
                p
                for p in self.payments.values()
                if p.factura_id == factura_id and p.estado == "PENDIENTE"
            ),
            None,
        )

    def mark_rejected(self, pago_id):
        self.rejected.add(pago_id)
        self.payments[pago_id] = replace(self.payments[pago_id], estado="RECHAZADO")


@dataclass
class FakeGateway:
    configured: bool = True
    fail: bool = False
    retrieve_fail: bool = False
    publishable_key: str | None = "pk_test_fake_publishable"
    requests: list[CheckoutRequest] = field(default_factory=list)
    sessions: dict[str, CheckoutSnapshot] = field(default_factory=dict)
    by_key: dict[str, CheckoutSession] = field(default_factory=dict)

    def is_configured(self) -> bool:
        return self.configured

    def create_session(self, request: CheckoutRequest) -> CheckoutSession:
        self.requests.append(request)
        if self.fail:
            raise ProviderUnavailable("provider down")
        # Like Stripe, a repeated Idempotency-Key returns the session it already created.
        if request.idempotency_key in self.by_key:
            return self.by_key[request.idempotency_key]
        session_id = f"cs_{len(self.requests)}"
        self.sessions[session_id] = CheckoutSnapshot(
            id=session_id,
            url=None,
            status="open",
            payment_status="unpaid",
            amount_cents=request.amount_cents,
            currency=request.currency,
        )
        session = CheckoutSession(id=session_id, client_secret=f"{session_id}_secret")
        self.by_key[request.idempotency_key] = session
        return session

    def retrieve_session(self, session_id: str) -> CheckoutSnapshot:
        if self.retrieve_fail:
            raise ProviderUnavailable("provider down")
        return self.sessions[session_id]


@dataclass
class FakeCustomers:
    """Stands in for the per-user Stripe customer. One customer per user, like the real one."""

    failing: bool = False
    ensured: list[str] = field(default_factory=list)

    def ensure(self, usuario_id: str) -> str:
        self.ensured.append(usuario_id)
        if self.failing:
            raise ServiceUnavailable("Payment provider is unavailable")
        return f"cus_{usuario_id[:8]}"


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


def _service(
    store: FakeBillingStore, gateway: FakeGateway, customers: FakeCustomers | None = None
) -> BillingService:
    return BillingService(
        store=store, gateway=gateway, clock=lambda: NOW, customers=customers or FakeCustomers()
    )


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
    assert result.client_secret == "cs_1_secret"
    assert result.publishable_key == "pk_test_fake_publishable"
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


def test_provider_failure_keeps_the_attempt_pending_with_the_first_retry_due():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    assert store.rejected == set()
    assert store.sessions == {}
    (payment,) = store.payments.values()
    assert payment.estado == "PENDIENTE"
    assert payment.reintentos_realizados == 0
    assert payment.proximo_reintento_en == NOW + timedelta(minutes=1)


def test_a_new_key_performs_the_due_retry_with_its_own_key_and_reuses_the_attempt():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    gateway = FakeGateway()
    result = _service(store, gateway).start_checkout(
        _patient(owner),
        factura_id=invoice.id,
        idempotency_key="checkout-key-0002",
        reintentar_ahora=True,
    )
    assert store.reserve_calls == 1
    assert result.replayed is True
    assert gateway.requests[-1].idempotency_key == f"{KEY}:1"
    (payment,) = store.payments.values()
    assert payment.reintentos_realizados == 1
    assert payment.proximo_reintento_en is None


def test_reopening_a_retried_attempt_returns_its_recorded_session_without_creating_another():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    gateway = FakeGateway()
    first = _service(store, gateway).start_checkout(
        _patient(owner),
        factura_id=invoice.id,
        idempotency_key="checkout-key-0002",
        reintentar_ahora=True,
    )
    again = _service(store, gateway).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key="checkout-key-0003"
    )
    assert again.client_secret == first.client_secret
    assert len(gateway.sessions) == 1


def test_five_failed_retries_exhaust_the_attempt_and_free_the_invoice_for_a_new_one():
    owner = _id()
    store, invoice = _invoiced(owner)
    failing = FakeGateway(fail=True)
    keys_sent = []
    with pytest.raises(ServiceUnavailable):
        _service(store, failing).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    for number in range(1, 6):
        with pytest.raises(ServiceUnavailable):
            _service(store, failing).start_checkout(
                _patient(owner),
                factura_id=invoice.id,
                idempotency_key=f"checkout-key-000{number + 1}",
                reintentar_ahora=True,
            )
        keys_sent.append(failing.requests[-1].idempotency_key)
    assert keys_sent == [f"{KEY}:{n}" for n in range(1, 6)]
    assert len(set(keys_sent)) == 5
    (payment,) = store.payments.values()
    assert payment.estado == "RECHAZADO"
    assert payment.reintentos_agotados is True
    assert payment.reintentos_realizados == 5
    assert payment.proximo_reintento_en is None
    fresh = _service(store, FakeGateway()).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key="checkout-key-0009"
    )
    assert fresh.replayed is False
    assert store.reserve_calls == 2


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


def test_new_key_on_an_invoice_with_a_pending_attempt_reuses_that_checkout():
    owner = _id()
    store, invoice = _invoiced(owner)
    gateway = FakeGateway()
    service = _service(store, gateway)
    first = service.start_checkout(_patient(owner), factura_id=invoice.id, idempotency_key=KEY)
    second = service.start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key="checkout-key-0002"
    )
    assert second.pago_id == first.pago_id
    assert second.client_secret == first.client_secret
    assert second.replayed is True
    assert store.reserve_calls == 1
    # The provider is asked again with the attempt's own key, so Stripe returns the same session.
    assert [r.idempotency_key for r in gateway.requests] == [KEY, KEY]


class _AttemptLandsAfterPrecheck(FakeBillingStore):
    """Another request's pending attempt commits after this request's pre-check."""

    def find_pending_payment_for_invoice(self, factura_id):
        return None


def test_concurrent_new_key_that_loses_the_insert_race_reuses_the_winning_attempt():
    owner = _id()
    base, invoice = _invoiced(owner)
    store = _AttemptLandsAfterPrecheck(
        appointments=base.appointments, invoices=base.invoices, payments=base.payments
    )
    winner = PaymentRecord(
        id=_id(), factura_id=invoice.id, clave="winner-key-0001", estado="PENDIENTE"
    )
    store.payments[winner.id] = winner
    gateway = FakeGateway()
    result = _service(store, gateway).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key="loser-key-0002"
    )
    assert result.pago_id == winner.id
    assert result.replayed is True
    assert store.reserve_calls == 0
    # The winner's own provider key is used, so the loser never opens a second session.
    assert [r.idempotency_key for r in gateway.requests] == ["winner-key-0001"]


def test_routine_reopen_while_a_retry_is_scheduled_never_calls_the_provider():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    gateway = FakeGateway()
    service = _service(store, gateway)
    for number in range(2, 7):
        with pytest.raises(Conflict):
            service.start_checkout(
                _patient(owner),
                factura_id=invoice.id,
                idempotency_key=f"checkout-key-000{number}",
            )
    assert gateway.requests == []
    (payment,) = store.payments.values()
    assert payment.reintentos_realizados == 0
    assert payment.estado == "PENDIENTE"


def test_an_explicit_retry_now_performs_the_retry_and_reuses_the_attempt():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    gateway = FakeGateway()
    result = _service(store, gateway).start_checkout(
        _patient(owner),
        factura_id=invoice.id,
        idempotency_key="checkout-key-0002",
        reintentar_ahora=True,
    )
    assert result.replayed is True
    assert store.reserve_calls == 1
    assert gateway.requests[-1].idempotency_key == f"{KEY}:1"


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


def test_checkout_session_is_created_for_the_patient_stripe_customer():
    owner = _id()
    store, invoice = _invoiced(owner)
    gateway = FakeGateway()
    customers = FakeCustomers()
    _service(store, gateway, customers).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key=KEY
    )
    assert customers.ensured == [owner]
    assert gateway.requests[0].customer_id == f"cus_{owner[:8]}"


def test_checkout_without_a_customer_opens_no_session_and_is_not_rejected():
    owner = _id()
    store, invoice = _invoiced(owner)
    gateway = FakeGateway()
    with pytest.raises(ServiceUnavailable):
        _service(store, gateway, FakeCustomers(failing=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    assert gateway.requests == []
    assert store.sessions == {}
    # Not terminal: a later request resumes the same attempt once the customer exists.
    assert store.rejected == set()


# --- repayment: the server asks Stripe before reusing a pending attempt -------------------


def _pending_with_session(store, factura_id, session_id, clave="old-key-0000001"):
    record = PaymentRecord(
        id=_id(),
        factura_id=factura_id,
        clave=clave,
        estado="PENDIENTE",
        session_id=session_id,
    )
    store.payments[record.id] = record
    return record


def _snapshot(session_id, *, status, payment_status="unpaid", amount_cents=2550, currency="USD"):
    return CheckoutSnapshot(
        id=session_id,
        url="https://checkout.test/old",
        status=status,
        payment_status=payment_status,
        amount_cents=amount_cents,
        currency=currency,
    )


def test_open_attempt_at_stripe_reopens_with_its_own_key():
    owner = _id()
    store, invoice = _invoiced(owner)
    pending = _pending_with_session(store, invoice.id, "cs_open", clave="open-key-0000001")
    gateway = FakeGateway()
    gateway.sessions["cs_open"] = _snapshot("cs_open", status="open")
    result = _service(store, gateway).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key="new-key-0000002"
    )
    assert result.pago_id == pending.id
    assert [r.idempotency_key for r in gateway.requests] == ["open-key-0000001"]
    assert store.cancelled == []
    assert store.confirmed == []


def test_attempt_paid_at_stripe_is_reconciled_without_a_new_charge():
    owner = _id()
    store, invoice = _invoiced(owner)
    pending = _pending_with_session(store, invoice.id, "cs_paid")
    gateway = FakeGateway()
    gateway.sessions["cs_paid"] = _snapshot("cs_paid", status="complete", payment_status="paid")
    with pytest.raises(Conflict):
        _service(store, gateway).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key="new-key-0000003"
        )
    assert store.confirmed == [pending.id]
    assert store.invoices[invoice.id].estado == "PAGADA"
    assert gateway.requests == []
    assert store.reserve_calls == 0


def test_attempt_expired_at_stripe_is_cancelled_and_a_new_attempt_opens():
    owner = _id()
    store, invoice = _invoiced(owner)
    pending = _pending_with_session(store, invoice.id, "cs_old")
    gateway = FakeGateway()
    gateway.sessions["cs_old"] = _snapshot("cs_old", status="expired")
    result = _service(store, gateway).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key="new-key-0000004"
    )
    assert store.cancelled == [pending.id]
    assert result.pago_id != pending.id
    assert result.replayed is False
    assert store.confirmed == []
    assert [r.idempotency_key for r in gateway.requests] == ["new-key-0000004"]


def test_provider_unreachable_while_checking_changes_nothing():
    owner = _id()
    store, invoice = _invoiced(owner)
    _pending_with_session(store, invoice.id, "cs_any")
    gateway = FakeGateway(retrieve_fail=True)
    with pytest.raises(ServiceUnavailable):
        _service(store, gateway).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key="new-key-0000005"
        )
    assert store.cancelled == []
    assert store.confirmed == []
    assert gateway.requests == []
    assert store.reserve_calls == 0


def test_paid_session_with_another_amount_is_not_confirmed():
    owner = _id()
    store, invoice = _invoiced(owner)
    _pending_with_session(store, invoice.id, "cs_wrong")
    gateway = FakeGateway()
    gateway.sessions["cs_wrong"] = _snapshot(
        "cs_wrong", status="complete", payment_status="paid", amount_cents=100
    )
    with pytest.raises(ServiceUnavailable):
        _service(store, gateway).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key="new-key-0000006"
        )
    assert store.confirmed == []
    assert store.invoices[invoice.id].estado == "PENDIENTE"


def test_session_complete_but_not_paid_yet_is_still_processing():
    owner = _id()
    store, invoice = _invoiced(owner)
    _pending_with_session(store, invoice.id, "cs_async")
    gateway = FakeGateway()
    gateway.sessions["cs_async"] = _snapshot("cs_async", status="complete", payment_status="unpaid")
    with pytest.raises(Conflict):
        _service(store, gateway).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key="new-key-0000007"
        )
    assert store.confirmed == []
    assert store.cancelled == []
    assert gateway.requests == []
