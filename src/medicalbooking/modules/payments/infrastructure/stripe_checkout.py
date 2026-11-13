"""Stripe adapter for checkout and saved payment methods (E4 billing). Calls the Stripe HTTP API
with httpx.

The secret key is sent only as a bearer token and never logged. Every provider failure is
reduced to ProviderUnavailable (one request helper, one mapping), so no provider body or secret
reaches the client.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
    CardPage,
    CardSnapshot,
    PaymentMethodSnapshot,
)
from medicalbooking.modules.payments.application.refund_service import ProviderRejected
from medicalbooking.modules.payments.domain.payment_methods import DEFAULT_PAGE_LIMIT

STRIPE_API_BASE = "https://api.stripe.com"
CHECKOUT_PATH = "/v1/checkout/sessions"
CUSTOMERS_PATH = "/v1/customers"
REFUNDS_PATH = "/v1/refunds"


@dataclass(frozen=True)
class StripeRefund:
    id: str
    status: str
    metadata: dict[str, str] = field(default_factory=dict)


SETUP_INTENTS_PATH = "/v1/setup_intents"
PAYMENT_METHODS_PATH = "/v1/payment_methods"

# Every provider request goes through _perform, so this policy covers every Stripe call.
# Retries are limited to requests that are safe to repeat: GET, or POST with an Idempotency-Key.
MAX_PROVIDER_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5
_NOT_AN_OBJECT = "Stripe answered a body that is not an object"
_NO_CHECKOUT_SESSION = "Stripe answered without a checkout session"
_NO_REFUND = "Stripe answered without a refund"
_NO_REFUND_LIST = "Stripe answered without a refund list"


class StripeCheckoutGateway:
    """Stripe calls for checkout and saved cards. The publishable key is public by design: the
    browser needs it to mount the session or the setup intent with its client secret."""

    def __init__(
        self,
        secret_key: str | None,
        return_url: str,
        publishable_key: str | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._secret_key = secret_key
        self._return_url = return_url
        self.publishable_key = publishable_key
        self._client = client or httpx.Client(base_url=STRIPE_API_BASE, timeout=10.0)
        self._sleep = sleep

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
        intent = body.get("payment_intent")
        return CheckoutSnapshot(
            id=body["id"],
            url=url if isinstance(url, str) else None,
            status=str(body.get("status", "")),
            payment_status=str(body.get("payment_status", "")),
            amount_cents=amount if isinstance(amount, int) else None,
            currency=currency.upper() if isinstance(currency, str) else None,
            # Only a plain id string is kept: an expanded object or null reads as None.
            payment_intent=intent if isinstance(intent, str) and intent else None,
        )

    def create_refund(
        self,
        payment_intent_id: str,
        amount_cents: int,
        idempotency_key: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StripeRefund:
        """Refund a payment intent in full. The key is the id of the local refund request.
        The POST is sent once: a transport failure or 5xx has an unknown outcome at Stripe, and
        the caller decides (no automatic retry). Without a payment intent nothing is sent.
        The metadata lets a later reconciliation find this refund by the local reembolso id."""
        if not payment_intent_id:
            raise ProviderUnavailable("refund refused: no payment intent recorded")
        data = {"payment_intent": payment_intent_id, "amount": str(amount_cents)}
        for name, value in (metadata or {}).items():
            data[f"metadata[{name}]"] = value
        body = self._send(
            "POST",
            REFUNDS_PATH,
            data=data,
            idempotency_key=idempotency_key,
            retry=False,
        )
        refund = _refund_from(body)
        if refund is None:
            raise ProviderUnavailable(_NO_REFUND)
        return refund

    def get_refund(self, stripe_refund_id: str) -> StripeRefund:
        """Read one refund by its Stripe id. Nothing is changed at the provider."""
        body = self._send(
            "GET",
            f"{REFUNDS_PATH}/{quote(stripe_refund_id, safe='')}",
            shape_error=_NO_REFUND,
        )
        refund = _refund_from(body)
        if refund is None:
            raise ProviderUnavailable(_NO_REFUND)
        return refund

    def list_refunds_for_payment_intent(self, payment_intent_id: str) -> list[StripeRefund]:
        """Every refund of one payment intent, read page by page. Nothing is changed."""
        refunds: list[StripeRefund] = []
        starting_after: str | None = None
        while True:
            params = {"payment_intent": payment_intent_id, "limit": "100"}
            if starting_after is not None:
                params["starting_after"] = starting_after
            body = self._send("GET", REFUNDS_PATH, params=params, shape_error=_NO_REFUND_LIST)
            items = body.get("data")
            if not isinstance(items, list):
                raise ProviderUnavailable(_NO_REFUND_LIST)
            refunds.extend(r for r in (_refund_from(item) for item in items) if r is not None)
            last = items[-1] if items else None
            last_id = last.get("id") if isinstance(last, dict) else None
            if body.get("has_more") is not True or not isinstance(last_id, str):
                return refunds
            if last_id == starting_after:
                raise ProviderUnavailable(_NO_REFUND_LIST)
            starting_after = last_id

    def create_customer(self, idempotency_key: str) -> str:
        """Create the Stripe customer of one user. The key makes concurrent first requests
        return the same customer."""
        body = self._send("POST", CUSTOMERS_PATH, idempotency_key=idempotency_key)
        customer_id = body.get("id")
        if not isinstance(customer_id, str):
            raise ProviderUnavailable("Stripe answered without a customer")
        return customer_id

    def create_setup_intent(self, customer_id: str, idempotency_key: str) -> str:
        """Create a SetupIntent to save a card without paying. Returns its client secret. The
        key makes a repeat return the same SetupIntent and client secret."""
        body = self._send(
            "POST",
            SETUP_INTENTS_PATH,
            data={
                "customer": customer_id,
                "usage": "off_session",
                "automatic_payment_methods[enabled]": "true",
            },
            idempotency_key=idempotency_key,
        )
        client_secret = body.get("client_secret")
        if not isinstance(client_secret, str):
            raise ProviderUnavailable("Stripe answered without a setup intent")
        return client_secret

    def list_cards(
        self,
        customer_id: str,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        starting_after: str | None = None,
    ) -> CardPage:
        params = {"type": "card", "limit": str(limit)}
        if starting_after is not None:
            params["starting_after"] = starting_after
        body = self._send(
            "GET",
            f"{CUSTOMERS_PATH}/{quote(customer_id, safe='')}/payment_methods",
            params=params,
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
        return CardPage(cards=cards, has_more=body.get("has_more") is True)

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
        retry: bool = True,
    ) -> dict[str, Any]:
        body = self._perform(
            method,
            path,
            data=data,
            params=params,
            idempotency_key=idempotency_key,
            shape_error=shape_error,
            retry=retry,
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
        retry: bool = True,
    ) -> dict[str, Any] | None:
        """Send one request and return its JSON object. With missing_ok, a 404 returns None
        (the object does not exist). Every other failure becomes ProviderUnavailable without
        the provider body; a definite 4xx refusal becomes ProviderRejected, which is the only
        failure that proves nothing was done (409 and 429 are not definite).

        Transient failures are retried up to MAX_PROVIDER_ATTEMPTS in total, with exponential
        backoff. A request is retried only when it is safe to repeat: a GET, or a POST that
        carries an Idempotency-Key. Detach carries no key, so it is never retried. A 409 is
        retried only with a key, because it means a same-key request is still running. A caller
        with retry=False (a refund) sends the request exactly once."""
        if not self._secret_key:
            raise ProviderUnavailable("Stripe secret key is not configured")
        headers = {"Authorization": f"Bearer {self._secret_key}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        retryable = retry and (method == "GET" or idempotency_key is not None)
        for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
            last_attempt = attempt == MAX_PROVIDER_ATTEMPTS
            try:
                response = self._client.request(
                    method, path, data=data, params=params, headers=headers
                )
            except httpx.HTTPError as error:
                if retryable and not last_attempt:
                    self._sleep(_backoff_seconds(attempt))
                    continue
                raise ProviderUnavailable("Stripe is unreachable") from error
            if missing_ok and response.status_code == 404:
                return None
            if response.status_code >= 400:
                transient = _is_transient(response.status_code, keyed=idempotency_key is not None)
                if retryable and transient and not last_attempt:
                    self._sleep(_backoff_seconds(attempt))
                    continue
                status = response.status_code
                if 400 <= status < 500 and status not in (409, 429):
                    raise ProviderRejected(f"Stripe refused the request ({status})")
                raise ProviderUnavailable(f"Stripe answered {status}")
            break
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderUnavailable("Stripe answered a body that is not JSON") from error
        if not isinstance(body, dict):
            raise ProviderUnavailable(shape_error)
        return body


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _refund_from(body: object) -> StripeRefund | None:
    """A refund object with a string id and status; None when the shape does not match."""
    if not isinstance(body, dict):
        return None
    refund_id = body.get("id")
    status = body.get("status")
    if not isinstance(refund_id, str) or not isinstance(status, str):
        return None
    raw = body.get("metadata")
    metadata = (
        {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(raw, dict)
        else {}
    )
    return StripeRefund(id=refund_id, status=status, metadata=metadata)


def _backoff_seconds(failed_attempt: int) -> float:
    """0.5 s after the first failed attempt, 1.0 s after the second."""
    return BACKOFF_BASE_SECONDS * 2 ** (failed_attempt - 1)


def _is_transient(status: int, *, keyed: bool) -> bool:
    """Stripe stores the first answer of a keyed POST, 5xx included, so repeating a keyed POST
    after a 500 only replays the stored error. A 5xx is retried for GET requests alone."""
    if status == 409:
        return keyed
    if status == 429:
        return True
    return status >= 500 and not keyed
