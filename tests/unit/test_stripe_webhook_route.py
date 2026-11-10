"""HTTP contract of POST /api/v1/payments/webhook (E4) with an in-memory service."""

from __future__ import annotations

import hashlib
import hmac
import json

from flask import Flask, jsonify

from medicalbooking.modules.payments.application.webhook_service import StripeWebhookService
from medicalbooking.modules.payments.presentation.routes import payments_bp
from medicalbooking.shared.domain.errors import AppError

SECRET = "whsec_test_synthetic_secret"
NOW = 1_800_000_000
PATH = "/api/v1/payments/webhook"


class RecordingStore:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, event_id, event_type, change):  # noqa: ANN001, ANN201
        self.calls += 1
        return "IGNORADO"


def _app(service: StripeWebhookService | None) -> Flask:
    app = Flask("test-payments")
    app.register_blueprint(payments_bp)
    app.extensions["medicalbooking.payments_webhook"] = service

    @app.errorhandler(AppError)
    def _domain_error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status, "title": error.title}), error.status

    return app


def _signed(body: dict, secret: str = SECRET) -> tuple[bytes, str]:
    payload = json.dumps(body).encode()
    signed = f"{NOW}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return payload, f"t={NOW},v1={digest}"


def _event() -> dict:
    return {"id": "evt_test_1", "type": "charge.refunded", "data": {"object": {"id": "ch_1"}}}


def test_valid_signed_event_is_acknowledged_without_jwt() -> None:
    store = RecordingStore()
    app = _app(StripeWebhookService(store=store, secret=SECRET, clock=lambda: NOW))
    payload, header = _signed(_event())

    response = app.test_client().post(
        PATH, data=payload, headers={"Stripe-Signature": header, "Content-Type": "application/json"}
    )

    assert response.status_code == 200
    assert response.get_json()["resultado"] == "IGNORADO"
    assert store.calls == 1


def test_bad_signature_is_400_and_stores_nothing() -> None:
    store = RecordingStore()
    app = _app(StripeWebhookService(store=store, secret=SECRET, clock=lambda: NOW))
    payload, header = _signed(_event(), secret="whsec_forged")

    response = app.test_client().post(PATH, data=payload, headers={"Stripe-Signature": header})

    assert response.status_code == 400
    assert store.calls == 0


def test_missing_signature_header_is_400() -> None:
    app = _app(StripeWebhookService(store=RecordingStore(), secret=SECRET, clock=lambda: NOW))
    payload, _ = _signed(_event())

    response = app.test_client().post(PATH, data=payload)

    assert response.status_code == 400


def test_unconfigured_secret_fails_closed_with_503() -> None:
    app = _app(None)
    payload, header = _signed(_event())

    response = app.test_client().post(PATH, data=payload, headers={"Stripe-Signature": header})

    assert response.status_code == 503


def test_non_ascii_signature_header_is_400_not_500() -> None:
    store = RecordingStore()
    app = _app(StripeWebhookService(store=store, secret=SECRET, clock=lambda: NOW))
    payload, _ = _signed(_event())

    response = app.test_client().post(
        PATH, data=payload, headers={"Stripe-Signature": f"t={NOW},v1=\u00e9"}
    )

    assert response.status_code == 400
    assert store.calls == 0
