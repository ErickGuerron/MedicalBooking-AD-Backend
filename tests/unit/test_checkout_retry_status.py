"""Checkout status for the app (checkout-retry S4, S7): attempts made, last error, next retry."""

from __future__ import annotations

from datetime import timedelta

import pytest

from medicalbooking.shared.domain.errors import Forbidden, NotFound
from tests.unit.checkout_retry_support import failed_first_attempt as _failed_first_attempt
from tests.unit.test_billing_service import (
    NOW,
    FakeGateway,
    _id,
    _invoiced,
    _patient,
    _service,
)


def test_status_shows_the_scheduled_retry_and_the_last_error():
    owner = _id()
    store, invoice = _failed_first_attempt(owner)

    status = _service(store, FakeGateway()).checkout_status(_patient(owner), factura_id=invoice.id)

    assert status.estado == "PENDIENTE"
    assert status.reintentos_realizados == 0
    assert status.reintentos_maximos == 5
    assert status.reintentos_agotados is False
    assert status.ultimo_error_codigo == "PROVEEDOR_NO_DISPONIBLE"
    assert status.proximo_reintento_en == NOW + timedelta(minutes=1)


def test_status_of_an_exhausted_attempt_says_so():
    owner = _id()
    store, invoice = _failed_first_attempt(owner)
    (payment,) = store.payments.values()
    store.record_create_failure(payment.id, 5, None, "PROVEEDOR_NO_DISPONIBLE")

    status = _service(store, FakeGateway()).checkout_status(_patient(owner), factura_id=invoice.id)

    assert status.estado == "RECHAZADO"
    assert status.reintentos_agotados is True
    assert status.proximo_reintento_en is None


def test_foreign_and_missing_invoices_get_the_same_forbidden():
    owner = _id()
    store, invoice = _failed_first_attempt(owner)
    service = _service(store, FakeGateway())
    with pytest.raises(Forbidden):
        service.checkout_status(_patient(_id()), factura_id=invoice.id)
    with pytest.raises(Forbidden):
        service.checkout_status(_patient(owner), factura_id=_id())


def test_an_invoice_without_any_attempt_is_not_found():
    owner = _id()
    store, invoice = _invoiced(owner)
    with pytest.raises(NotFound):
        _service(store, FakeGateway()).checkout_status(_patient(owner), factura_id=invoice.id)
