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
        sleep=lambda _seconds: None,
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


def test_retrieve_exposes_the_payment_intent_id_when_stripe_reports_one():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "cs_test_pi",
                "status": "complete",
                "payment_status": "paid",
                "payment_intent": "pi_test_synthetic_0001",
            },
        )

    snapshot = _gateway(handler).retrieve_session("cs_test_pi")

    assert snapshot.payment_intent == "pi_test_synthetic_0001"


def test_payment_intent_is_none_when_absent_or_not_a_plain_id():
    # An expanded object or a null field is not an id we can store, so it reads as None.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "cs_test_pi", "payment_intent": {"id": "pi_x"}})

    assert _gateway(handler).retrieve_session("cs_test_pi").payment_intent is None

    def absent(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "cs_test_pi"})

    assert _gateway(absent).retrieve_session("cs_test_pi").payment_intent is None


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


# --- bounded automatic retries (shared request helper) ----------------------------------------


def _retrying_gateway(handler, sleeps: list[float]) -> StripeCheckoutGateway:
    client = httpx.Client(
        base_url="https://api.stripe.test", transport=httpx.MockTransport(handler)
    )
    return StripeCheckoutGateway(
        secret_key=SECRET,
        return_url=RETURN_URL,
        publishable_key=PUBLISHABLE,
        client=client,
        sleep=sleeps.append,
    )


def _script(*steps):
    """Serve the steps in order. A step is a response or an exception to raise."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        step = steps[len(calls) - 1]
        if isinstance(step, Exception):
            raise step
        return step

    return handler, calls


def _status(code: int) -> httpx.Response:
    return httpx.Response(code, json={"error": {"message": f"boom {SECRET}"}})


def _session_ok() -> httpx.Response:
    return httpx.Response(
        200, json={"id": "cs_test_retry", "client_secret": "cs_test_retry_secret"}
    )


def _session_get_ok() -> httpx.Response:
    return httpx.Response(200, json={"id": "cs_test_retry", "status": "open"})


def test_a_transient_503_on_a_read_is_retried_and_the_second_attempt_succeeds():
    sleeps: list[float] = []
    handler, calls = _script(_status(503), _session_get_ok())

    snapshot = _retrying_gateway(handler, sleeps).retrieve_session("cs_test_retry")

    assert snapshot.id == "cs_test_retry"
    assert len(calls) == 2
    assert sleeps == [0.5]


def test_a_transient_503_on_a_keyed_post_is_not_retried():
    # Stripe stores the first answer of a keyed POST, so a repeat would replay the 503.
    sleeps: list[float] = []
    handler, calls = _script(_status(503), _session_ok())

    with pytest.raises(ProviderUnavailable):
        _retrying_gateway(handler, sleeps).create_session(REQUEST)

    assert len(calls) == 1
    assert sleeps == []


def test_three_503_responses_on_a_read_raise_unavailable_after_exactly_three_attempts():
    sleeps: list[float] = []
    handler, calls = _script(_status(503), _status(503), _status(503))

    with pytest.raises(ProviderUnavailable) as error:
        _retrying_gateway(handler, sleeps).retrieve_session("cs_test_retry")

    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]
    assert str(error.value) == "Stripe answered 503"
    assert SECRET not in str(error.value)


def test_a_client_error_is_never_retried():
    sleeps: list[float] = []
    handler, calls = _script(_status(400), _session_ok())

    with pytest.raises(ProviderUnavailable):
        _retrying_gateway(handler, sleeps).create_session(REQUEST)

    assert len(calls) == 1
    assert sleeps == []


def test_detach_is_not_retried_on_503():
    sleeps: list[float] = []
    handler, calls = _script(_status(503), httpx.Response(200, json={"id": "pm_x"}))

    with pytest.raises(ProviderUnavailable):
        _retrying_gateway(handler, sleeps).detach_payment_method("pm_test_card_0003")

    assert len(calls) == 1
    assert sleeps == []


def test_a_network_error_is_retried():
    sleeps: list[float] = []
    handler, calls = _script(httpx.ConnectError("no route"), _session_ok())

    session = _retrying_gateway(handler, sleeps).create_session(REQUEST)

    assert session.id == "cs_test_retry"
    assert len(calls) == 2


def test_a_timeout_is_retried_like_any_network_error():
    sleeps: list[float] = []
    handler, calls = _script(httpx.ReadTimeout("slow"), _session_ok())

    _retrying_gateway(handler, sleeps).create_session(REQUEST)

    assert len(calls) == 2


def test_a_429_is_retried_for_a_get():
    sleeps: list[float] = []
    handler, calls = _script(_status(429), _session_get_ok())

    snapshot = _retrying_gateway(handler, sleeps).retrieve_session("cs_test_retry")

    assert snapshot.status == "open"
    assert len(calls) == 2


def test_a_409_is_retried_only_when_the_request_carries_an_idempotency_key():
    sleeps: list[float] = []
    handler, calls = _script(_status(409), _session_ok())
    session = _retrying_gateway(handler, sleeps).create_session(REQUEST)
    assert session.id == "cs_test_retry"
    assert len(calls) == 2

    sleeps_get: list[float] = []
    handler_get, calls_get = _script(_status(409), _session_get_ok())
    with pytest.raises(ProviderUnavailable):
        _retrying_gateway(handler_get, sleeps_get).retrieve_session("cs_test_retry")
    assert len(calls_get) == 1
    assert sleeps_get == []


def test_a_malformed_success_body_is_never_retried():
    sleeps: list[float] = []
    handler, calls = _script(httpx.Response(200, text="<html>"), _session_ok())

    with pytest.raises(ProviderUnavailable):
        _retrying_gateway(handler, sleeps).create_session(REQUEST)

    assert len(calls) == 1


def test_every_attempt_of_a_keyed_request_sends_the_same_idempotency_key():
    sleeps: list[float] = []
    handler, calls = _script(_status(409), _session_ok())

    _retrying_gateway(handler, sleeps).create_session(REQUEST)

    assert [call.headers["Idempotency-Key"] for call in calls] == [
        REQUEST.idempotency_key,
        REQUEST.idempotency_key,
    ]


def test_a_successful_checkout_is_sent_exactly_once():
    sleeps: list[float] = []
    handler, calls = _script(_session_ok())

    _retrying_gateway(handler, sleeps).create_session(REQUEST)

    assert len(calls) == 1
    assert sleeps == []


def test_a_missing_object_is_answered_once_and_never_retried():
    sleeps: list[float] = []
    handler, calls = _script(httpx.Response(404, json={"error": {"code": "resource_missing"}}))

    snapshot = _retrying_gateway(handler, sleeps).retrieve_payment_method("pm_gone_0001")

    assert snapshot is None
    assert len(calls) == 1
    assert sleeps == []
