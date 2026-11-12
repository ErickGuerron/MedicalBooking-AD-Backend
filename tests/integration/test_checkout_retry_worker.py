"""Scheduled checkout retry against SQL Server: a failed create becomes a session once due."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from sqlalchemy import Engine

from medicalbooking.modules.payments.application.billing_service import BillingService
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from medicalbooking.modules.payments.infrastructure.customer_repositories import (
    SqlStripeCustomerStore,
)
from tests.integration import test_billing_api as billing_api
from tests.integration.test_checkout_retry_api import FlakyGateway
from tests.integration.test_scheduling_api import _count

app = billing_api.app
engine = billing_api.engine

pytestmark = pytest.mark.integration


def _scheduler(engine: Engine, gateway, minutes_after_now: int) -> BillingService:
    """A service whose clock sits `minutes_after_now` in the future, to make a retry due."""
    moment = datetime.now(UTC) + timedelta(minutes=minutes_after_now)
    return BillingService(
        store=SqlBillingStore(engine),
        gateway=gateway,
        clock=lambda: moment,
        customers=CustomerDirectory(SqlStripeCustomerStore(engine), gateway),
    )


@pytest.fixture
def failing_provider(app: Flask, engine: Engine) -> Iterator[FlakyGateway]:
    gateway = FlakyGateway()
    previous = app.extensions.get("medicalbooking.billing")
    app.extensions["medicalbooking.billing"] = billing_api._billing(engine, gateway)
    yield gateway
    if previous is not None:
        app.extensions["medicalbooking.billing"] = previous


def test_a_failed_create_becomes_a_session_when_the_scheduled_retry_runs(
    app, engine, failing_provider
) -> None:
    headers, cita = billing_api._booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]
    key = f"checkout-{uuid.uuid4()}"
    first = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura},
        headers={**headers, "Idempotency-Key": key},
    )
    assert first.status_code == 503
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM pagos WHERE factura_id = :f AND estado = 'PENDIENTE' "
            "AND stripe_checkout_session_id IS NULL AND proximo_reintento_en IS NOT NULL",
            f=factura,
        )
        == 1
    )

    healthy = FlakyGateway()
    healthy.failing = False
    run = _scheduler(engine, healthy, minutes_after_now=2).retry_due_checkouts(limit=500)

    assert run.attempted >= 1
    assert f"{key}:1" in [r.idempotency_key for r in healthy.requests]
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM pagos WHERE factura_id = :f AND estado = 'PENDIENTE' "
            "AND stripe_checkout_session_id IS NOT NULL AND reintentos_realizados = 1 "
            "AND proximo_reintento_en IS NULL",
            f=factura,
        )
        == 1
    )
