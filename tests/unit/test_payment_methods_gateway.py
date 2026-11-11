"""Stripe gateway methods for saved payment methods, against a mocked HTTP transport.

Request shape (method, path, parameters, Idempotency-Key), response mapping and failure mapping.
No test reaches the network, and no provider body or secret reaches an error message.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs

import httpx
import pytest

from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.modules.payments.infrastructure.stripe_checkout import StripeCheckoutGateway

SECRET = "sk_test_synthetic_secret"
PUBLISHABLE = "pk_test_synthetic_publishable"
RETURN_URL = "https://front.test/pago/retorno?session_id={CHECKOUT_SESSION_ID}"
CUSTOMER = "cus_test_patient_0002"
PAYMENT_METHOD = "pm_test_card_0002"


def _gateway(handler, secret: str | None = SECRET) -> StripeCheckoutGateway:
    client = httpx.Client(
        base_url="https://api.stripe.test", transport=httpx.MockTransport(handler)
    )
    return StripeCheckoutGateway(
        secret_key=secret,
        return_url=RETURN_URL,
        publishable_key=PUBLISHABLE,
        client=client,
    )


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode(), keep_blank_values=True)


def _card(card_id: str, brand: str, last4: str, month: int, year: int) -> dict:
    return {
        "id": card_id,
        "object": "payment_method",
        "type": "card",
        "card": {"brand": brand, "last4": last4, "exp_month": month, "exp_year": year},
    }


# --- customer ----------------------------------------------------------------


def test_customer_is_created_with_the_per_user_idempotency_key():
    seen = {}
    usuario = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["idem"] = request.headers["Idempotency-Key"]
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"id": CUSTOMER, "object": "customer"})

    assert _gateway(handler).create_customer(f"customer-{usuario}") == CUSTOMER
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/customers"
    assert seen["idem"] == f"customer-{usuario}"
    assert seen["auth"] == f"Bearer {SECRET}"


def test_customer_without_an_id_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "customer"})

    with pytest.raises(ProviderUnavailable):
        _gateway(handler).create_customer("customer-x")


# --- setup intent --------------------------------------------------------------


def test_setup_intent_is_off_session_for_the_customer_and_returns_its_client_secret():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["form"] = _form(request)
        return httpx.Response(
            200, json={"id": "seti_test_1", "object": "setup_intent", "client_secret": "seti_s"}
        )

    assert _gateway(handler).create_setup_intent(CUSTOMER) == "seti_s"
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/setup_intents"
    assert seen["form"]["customer"] == [CUSTOMER]
    assert seen["form"]["usage"] == ["off_session"]
    assert seen["form"]["automatic_payment_methods[enabled]"] == ["true"]


def test_setup_intent_without_client_secret_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "seti_test_2"})

    with pytest.raises(ProviderUnavailable):
        _gateway(handler).create_setup_intent(CUSTOMER)


# --- listing cards -------------------------------------------------------------


def test_cards_are_listed_as_card_type_for_the_customer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["type"] = request.url.params.get("type")
        return httpx.Response(
            200,
            json={
                "object": "list",
                "has_more": False,
                "data": [_card("pm_a", "visa", "4242", 12, 2031)],
            },
        )

    cards = _gateway(handler).list_cards(CUSTOMER)

    assert seen["method"] == "GET"
    assert seen["path"] == f"/v1/customers/{CUSTOMER}/payment_methods"
    assert seen["type"] == "card"
    assert len(cards) == 1
    assert cards[0].id == "pm_a"
    assert cards[0].brand == "visa"
    assert cards[0].last4 == "4242"
    assert cards[0].exp_month == 12
    assert cards[0].exp_year == 2031


def test_a_list_entry_without_card_details_is_skipped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"id": "pm_bare", "type": "card"}, _card("pm_b", "amex", "0005", 1, 2030)]
            },
        )

    cards = _gateway(handler).list_cards(CUSTOMER)
    assert [card.id for card in cards] == ["pm_b"]


def test_default_payment_method_is_read_from_the_customer_invoice_settings():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "id": CUSTOMER,
                "object": "customer",
                "invoice_settings": {"default_payment_method": PAYMENT_METHOD},
            },
        )

    assert _gateway(handler).default_payment_method(CUSTOMER) == PAYMENT_METHOD
    assert seen["method"] == "GET"
    assert seen["path"] == f"/v1/customers/{CUSTOMER}"


def test_no_default_payment_method_is_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": CUSTOMER, "invoice_settings": {}})

    assert _gateway(handler).default_payment_method(CUSTOMER) is None


def test_listing_failure_becomes_unavailable_without_leaking_the_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": f"oops {SECRET}"}})

    with pytest.raises(ProviderUnavailable) as error:
        _gateway(handler).list_cards(CUSTOMER)
    assert SECRET not in str(error.value)


# --- retrieve and detach -------------------------------------------------------


def test_retrieve_returns_the_customer_the_card_belongs_to():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(
            200, json={"id": PAYMENT_METHOD, "object": "payment_method", "customer": CUSTOMER}
        )

    found = _gateway(handler).retrieve_payment_method(PAYMENT_METHOD)

    assert seen["method"] == "GET"
    assert seen["path"] == f"/v1/payment_methods/{PAYMENT_METHOD}"
    assert found is not None
    assert found.id == PAYMENT_METHOD
    assert found.customer_id == CUSTOMER


def test_retrieve_of_a_detached_card_has_no_customer():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": PAYMENT_METHOD, "customer": None})

    found = _gateway(handler).retrieve_payment_method(PAYMENT_METHOD)
    assert found is not None
    assert found.customer_id is None


def test_retrieve_of_an_unknown_card_is_none_not_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "resource_missing"}})

    assert _gateway(handler).retrieve_payment_method("pm_unknown_0001") is None


def test_detach_posts_to_the_detach_endpoint_and_reports_success():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": PAYMENT_METHOD, "customer": None})

    assert _gateway(handler).detach_payment_method(PAYMENT_METHOD) is True
    assert seen["method"] == "POST"
    assert seen["path"] == f"/v1/payment_methods/{PAYMENT_METHOD}/detach"


def test_detach_of_an_unknown_card_reports_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "resource_missing"}})

    assert _gateway(handler).detach_payment_method("pm_unknown_0001") is False


def test_detach_provider_error_becomes_unavailable_without_leaking_the_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"message": f"declined {SECRET}"}})

    with pytest.raises(ProviderUnavailable) as error:
        _gateway(handler).detach_payment_method(PAYMENT_METHOD)
    assert SECRET not in str(error.value)


def test_no_method_reaches_the_provider_without_a_secret_key():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    gateway = _gateway(handler, secret=None)
    with pytest.raises(ProviderUnavailable):
        gateway.create_customer("customer-x")
    with pytest.raises(ProviderUnavailable):
        gateway.create_setup_intent(CUSTOMER)
    with pytest.raises(ProviderUnavailable):
        gateway.list_cards(CUSTOMER)
    with pytest.raises(ProviderUnavailable):
        gateway.retrieve_payment_method(PAYMENT_METHOD)
    with pytest.raises(ProviderUnavailable):
        gateway.detach_payment_method(PAYMENT_METHOD)
    assert calls == []
