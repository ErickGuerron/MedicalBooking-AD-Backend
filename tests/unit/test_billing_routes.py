"""HTTP contract of the billing endpoints with an in-memory service: auth, status codes, headers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from flask import Flask, jsonify

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.billing_service import (
    BillingService,
    CheckoutRequest,
    CheckoutSession,
)
from medicalbooking.modules.payments.presentation.routes import BILLING_EXTENSION, payments_bp
from medicalbooking.shared.domain.errors import AppError
from tests.unit.test_billing_service import FakeBillingStore, _appointment, _id

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
KEY = "checkout-key-0001"


class OkGateway:
    def is_configured(self) -> bool:
        return True

    def create_session(self, request: CheckoutRequest) -> CheckoutSession:
        return CheckoutSession(id="cs_route_1", url="https://pay.test/route")


class _Tokens:
    """Maps a bearer token to an access profile for the test client."""

    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self._profiles = profiles

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self._profiles:
            raise AppError("bad token")
        return self._profiles[token]


def _app(store: FakeBillingStore, profiles: dict[str, AccessProfile]) -> Flask:
    app = Flask("test-billing")
    app.register_blueprint(payments_bp)
    app.extensions[BILLING_EXTENSION] = BillingService(
        store=store, gateway=OkGateway(), clock=lambda: NOW
    )
    app.extensions["medicalbooking.identity"] = _Tokens(profiles)

    @app.errorhandler(AppError)
    def _domain_error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status, "title": error.title}), error.status

    return app


@pytest.fixture
def patient() -> tuple[str, dict[str, AccessProfile]]:
    user_id = _id()
    profile = AccessProfile(
        user_id=user_id,
        status="ACTIVO",
        roles=frozenset({"PACIENTE"}),
        permissions=frozenset({"facturas.crear", "pagos.iniciar"}),
    )
    return user_id, {"tok-patient": profile}


def test_invoice_requires_a_bearer_token(patient) -> None:
    user_id, profiles = patient
    client = _app(FakeBillingStore(), profiles).test_client()
    response = client.post("/api/v1/invoices", json={"cita_id": _id()})
    assert response.status_code == 401


def test_invoice_is_created_for_the_owner_with_201(patient) -> None:
    user_id, profiles = patient
    store = FakeBillingStore()
    appointment = _appointment(user_id)
    store.appointments[appointment.cita_id] = appointment
    client = _app(store, profiles).test_client()

    response = client.post(
        "/api/v1/invoices",
        json={"cita_id": appointment.cita_id},
        headers={"Authorization": "Bearer tok-patient"},
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["estado"] == "PENDIENTE"
    assert body["total"] == str(Decimal("25.50"))


def test_invoice_rejects_unknown_fields(patient) -> None:
    _, profiles = patient
    client = _app(FakeBillingStore(), profiles).test_client()
    response = client.post(
        "/api/v1/invoices",
        json={"cita_id": _id(), "total": "0.01"},
        headers={"Authorization": "Bearer tok-patient"},
    )
    assert response.status_code == 422


def test_checkout_without_idempotency_key_is_422(patient) -> None:
    user_id, profiles = patient
    client = _app(FakeBillingStore(), profiles).test_client()
    response = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": _id()},
        headers={"Authorization": "Bearer tok-patient"},
    )
    assert response.status_code == 422


def test_checkout_answers_201_then_200_on_replay(patient) -> None:
    user_id, profiles = patient
    store = FakeBillingStore()
    appointment = _appointment(user_id)
    store.appointments[appointment.cita_id] = appointment
    app = _app(store, profiles)
    client = app.test_client()
    headers = {"Authorization": "Bearer tok-patient", "Idempotency-Key": KEY}

    invoice = client.post(
        "/api/v1/invoices",
        json={"cita_id": appointment.cita_id},
        headers={"Authorization": "Bearer tok-patient"},
    ).get_json()

    first = client.post(
        "/api/v1/payments/checkout", json={"factura_id": invoice["id"]}, headers=headers
    )
    second = client.post(
        "/api/v1/payments/checkout", json={"factura_id": invoice["id"]}, headers=headers
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.get_json()["checkout_url"] == "https://pay.test/route"
    assert second.get_json()["pago_id"] == first.get_json()["pago_id"]
