"""Shared setup for checkout retry unit tests: a clock-controlled service and a failed attempt."""

from __future__ import annotations

from datetime import timedelta

import pytest

from medicalbooking.modules.payments.application.billing_service import BillingService
from medicalbooking.shared.domain.errors import ServiceUnavailable
from tests.unit.test_billing_service import (
    KEY,
    NOW,
    FakeBillingStore,
    FakeCustomers,
    FakeGateway,
    _invoiced,
    _patient,
    _service,
)


def service_at(minutes: float, store, gateway, customers=None) -> BillingService:
    """A service whose clock reads NOW plus `minutes`."""
    return BillingService(
        store=store,
        gateway=gateway,
        clock=lambda: NOW + timedelta(minutes=minutes),
        customers=customers or FakeCustomers(),
    )


def failed_first_attempt(owner: str) -> tuple[FakeBillingStore, object]:
    """An invoice whose first checkout failed at NOW, so a retry is scheduled one minute later."""
    store, invoice = _invoiced(owner)
    with pytest.raises(ServiceUnavailable):
        _service(store, FakeGateway(fail=True)).start_checkout(
            _patient(owner), factura_id=invoice.id, idempotency_key=KEY
        )
    return store, invoice
