"""Stripe Checkout gateway against a mocked HTTP transport: request shape and failure mapping."""

from __future__ import annotations

import httpx
import pytest

from medicalbooking.modules.payments.application.billing_service import (
    CheckoutRequest,
    ProviderUnavailable,
)
from medicalbooking.modules.payments.infrastructure.stripe_checkout import (
    CHECKOUT_PATH,
    StripeCheckoutGateway,
)

SECRET = "sk_test_synthetic_secret"
REQUEST = CheckoutRequest(
    idempotency_key="checkout-key-0001",
    amount_cents=2550,
    currency="USD",
    description="Factura MedicalBooking",
    reference="00000000-0000-0000-0000-000000000001",
)


def _gateway(handler, secret: str | None = SECRET) -> StripeCheckoutGateway:
    client = httpx.Client(
        base_url="https://api.stripe.test", transport=httpx.MockTransport(handler)
    )
    return StripeCheckoutGateway(
        secret_key=secret,
        success_url="https://front.test/pago/exito",
        cancel_url="https://front.test/pago/cancelado",
        client=client,
    )


def test_session_is_created_with_amount_currency_and_provider_idempotency_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        seen["idem"] = request.headers["Idempotency-Key"]
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "cs_test_1", "url": "https://pay.test/cs_1"})

    session = _gateway(handler).create_session(REQUEST)

    assert session.id == "cs_test_1"
    assert session.url == "https://pay.test/cs_1"
    assert seen["path"] == CHECKOUT_PATH
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["idem"] == REQUEST.idempotency_key
    assert "line_items%5B0%5D%5Bprice_data%5D%5Bunit_amount%5D=2550" in seen["body"]
    assert "line_items%5B0%5D%5Bprice_data%5D%5Bcurrency%5D=usd" in seen["body"]
    assert "mode=payment" in seen["body"]


def test_provider_error_becomes_unavailable_without_leaking_the_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"bad key {SECRET}"}})

    with pytest.raises(ProviderUnavailable) as error:
        _gateway(handler).create_session(REQUEST)
    assert SECRET not in str(error.value)


def test_network_failure_becomes_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderUnavailable):
        _gateway(handler).create_session(REQUEST)


def test_unconfigured_gateway_is_not_configured_and_never_calls_the_provider():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    gateway = _gateway(handler, secret=None)
    assert gateway.is_configured() is False
    with pytest.raises(ProviderUnavailable):
        gateway.create_session(REQUEST)
    assert calls == []
