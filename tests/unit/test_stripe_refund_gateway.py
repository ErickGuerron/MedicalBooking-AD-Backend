"""Stripe refund call against a mocked transport: request shape, idempotency, failure mapping."""

from __future__ import annotations

import httpx
import pytest

from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.modules.payments.infrastructure.stripe_checkout import (
    REFUNDS_PATH,
    StripeCheckoutGateway,
)

SECRET = "sk_test_synthetic_secret"
RETURN_URL = "https://front.test/pago/retorno?session_id={CHECKOUT_SESSION_ID}"
PAYMENT_INTENT = "pi_test_synthetic_0001"


def _gateway(handler) -> StripeCheckoutGateway:
    client = httpx.Client(
        base_url="https://api.stripe.test", transport=httpx.MockTransport(handler)
    )
    return StripeCheckoutGateway(
        secret_key=SECRET,
        return_url=RETURN_URL,
        publishable_key="pk_test_synthetic_publishable",
        client=client,
        sleep=lambda _seconds: None,
    )


def test_a_refund_posts_the_full_amount_with_the_idempotency_key_and_returns_its_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["key"] = request.headers.get("Idempotency-Key")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "re_test_0001", "status": "pending"})

    refund = _gateway(handler).create_refund(
        payment_intent_id=PAYMENT_INTENT,
        amount_cents=2550,
        idempotency_key="refund-00000000-0000-0000-0000-000000000001",
    )

    assert seen["path"] == REFUNDS_PATH
    assert seen["method"] == "POST"
    assert seen["key"] == "refund-00000000-0000-0000-0000-000000000001"
    assert "payment_intent=pi_test_synthetic_0001" in seen["body"]
    assert "amount=2550" in seen["body"]
    assert refund.id == "re_test_0001"
    assert refund.status == "pending"


def test_a_refund_without_a_payment_intent_is_refused_before_any_call():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    with pytest.raises(ProviderUnavailable):
        _gateway(handler).create_refund(
            payment_intent_id="", amount_cents=2550, idempotency_key="k"
        )
    assert calls == []


def test_a_provider_error_becomes_provider_unavailable_without_the_provider_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "secret detail sk_live_x"}})

    with pytest.raises(ProviderUnavailable) as raised:
        _gateway(handler).create_refund(
            payment_intent_id=PAYMENT_INTENT, amount_cents=2550, idempotency_key="k"
        )
    assert "sk_live" not in str(raised.value)
