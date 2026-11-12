"""Manual 'Reintentar ahora' has a one-minute cooldown after the last failure (checkout-retry)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from medicalbooking.modules.payments.application.billing_service import BillingService
from medicalbooking.shared.domain.errors import Conflict, ServiceUnavailable
from tests.unit.test_billing_service import (
    KEY,
    NOW,
    FakeCustomers,
    FakeGateway,
    _id,
    _invoiced,
    _patient,
)


def _at(minutes: float, store, gateway) -> BillingService:
    return BillingService(
        store=store,
        gateway=gateway,
        clock=lambda: NOW + timedelta(minutes=minutes),
        customers=FakeCustomers(),
    )


def _failed_at_now(owner: str):
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _at(0, store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    return store, invoice


def test_a_manual_retry_right_after_a_failure_is_refused_and_calls_nothing():
    owner = _id()
    store, invoice = _failed_at_now(owner)
    gateway = FakeGateway()

    with pytest.raises(Conflict):
        _at(0, store, gateway).start_checkout(
            _patient(owner),
            factura_id=invoice.id,
            idempotency_key="checkout-key-0002",
            reintentar_ahora=True,
        )
    assert gateway.requests == []


def test_a_manual_retry_is_allowed_one_minute_after_the_failure():
    owner = _id()
    store, invoice = _failed_at_now(owner)
    gateway = FakeGateway()

    result = _at(1, store, gateway).start_checkout(
        _patient(owner),
        factura_id=invoice.id,
        idempotency_key="checkout-key-0002",
        reintentar_ahora=True,
    )

    assert result.replayed is True
    assert gateway.requests[-1].idempotency_key == f"{KEY}:1"


def test_a_burst_of_manual_clicks_spends_one_retry_not_five():
    owner = _id()
    store, invoice = _failed_at_now(owner)
    gateway = FakeGateway(fail=True)
    with pytest.raises(ServiceUnavailable):
        _at(1, store, gateway).start_checkout(
            _patient(owner),
            factura_id=invoice.id,
            idempotency_key="checkout-key-0001",
            reintentar_ahora=True,
        )
    for number in range(2, 7):
        with pytest.raises((Conflict, ServiceUnavailable)):
            _at(1, store, gateway).start_checkout(
                _patient(owner),
                factura_id=invoice.id,
                idempotency_key=f"checkout-key-000{number}",
                reintentar_ahora=True,
            )
    (payment,) = store.payments.values()
    assert payment.reintentos_realizados == 1
    assert len(gateway.requests) == 1
