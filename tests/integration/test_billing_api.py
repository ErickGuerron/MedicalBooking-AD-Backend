"""Billing checks against real SQL Server: invoices, ownership, one invoice per appointment and
checkout idempotency with a stubbed Stripe gateway (no network calls).

Each test builds its own patients, appointment and service with unique identifiers.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.payments.application.billing_service import (
    BillingService,
    CheckoutRequest,
    CheckoutSession,
)
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from medicalbooking.modules.payments.infrastructure.customer_repositories import (
    SqlStripeCustomerStore,
)
from tests.integration.test_scheduling_api import (
    _book,
    _count,
    _service,
    _slot,
    _token,
    _user,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


class StubGateway:
    """Stands in for Stripe: configured, returns a deterministic session, records requests."""

    publishable_key = "pk_test_stub_publishable"

    def __init__(self) -> None:
        self.requests: list[CheckoutRequest] = []

    def is_configured(self) -> bool:
        return True

    def create_session(self, request: CheckoutRequest) -> CheckoutSession:
        self.requests.append(request)
        session_id = f"cs_test_{uuid.uuid4().hex[:12]}"
        return CheckoutSession(id=session_id, client_secret=f"{session_id}_secret")

    def create_customer(self, idempotency_key: str) -> str:
        return f"cus_test_{uuid.uuid4().hex[:12]}"


def _billing(engine: Engine, gateway: StubGateway) -> BillingService:
    """Billing with the real SQL customer mapping and the stub provider."""
    return BillingService(
        store=SqlBillingStore(engine),
        gateway=gateway,
        clock=lambda: datetime.now(UTC),
        customers=CustomerDirectory(SqlStripeCustomerStore(engine), gateway),
    )


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    # Explicit: a developer's .env may hold a Stripe test key. These tests never call Stripe;
    # the gateway is replaced by StubGateway where a session is needed.
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _booked_appointment(app: Flask, engine: Engine) -> tuple[dict[str, str], str]:
    patient = _user(engine, "PACIENTE")
    headers = _token(app, patient)
    servicio = _service(engine)
    slot = _slot(engine, days_ahead=4)
    response = _book(
        app.test_client(), headers, key=f"billing-{uuid.uuid4()}", slot=slot, servicio=servicio
    )
    assert response.status_code == 201, response.get_json()
    return headers, response.get_json()["id"]


def test_invoice_for_a_booked_appointment_has_one_detail_line(app, engine) -> None:
    headers, cita = _booked_appointment(app, engine)
    response = app.test_client().post("/api/v1/invoices", json={"cita_id": cita}, headers=headers)

    assert response.status_code == 201, response.get_json()
    factura = response.get_json()["id"]
    assert response.get_json()["total"] == "25.00"
    assert _count(engine, "SELECT COUNT(*) FROM facturas WHERE id = :f", f=factura) == 1
    assert (
        _count(engine, "SELECT COUNT(*) FROM facturas_detalle WHERE factura_id = :f", f=factura)
        == 1
    )


def test_second_invoice_for_the_same_appointment_conflicts(app, engine) -> None:
    headers, cita = _booked_appointment(app, engine)
    client = app.test_client()
    assert (
        client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).status_code == 201
    )
    again = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers)
    assert again.status_code == 409


def test_foreign_patient_cannot_invoice_and_gets_the_same_answer_as_missing(app, engine) -> None:
    _, cita = _booked_appointment(app, engine)
    intruder = _token(app, _user(engine, "PACIENTE"))
    client = app.test_client()
    foreign = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=intruder)
    missing = client.post("/api/v1/invoices", json={"cita_id": str(uuid.uuid4())}, headers=intruder)
    assert foreign.status_code == missing.status_code == 403
    assert foreign.get_json()["detail"] == missing.get_json()["detail"]


def test_checkout_without_provider_key_answers_503_and_creates_nothing(app, engine) -> None:
    headers, cita = _booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]

    response = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura},
        headers={**headers, "Idempotency-Key": f"checkout-{uuid.uuid4()}"},
    )

    assert response.status_code == 503
    assert _count(engine, "SELECT COUNT(*) FROM pagos WHERE factura_id = :f", f=factura) == 0


def test_checkout_is_idempotent_per_key_and_stores_the_session(app, engine) -> None:
    headers, cita = _booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]

    gateway = StubGateway()
    app.extensions["medicalbooking.billing"] = _billing(engine, gateway)
    key = f"checkout-{uuid.uuid4()}"
    first = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura},
        headers={**headers, "Idempotency-Key": key},
    )
    second = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura},
        headers={**headers, "Idempotency-Key": key},
    )

    assert first.status_code == 201, first.get_json()
    assert second.status_code == 200
    assert second.get_json()["pago_id"] == first.get_json()["pago_id"]
    assert _count(engine, "SELECT COUNT(*) FROM pagos WHERE clave_idempotencia = :k", k=key) == 1
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM pagos WHERE id = :p AND stripe_checkout_session_id IS NOT NULL",
            p=first.get_json()["pago_id"],
        )
        == 1
    )
    assert len(gateway.requests) == 2
    assert gateway.requests[0].amount_cents == 2500


def test_key_reused_for_another_invoice_conflicts(app, engine) -> None:
    headers, cita = _booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]
    app.extensions["medicalbooking.billing"] = _billing(engine, StubGateway())
    key = f"checkout-{uuid.uuid4()}"
    client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura},
        headers={**headers, "Idempotency-Key": key},
    )
    # A different patient owns the second appointment, so use that patient's token.
    other_headers, other_cita = _booked_appointment(app, engine)
    other = client.post(
        "/api/v1/invoices", json={"cita_id": other_cita}, headers=other_headers
    ).get_json()
    response = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": other["id"]},
        headers={**other_headers, "Idempotency-Key": key},
    )
    assert response.status_code == 409
