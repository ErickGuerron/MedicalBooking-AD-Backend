"""Payments reach the replica as one snapshot per change: pago.actualizado (kafka-replication S10).

Every state change of a payment writes the full snapshot of the payment and of its invoice in
the same SQL Server transaction, whatever path produced it (checkout, verified webhook,
rejection). The consumer only upserts. Gateway identifiers and idempotency keys never leave SQL
Server.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from tests.integration.test_billing_api import (
    StubGateway,
    _billing,
    _booked_appointment,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _latest_payment_snapshot(engine: Engine, pago_id: str) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT payload FROM eventos_outbox "
                "WHERE agregado_tipo = 'pago' AND agregado_id = :p "
                "AND tipo_evento = 'pago.actualizado' "
                "ORDER BY version_agregado"
            ),
            {"p": pago_id},
        ).all()
    assert rows, "no pago.actualizado event for this payment"
    return json.loads(rows[-1].payload)


def _checkout(app: Flask, engine: Engine) -> tuple[dict, str, str]:
    headers, cita = _booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]
    app.extensions["medicalbooking.billing"] = _billing(engine, StubGateway())
    response = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura},
        headers={**headers, "Idempotency-Key": f"checkout-{uuid.uuid4()}"},
    )
    assert response.status_code == 201, response.get_json()
    return headers, factura, response.get_json()["pago_id"]


def test_a_checkout_emits_the_pending_payment_and_its_invoice(app, engine) -> None:
    _headers, factura, pago = _checkout(app, engine)

    snapshot = _latest_payment_snapshot(engine, pago)

    assert snapshot["pago"]["id"] == pago.lower()
    assert snapshot["pago"]["factura_id"] == factura.lower()
    assert snapshot["pago"]["estado"] == "PENDIENTE"
    assert Decimal(snapshot["pago"]["monto"]) == Decimal("25.00")
    assert snapshot["factura"]["estado"] == "PENDIENTE"
    assert snapshot["factura"]["paciente_id"]


def test_a_confirmed_payment_marks_its_invoice_paid_in_the_snapshot(app, engine) -> None:
    _headers, factura, pago = _checkout(app, engine)

    SqlBillingStore(engine).confirm_payment(pago, factura, datetime.now(UTC))

    snapshot = _latest_payment_snapshot(engine, pago)
    assert snapshot["pago"]["estado"] == "CONFIRMADO"
    assert snapshot["pago"]["pagado_en"] is not None
    assert snapshot["factura"]["estado"] == "PAGADA"


def test_a_rejected_payment_is_projected_as_rejected(app, engine) -> None:
    _headers, _factura, pago = _checkout(app, engine)

    SqlBillingStore(engine).mark_rejected(pago)

    assert _latest_payment_snapshot(engine, pago)["pago"]["estado"] == "RECHAZADO"


def test_no_gateway_identifier_or_idempotency_key_is_projected(app, engine) -> None:
    _headers, _factura, pago = _checkout(app, engine)
    SqlBillingStore(engine).confirm_payment(pago, _latest_factura(engine, pago), datetime.now(UTC))

    serialized = json.dumps(_latest_payment_snapshot(engine, pago)).lower()

    assert "stripe" not in serialized
    assert "clave_idempotencia" not in serialized
    assert "cs_test_" not in serialized


def _latest_factura(engine: Engine, pago_id: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT factura_id FROM pagos WHERE id = :p"), {"p": pago_id}
            ).scalar_one()
        )
