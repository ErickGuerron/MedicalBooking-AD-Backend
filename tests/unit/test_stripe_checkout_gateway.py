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
PUBLISHABLE = "pk_test_synthetic_publishable"
RETURN_URL = "https://front.test/pago/retorno?session_id={CHECKOUT_SESSION_ID}"
REQUEST = CheckoutRequest(
    idempotency_key="checkout-key-0001",
    amount_cents=2550,
    currency="USD",
    description="Factura MedicalBooking",
    reference="00000000-0000-0000-0000-000000000001",
    customer_id="cus_test_patient_0001",
)


def _gateway(
    handler,
    secret: str | None = SECRET,
    publishable: str | None = PUBLISHABLE,
) -> StripeCheckoutGateway:
    client = httpx.Client(
        base_url="https://api.stripe.test", transport=httpx.MockTransport(handler)
    )
    return StripeCheckoutGateway(
        secret_key=secret,
        return_url=RETURN_URL,
        publishable_key=publishable,
        client=client,
    )


def test_embedded_session_is_created_with_return_url_and_returns_its_client_secret():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        seen["idem"] = request.headers["Idempotency-Key"]
        seen["body"] = request.content.decode()
        return httpx.Response(
            200, json={"id": "cs_test_1", "url": None, "client_secret": "cs_test_1_secret_x"}
        )

    session = _gateway(handler).create_session(REQUEST)

    assert session.id == "cs_test_1"
    assert session.client_secret == "cs_test_1_secret_x"
    assert seen["path"] == CHECKOUT_PATH
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["idem"] == REQUEST.idempotency_key
    assert "line_items%5B0%5D%5Bprice_data%5D%5Bunit_amount%5D=2550" in seen["body"]
    assert "line_items%5B0%5D%5Bprice_data%5D%5Bcurrency%5D=usd" in seen["body"]
    assert "mode=payment" in seen["body"]
    assert "customer=cus_test_patient_0001" in seen["body"]
    assert "payment_intent_data%5Bsetup_future_usage%5D=off_session" in seen["body"]
    assert "ui_mode=embedded_page" in seen["body"]
    assert "return_url=" in seen["body"]
    assert "%7BCHECKOUT_SESSION_ID%7D" in seen["body"]
    assert "success_url" not in seen["body"]
    assert "cancel_url" not in seen["body"]


def test_a_session_without_client_secret_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "cs_test_2", "url": None})

    with pytest.raises(ProviderUnavailable):
        _gateway(handler).create_session(REQUEST)


def test_the_publishable_key_is_exposed_only_when_configured():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    assert _gateway(handler).publishable_key == PUBLISHABLE
    assert _gateway(handler, publishable=None).is_configured() is False
    assert _gateway(handler).is_configured() is True


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


def test_a_non_json_success_body_is_unavailable_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(ProviderUnavailable):
        _gateway(handler).create_session(REQUEST)


def test_retrieve_is_a_read_only_get_for_the_session_and_maps_its_state():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={
                "id": "cs_test_9",
                "url": "https://pay.test/cs_9",
                "status": "complete",
                "payment_status": "paid",
                "amount_total": 2550,
                "currency": "usd",
            },
        )

    snapshot = _gateway(handler).retrieve_session("cs_test_9")

    assert seen["method"] == "GET"
    assert seen["path"] == f"{CHECKOUT_PATH}/cs_test_9"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert snapshot.status == "complete"
    assert snapshot.payment_status == "paid"
    assert snapshot.amount_cents == 2550
    assert snapshot.currency == "USD"


def test_retrieve_failure_becomes_unavailable_without_leaking_the_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": f"oops {SECRET}"}})

    with pytest.raises(ProviderUnavailable) as error:
        _gateway(handler).retrieve_session("cs_test_9")
    assert SECRET not in str(error.value)


def test_retrieve_without_secret_never_calls_the_provider():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    with pytest.raises(ProviderUnavailable):
        _gateway(handler, secret=None).retrieve_session("cs_test_9")
    assert calls == []


def test_a_non_object_checkout_answer_keeps_its_original_failure_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2])

    gateway = _gateway(handler)
    with pytest.raises(ProviderUnavailable) as create_error:
        gateway.create_session(REQUEST)
    with pytest.raises(ProviderUnavailable) as retrieve_error:
        gateway.retrieve_session("cs_test_9")
    assert str(create_error.value) == "Stripe answered without a checkout session"
    assert str(retrieve_error.value) == "Stripe answered without a checkout session"
