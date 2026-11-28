"""Settling a checkout against Stripe when the webhook never arrived.

"Verificar pago" used to read only the stored attempt, so a payment Stripe had already charged
stayed pending. These tests pin the explicit reconcile step and the replay with the same key,
which must ask Stripe before it hands back a checkout.
"""

from __future__ import annotations

import pytest

from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ServiceUnavailable
from tests.unit.test_billing_service import (
    FakeGateway,
    _id,
    _invoiced,
    _patient,
    _pending_with_session,
    _service,
    _snapshot,
)


def test_reconcile_confirms_an_attempt_stripe_reports_as_paid():
    owner = _id()
    store, invoice = _invoiced(owner)
    pending = _pending_with_session(store, invoice.id, "cs_paid")
    gateway = FakeGateway()
    gateway.sessions["cs_paid"] = _snapshot("cs_paid", status="complete", payment_status="paid")

    status = _service(store, gateway).reconcile_checkout(_patient(owner), factura_id=invoice.id)

    assert store.confirmed == [pending.id]
    assert store.invoices[invoice.id].estado == "PAGADA"
    assert gateway.requests == []
    assert status.estado == "CONFIRMADO"


def test_reconcile_of_an_open_session_changes_nothing_and_opens_nothing():
    owner = _id()
    store, invoice = _invoiced(owner)
    _pending_with_session(store, invoice.id, "cs_open")
    gateway = FakeGateway()
    gateway.sessions["cs_open"] = _snapshot("cs_open", status="open")

    status = _service(store, gateway).reconcile_checkout(_patient(owner), factura_id=invoice.id)

    assert store.confirmed == []
    assert store.cancelled == []
    assert gateway.requests == []
    assert status.estado == "PENDIENTE"


def test_reconcile_still_processing_is_not_confirmed():
    owner = _id()
    store, invoice = _invoiced(owner)
    _pending_with_session(store, invoice.id, "cs_async")
    gateway = FakeGateway()
    gateway.sessions["cs_async"] = _snapshot("cs_async", status="complete", payment_status="unpaid")

    status = _service(store, gateway).reconcile_checkout(_patient(owner), factura_id=invoice.id)

    assert store.confirmed == []
    assert status.estado == "PENDIENTE"


def test_reconcile_cancels_an_attempt_stripe_expired():
    owner = _id()
    store, invoice = _invoiced(owner)
    pending = _pending_with_session(store, invoice.id, "cs_old")
    gateway = FakeGateway()
    gateway.sessions["cs_old"] = _snapshot("cs_old", status="expired")

    _service(store, gateway).reconcile_checkout(_patient(owner), factura_id=invoice.id)

    assert store.cancelled == [pending.id]
    assert store.confirmed == []


def test_reconcile_with_a_paid_session_of_another_amount_is_refused():
    owner = _id()
    store, invoice = _invoiced(owner)
    _pending_with_session(store, invoice.id, "cs_wrong")
    gateway = FakeGateway()
    gateway.sessions["cs_wrong"] = _snapshot(
        "cs_wrong", status="complete", payment_status="paid", amount_cents=100
    )

    with pytest.raises(ServiceUnavailable):
        _service(store, gateway).reconcile_checkout(_patient(owner), factura_id=invoice.id)

    assert store.confirmed == []
    assert store.invoices[invoice.id].estado == "PENDIENTE"


def test_reconcile_while_the_provider_is_down_changes_nothing():
    owner = _id()
    store, invoice = _invoiced(owner)
    _pending_with_session(store, invoice.id, "cs_any")

    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(retrieve_fail=True)).reconcile_checkout(
            _patient(owner), factura_id=invoice.id
        )

    assert store.confirmed == []
    assert store.cancelled == []


def test_reconcile_is_owner_only_and_needs_an_attempt():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(Forbidden):
        _service(store, FakeGateway()).reconcile_checkout(_patient(_id()), factura_id=invoice.id)
    with pytest.raises(NotFound):
        _service(store, FakeGateway()).reconcile_checkout(_patient(owner), factura_id=invoice.id)


def test_same_key_after_payment_confirms_instead_of_handing_back_the_paid_session():
    # Regression: Flutter reuses the key of the first attempt. The replay used to reopen the session
    # with that key without asking Stripe, so a paid checkout came back to the patient.
    owner = _id()
    store, invoice = _invoiced(owner)
    pending = _pending_with_session(store, invoice.id, "cs_paid", clave="same-key-0000001")
    gateway = FakeGateway()
    gateway.sessions["cs_paid"] = _snapshot("cs_paid", status="complete", payment_status="paid")

    with pytest.raises(Conflict, match="already paid"):
        _service(store, gateway).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key="same-key-0000001"
        )

    assert store.confirmed == [pending.id]
    assert store.invoices[invoice.id].estado == "PAGADA"
    assert gateway.requests == []


def test_same_key_with_an_open_session_still_reopens_its_own_session():
    owner = _id()
    store, invoice = _invoiced(owner)
    pending = _pending_with_session(store, invoice.id, "cs_open", clave="same-key-0000002")
    gateway = FakeGateway()
    gateway.sessions["cs_open"] = _snapshot("cs_open", status="open")

    result = _service(store, gateway).start_checkout(
        _patient(owner), factura_id=invoice.id, idempotency_key="same-key-0000002"
    )

    assert result.pago_id == pending.id
    assert result.replayed is True
    assert [r.idempotency_key for r in gateway.requests] == ["same-key-0000002"]
    assert store.confirmed == []
