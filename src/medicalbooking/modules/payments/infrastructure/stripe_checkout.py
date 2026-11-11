"""Stripe Checkout Session adapter (E4 billing). Calls the Stripe HTTP API with httpx.

The secret key is sent only as a bearer token and never logged. Provider errors are reduced to
ProviderUnavailable so no provider body or secret reaches the client.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from medicalbooking.modules.payments.application.billing_service import (
    CheckoutRequest,
    CheckoutSession,
    CheckoutSnapshot,
    ProviderUnavailable,
)

STRIPE_API_BASE = "https://api.stripe.com"
CHECKOUT_PATH = "/v1/checkout/sessions"


class StripeCheckoutGateway:
    """Creates embedded Checkout Sessions. The publishable key is public by design: the
    browser needs it to mount the session with its client secret."""

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
        if not self._secret_key:
            raise ProviderUnavailable("Stripe secret key is not configured")
        form = {
            "mode": "payment",
            "ui_mode": "embedded_page",
            "return_url": self._return_url,
            "client_reference_id": request.reference,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": request.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(request.amount_cents),
            "line_items[0][price_data][product_data][name]": request.description,
        }
        try:
            response = self._client.post(
                CHECKOUT_PATH,
                data=form,
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Idempotency-Key": request.idempotency_key,
                },
            )
        except httpx.HTTPError as error:
            raise ProviderUnavailable("Stripe is unreachable") from error
        if response.status_code >= 400:
            raise ProviderUnavailable(f"Stripe answered {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderUnavailable("Stripe answered a body that is not JSON") from error
        if not isinstance(body, dict):
            raise ProviderUnavailable("Stripe answered without a checkout session")
        session_id = body.get("id")
        client_secret = body.get("client_secret")
        if not isinstance(session_id, str) or not isinstance(client_secret, str):
            raise ProviderUnavailable("Stripe answered without a checkout session")
        return CheckoutSession(id=session_id, client_secret=client_secret)

    def retrieve_session(self, session_id: str) -> CheckoutSnapshot:
        """Read a session's state with a GET. Nothing is created, charged or changed."""
        if not self._secret_key:
            raise ProviderUnavailable("Stripe secret key is not configured")
        try:
            response = self._client.get(
                f"{CHECKOUT_PATH}/{quote(session_id, safe='')}",
                headers={"Authorization": f"Bearer {self._secret_key}"},
            )
        except httpx.HTTPError as error:
            raise ProviderUnavailable("Stripe is unreachable") from error
        if response.status_code >= 400:
            raise ProviderUnavailable(f"Stripe answered {response.status_code}")
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderUnavailable("Stripe answered a body that is not JSON") from error
        if not isinstance(body, dict) or not isinstance(body.get("id"), str):
            raise ProviderUnavailable("Stripe answered without a checkout session")
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
