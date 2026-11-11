"""Stripe adapter for checkout and saved payment methods (E4 billing). Calls the Stripe HTTP API
with httpx.

The secret key is sent only as a bearer token and never logged. Every provider failure is
reduced to ProviderUnavailable (one request helper, one mapping), so no provider body or secret
reaches the client.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from medicalbooking.modules.payments.application.billing_service import (
    CheckoutRequest,
    CheckoutSession,
    CheckoutSnapshot,
    ProviderUnavailable,
)
from medicalbooking.modules.payments.application.payment_methods import (
    CardSnapshot,
    PaymentMethodSnapshot,
)

STRIPE_API_BASE = "https://api.stripe.com"
CHECKOUT_PATH = "/v1/checkout/sessions"
CUSTOMERS_PATH = "/v1/customers"
SETUP_INTENTS_PATH = "/v1/setup_intents"
PAYMENT_METHODS_PATH = "/v1/payment_methods"
CARDS_PAGE_LIMIT = "100"
_NOT_AN_OBJECT = "Stripe answered a body that is not an object"
_NO_CHECKOUT_SESSION = "Stripe answered without a checkout session"


class StripeCheckoutGateway:
    """Stripe calls for checkout and saved cards. The publishable key is public by design: the
    browser needs it to mount the session or the setup intent with its client secret."""

    def __init__(
        self,
        secret_key: str | None,
        return_url: str,
        publishable_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._secret_key = secret_key
        self._return_url = return_url
        self.publishable_key = publishable_key
        self._client = client or httpx.Client(base_url=STRIPE_API_BASE, timeout=10.0)

    def is_configured(self) -> bool:
        return bool(self._secret_key and self.publishable_key)

    def create_session(self, request: CheckoutRequest) -> CheckoutSession:
        form = {
            "mode": "payment",
            "ui_mode": "embedded_page",
            "customer": request.customer_id,
            # Lets the embedded form offer to save the card for later off-session payments.
            "payment_intent_data[setup_future_usage]": "off_session",
            "return_url": self._return_url,
            "client_reference_id": request.reference,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": request.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(request.amount_cents),
            "line_items[0][price_data][product_data][name]": request.description,
        }
        body = self._send(
            "POST",
            CHECKOUT_PATH,
            data=form,
            idempotency_key=request.idempotency_key,
            shape_error=_NO_CHECKOUT_SESSION,
        )
        session_id = body.get("id")
        client_secret = body.get("client_secret")
        if not isinstance(session_id, str) or not isinstance(client_secret, str):
            raise ProviderUnavailable(_NO_CHECKOUT_SESSION)
        return CheckoutSession(id=session_id, client_secret=client_secret)

    def retrieve_session(self, session_id: str) -> CheckoutSnapshot:
        """Read a session's state with a GET. Nothing is created, charged or changed."""
        body = self._send(
            "GET",
            f"{CHECKOUT_PATH}/{quote(session_id, safe='')}",
            shape_error=_NO_CHECKOUT_SESSION,
        )
        if not isinstance(body.get("id"), str):
            raise ProviderUnavailable(_NO_CHECKOUT_SESSION)
        amount = body.get("amount_total")
        currency = body.get("currency")
        url = body.get("url")
        return CheckoutSnapshot(
            id=body["id"],
            url=url if isinstance(url, str) else None,
            status=str(body.get("status", "")),
            payment_status=str(body.get("payment_status", "")),
            amount_cents=amount if isinstance(amount, int) else None,
            currency=currency.upper() if isinstance(currency, str) else None,
        )

    def create_customer(self, idempotency_key: str) -> str:
        """Create the Stripe customer of one user. The key makes concurrent first requests
        return the same customer."""
        body = self._send("POST", CUSTOMERS_PATH, idempotency_key=idempotency_key)
        customer_id = body.get("id")
        if not isinstance(customer_id, str):
            raise ProviderUnavailable("Stripe answered without a customer")
        return customer_id

    def create_setup_intent(self, customer_id: str) -> str:
        """Create a SetupIntent to save a card without paying. Returns its client secret."""
        body = self._send(
            "POST",
            SETUP_INTENTS_PATH,
            data={
                "customer": customer_id,
                "usage": "off_session",
                "automatic_payment_methods[enabled]": "true",
            },
        )
        client_secret = body.get("client_secret")
        if not isinstance(client_secret, str):
            raise ProviderUnavailable("Stripe answered without a setup intent")
        return client_secret

    def list_cards(self, customer_id: str) -> list[CardSnapshot]:
        body = self._send(
            "GET",
            f"{CUSTOMERS_PATH}/{quote(customer_id, safe='')}/payment_methods",
            params={"type": "card", "limit": CARDS_PAGE_LIMIT},
        )
        items = body.get("data")
        if not isinstance(items, list):
            raise ProviderUnavailable("Stripe answered without a payment method list")
        cards: list[CardSnapshot] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            card = item.get("card")
            if not isinstance(card, dict):
                continue
            cards.append(
                CardSnapshot(
                    id=item["id"],
                    brand=str(card.get("brand", "")),
                    last4=str(card.get("last4", "")),
                    exp_month=_int_or_none(card.get("exp_month")),
                    exp_year=_int_or_none(card.get("exp_year")),
                )
            )
        return cards

    def default_payment_method(self, customer_id: str) -> str | None:
        body = self._send("GET", f"{CUSTOMERS_PATH}/{quote(customer_id, safe='')}")
        if body.get("deleted") is True:
            return None
        settings = body.get("invoice_settings")
        if not isinstance(settings, dict):
            return None
        value = settings.get("default_payment_method")
        return value if isinstance(value, str) else None

    def retrieve_payment_method(self, payment_method_id: str) -> PaymentMethodSnapshot | None:
        """Read a PaymentMethod. None when Stripe does not know it."""
        body = self._perform(
            "GET",
            f"{PAYMENT_METHODS_PATH}/{quote(payment_method_id, safe='')}",
            missing_ok=True,
        )
        if body is None:
            return None
        if not isinstance(body.get("id"), str):
            raise ProviderUnavailable("Stripe answered without a payment method")
        customer = body.get("customer")
        return PaymentMethodSnapshot(
            id=body["id"],
            customer_id=customer if isinstance(customer, str) else None,
        )

    def detach_payment_method(self, payment_method_id: str) -> bool:
        """Detach a card from its customer. False when Stripe does not know the card."""
        body = self._perform(
            "POST",
            f"{PAYMENT_METHODS_PATH}/{quote(payment_method_id, safe='')}/detach",
            missing_ok=True,
        )
        return body is not None

    def _send(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        shape_error: str = _NOT_AN_OBJECT,
    ) -> dict[str, Any]:
        body = self._perform(
            method,
            path,
            data=data,
            params=params,
            idempotency_key=idempotency_key,
            shape_error=shape_error,
        )
        if body is None:
            raise ProviderUnavailable("Stripe answered without a body")
        return body

    def _perform(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        missing_ok: bool = False,
        shape_error: str = _NOT_AN_OBJECT,
    ) -> dict[str, Any] | None:
        """Send one request and return its JSON object. With missing_ok, a 404 returns None
        (the object does not exist). Every other failure becomes ProviderUnavailable without
        the provider body."""
        if not self._secret_key:
            raise ProviderUnavailable("Stripe secret key is not configured")
        headers = {"Authorization": f"Bearer {self._secret_key}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = self._client.request(method, path, data=data, params=params, headers=headers)
        except httpx.HTTPError as error:
            raise ProviderUnavailable("Stripe is unreachable") from error
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ProviderUnavailable(f"Stripe answered {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderUnavailable("Stripe answered a body that is not JSON") from error
        if not isinstance(body, dict):
            raise ProviderUnavailable(shape_error)
        return body


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None
