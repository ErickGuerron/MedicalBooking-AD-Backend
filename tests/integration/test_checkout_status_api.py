"""Checkout status over HTTP (checkout-retry S4): what the app shows while a retry is scheduled."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from sqlalchemy import Engine

from tests.integration import test_billing_api as billing_api
from tests.integration.test_checkout_retry_api import FlakyGateway

app = billing_api.app
engine = billing_api.engine

pytestmark = pytest.mark.integration


@pytest.fixture
def failing_provider(app: Flask, engine: Engine) -> Iterator[FlakyGateway]:
    gateway = FlakyGateway()
    previous = app.extensions.get("medicalbooking.billing")
    app.extensions["medicalbooking.billing"] = billing_api._billing(engine, gateway)
    yield gateway
    if previous is not None:
        app.extensions["medicalbooking.billing"] = previous


def _invoice(app: Flask, engine: Engine, headers, client) -> str:
    _, cita = billing_api._booked_appointment(app, engine)
    return client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()["id"]


def test_status_tells_the_app_the_failure_and_the_scheduled_retry(
    app, engine, failing_provider
) -> None:
    headers, cita = billing_api._booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]
    failed = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura},
        headers={**headers, "Idempotency-Key": f"checkout-{uuid.uuid4()}"},
    )
    assert failed.status_code == 503

    status = client.get(f"/api/v1/invoices/{factura}/checkout-status", headers=headers)

    assert status.status_code == 200, status.get_json()
    body = status.get_json()
    assert body["estado"] == "PENDIENTE"
    assert body["reintentos_realizados"] == 0
    assert body["reintentos_maximos"] == 5
    assert body["reintentos_agotados"] is False
    assert body["ultimo_error_codigo"] == "PROVEEDOR_NO_DISPONIBLE"
    assert body["proximo_reintento_en"] is not None


def test_another_patient_gets_the_same_forbidden_as_for_a_missing_invoice(
    app, engine, failing_provider
) -> None:
    headers, cita = billing_api._booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]
    intruder = billing_api._token(app, billing_api._user(engine, "PACIENTE"))

    foreign = client.get(f"/api/v1/invoices/{factura}/checkout-status", headers=intruder)
    missing = client.get(f"/api/v1/invoices/{uuid.uuid4()}/checkout-status", headers=intruder)

    assert foreign.status_code == missing.status_code == 403
    assert foreign.get_json()["detail"] == missing.get_json()["detail"]


def test_an_invoice_without_any_attempt_answers_not_found(app, engine, failing_provider) -> None:
    headers, cita = billing_api._booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]

    status = client.get(f"/api/v1/invoices/{factura}/checkout-status", headers=headers)

    assert status.status_code == 404
