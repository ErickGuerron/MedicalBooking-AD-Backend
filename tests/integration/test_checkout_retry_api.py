"""Checkout retry through HTTP: a routine reopen never spends a retry, reintentar_ahora does."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from sqlalchemy import Engine, text

from tests.integration import test_billing_api as billing_api
from tests.integration.checkout_support import (
    FlakyGateway,
    failing_checkout_provider,
    invoiced_appointment,
)
from tests.integration.test_scheduling_api import _count

app = billing_api.app
engine = billing_api.engine

pytestmark = pytest.mark.integration


@pytest.fixture
def flaky(app: Flask, engine: Engine) -> Iterator[FlakyGateway]:
    with failing_checkout_provider(app, engine) as gateway:
        yield gateway


def _checkout(client, headers, factura: str, key: str, **extra):
    return client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura, **extra},
        headers={**headers, "Idempotency-Key": key},
    )


def test_routine_reopen_during_a_scheduled_retry_is_refused_without_calling_the_provider(
    app, engine, flaky
) -> None:
    headers, client, factura = invoiced_appointment(app, engine)
    first = _checkout(client, headers, factura, f"checkout-{uuid.uuid4()}")
    assert first.status_code == 503
    creates_after_failure = len(flaky.requests)

    flaky.failing = False
    refused = _checkout(client, headers, factura, f"checkout-{uuid.uuid4()}")

    assert refused.status_code == 409
    assert len(flaky.requests) == creates_after_failure
    assert (
        _count(
            engine,
            "SELECT reintentos_realizados FROM pagos WHERE factura_id = :f",
            f=factura,
        )
        == 0
    )


def test_reintentar_ahora_runs_the_retry_with_its_own_key(app, engine, flaky) -> None:
    headers, client, factura = invoiced_appointment(app, engine)
    key = f"checkout-{uuid.uuid4()}"
    assert _checkout(client, headers, factura, key).status_code == 503

    flaky.failing = False
    too_soon = _checkout(
        client, headers, factura, f"checkout-{uuid.uuid4()}", reintentar_ahora=True
    )
    assert too_soon.status_code == 409
    assert flaky.requests[-1].idempotency_key == key

    # Simulate a minute passing: the last failure moves one minute back.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE pagos SET proximo_reintento_en = DATEADD(minute, -1, proximo_reintento_en) "
                "WHERE factura_id = :f"
            ),
            {"f": factura},
        )
    retried = _checkout(client, headers, factura, f"checkout-{uuid.uuid4()}", reintentar_ahora=True)

    assert retried.status_code == 200, retried.get_json()
    assert flaky.requests[-1].idempotency_key == f"{key}:1"
    assert (
        _count(engine, "SELECT reintentos_realizados FROM pagos WHERE factura_id = :f", f=factura)
        == 1
    )
