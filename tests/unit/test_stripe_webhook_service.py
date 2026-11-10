"""Stripe webhook use case against an in-memory store (E4)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from medicalbooking.modules.payments.application.webhook_service import (
    InvalidPayload,
    StripeWebhookService,
    WebhookNotConfigured,
)
from medicalbooking.modules.payments.domain.webhook import (
    PROCESADO,
    InvalidSignature,
    PaymentChange,
)

SECRET = "whsec_test_synthetic_secret"
NOW = 1_800_000_000


class FakeStore:
    def __init__(self, outcome: str = PROCESADO) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str, PaymentChange | None]] = []

    def process(self, event_id: str, event_type: str, change: PaymentChange) -> str:
        self.calls.append((event_id, event_type, change))
        return self.outcome


def _signed(body: dict, secret: str = SECRET) -> tuple[bytes, str]:
    payload = json.dumps(body).encode()
    signed = f"{NOW}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return payload, f"t={NOW},v1={digest}"


def _event() -> dict:
    return {
        "id": "evt_test_1",
        "type": "checkout.session.async_payment_succeeded",
        "data": {"object": {"id": "cs_test_1", "payment_status": "paid"}},
    }


def test_valid_event_is_passed_to_the_store_once() -> None:
    store = FakeStore()
    service = StripeWebhookService(store=store, secret=SECRET, clock=lambda: NOW)
    payload, header = _signed(_event())

    outcome = service.handle(payload, header)

    assert outcome == PROCESADO
    assert len(store.calls) == 1
    event_id, event_type, change = store.calls[0]
    assert event_id == "evt_test_1"
    assert event_type == "checkout.session.async_payment_succeeded"
    assert change is not None and change.session_id == "cs_test_1"


def test_invalid_signature_never_reaches_the_store() -> None:
    store = FakeStore()
    service = StripeWebhookService(store=store, secret=SECRET, clock=lambda: NOW)
    payload, header = _signed(_event(), secret="whsec_forged")

    with pytest.raises(InvalidSignature):
        service.handle(payload, header)
    assert store.calls == []


def test_signed_but_non_json_body_is_rejected() -> None:
    store = FakeStore()
    service = StripeWebhookService(store=store, secret=SECRET, clock=lambda: NOW)
    payload = b"not json"
    signed = f"{NOW}.".encode() + payload
    digest = hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()

    with pytest.raises(InvalidPayload):
        service.handle(payload, f"t={NOW},v1={digest}")
    assert store.calls == []


def test_missing_secret_means_not_configured() -> None:
    service = StripeWebhookService(store=FakeStore(), secret=None, clock=lambda: NOW)
    payload, header = _signed(_event())

    with pytest.raises(WebhookNotConfigured):
        service.handle(payload, header)
