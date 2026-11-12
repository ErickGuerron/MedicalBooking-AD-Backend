"""Scheduled checkout retries (checkout-retry T3): the service retries only what is due."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from medicalbooking.modules.payments.application.billing_service import BillingService
from medicalbooking.shared.domain.errors import ServiceUnavailable
from tests.unit.test_billing_service import (
    KEY,
    NOW,
    FakeCustomers,
    FakeGateway,
    _id,
    _invoiced,
    _patient,
    _service,
)


def _later(minutes: int, store, gateway, customers=None) -> BillingService:
    return BillingService(
        store=store,
        gateway=gateway,
        clock=lambda: NOW + timedelta(minutes=minutes),
        customers=customers or FakeCustomers(),
    )


def _failed_first_attempt(owner: str):
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    return store, invoice


def test_a_due_retry_is_performed_with_its_own_key_and_recorded():
    owner = _id()
    store, invoice = _failed_first_attempt(owner)
    gateway = FakeGateway()

    run = _later(1, store, gateway).retry_due_checkouts(limit=10)

    assert (run.attempted, run.succeeded, run.failed) == (1, 1, 0)
    assert gateway.requests[-1].idempotency_key == f"{KEY}:1"
    (payment,) = store.payments.values()
    assert payment.session_id is not None
    assert payment.reintentos_realizados == 1
    assert payment.proximo_reintento_en is None


def test_a_retry_that_is_not_due_yet_is_left_alone():
    owner = _id()
    store, _ = _failed_first_attempt(owner)
    gateway = FakeGateway()

    run = _later(0, store, gateway).retry_due_checkouts(limit=10)

    assert (run.attempted, run.succeeded, run.failed) == (0, 0, 0)
    assert gateway.requests == []


def test_a_failed_scheduled_retry_moves_the_next_one_by_the_next_gap():
    owner = _id()
    store, _ = _failed_first_attempt(owner)

    run = _later(1, store, FakeGateway(fail=True)).retry_due_checkouts(limit=10)

    assert (run.attempted, run.succeeded, run.failed) == (1, 0, 1)
    (payment,) = store.payments.values()
    assert payment.estado == "PENDIENTE"
    assert payment.reintentos_realizados == 1
    assert payment.proximo_reintento_en == NOW + timedelta(minutes=2)


def test_a_customer_failure_during_a_scheduled_retry_counts_as_a_failed_retry():
    owner = _id()
    store, _ = _failed_first_attempt(owner)
    customers = FakeCustomers(failing=True)

    run = _later(1, store, FakeGateway(), customers).retry_due_checkouts(limit=10)

    assert (run.attempted, run.succeeded, run.failed) == (1, 0, 1)
    (payment,) = store.payments.values()
    assert payment.reintentos_realizados == 1
    assert payment.proximo_reintento_en == NOW + timedelta(minutes=2)


def test_a_retry_for_an_invoice_no_longer_pending_cancels_the_attempt_and_calls_nothing():
    owner = _id()
    store, invoice = _failed_first_attempt(owner)
    store.invoices[invoice.id] = replace(store.invoices[invoice.id], estado="PAGADA")
    gateway = FakeGateway()
    (payment,) = store.payments.values()

    run = _later(1, store, gateway).retry_due_checkouts(limit=10)

    assert run.succeeded == 0
    assert store.cancelled == [payment.id]
    assert gateway.requests == []
