"""HTTP adapter for payments (E4): the Stripe webhook.

The endpoint has no JWT: Stripe cannot send one. Trust comes from the signature over the raw
body, which is read before any parsing. Errors go through the shared problem handler.
"""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, request

from medicalbooking.modules.payments.application.webhook_service import (
    StripeWebhookService,
    WebhookNotConfigured,
)

payments_bp = Blueprint("payments", __name__, url_prefix="/api/v1")

WEBHOOK_EXTENSION = "medicalbooking.payments_webhook"


@payments_bp.post("/payments/webhook")
def stripe_webhook() -> tuple[Response, int]:
    service: StripeWebhookService | None = current_app.extensions.get(WEBHOOK_EXTENSION)
    if service is None:
        raise WebhookNotConfigured("Stripe webhook secret is not configured")
    outcome = service.handle(
        request.get_data(cache=False),
        request.headers.get("Stripe-Signature"),
    )
    return jsonify({"recibido": True, "resultado": outcome}), 200
