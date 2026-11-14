"""Refund end to end over HTTP: request, Stripe refund.updated (signed), final state.

The patient requests a refund for a paid, cancelled appointment. Stripe is a double
(httpx.MockTransport). Stripe later confirms the refund through the real webhook endpoint,
signed with the webhook secret. The reembolso becomes PROCESADO and the invoice REEMBOLSADA.
A replay of the same signed event changes nothing.
"""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_refund_request_api import (
    ProviderDouble,
    _install,
    _patient_headers,
    _refunds_for,
    _request,
    _seed,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
WEBHOOK_SECRET = "whsec_synthetic_refund_e2e"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(
        update={
            "jwt_signing_key": SecretStr(KEY),
            "stripe_webhook_secret": SecretStr(WEBHOOK_SECRET),
        }
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _signed(event: dict) -> tuple[bytes, str]:
    body = json.dumps(event).encode()
    timestamp = int(time.time())
    digest = hmac.new(
        WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return body, f"t={timestamp},v1={digest}"


def _states(engine: Engine, pago_id: str) -> tuple[str, str, str]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT r.estado, f.estado FROM reembolsos AS r "
                "JOIN pagos AS p ON p.id = r.pago_id "
                "JOIN facturas AS f ON f.id = p.factura_id WHERE p.id = :p"
            ),
            {"p": pago_id},
        ).one()
        pago = conn.execute(
            text("SELECT estado FROM pagos WHERE id = :p"), {"p": pago_id}
        ).scalar_one()
    return row[0], pago, row[1]


def test_a_refund_goes_from_request_to_processed_and_the_invoice_is_refunded(app, engine):
    seed = _seed(engine)
    double = ProviderDouble(refund_status="pending")
    _install(app, engine, double)
    headers = _patient_headers(app, engine, seed)

    created = _request(app.test_client(), headers, seed.pago_id, f"refund-{uuid.uuid4()}")
    assert created.status_code == 201, created.get_json()
    [(_reembolso, estado, stripe_refund_id)] = _refunds_for(engine, seed.pago_id)
    assert estado == "PENDIENTE"

    event = {
        "id": f"evt_{uuid.uuid4().hex[:16]}",
        "type": "refund.updated",
        "data": {"object": {"id": stripe_refund_id, "status": "succeeded"}},
    }
    body, signature = _signed(event)
    client = app.test_client()
    first = client.post(
        "/api/v1/payments/webhook",
        data=body,
        headers={"Stripe-Signature": signature, "Content-Type": "application/json"},
    )
    assert first.status_code == 200, first.get_json()

    reembolso, pago, factura = _states(engine, seed.pago_id)
    assert reembolso == "PROCESADO"
    assert pago == "CONFIRMADO"  # the payment itself stays confirmed: it was paid
    assert factura == "REEMBOLSADA"

    replay = client.post(
        "/api/v1/payments/webhook",
        data=body,
        headers={"Stripe-Signature": signature, "Content-Type": "application/json"},
    )
    assert replay.status_code == 200
    assert _states(engine, seed.pago_id) == (reembolso, pago, factura)
