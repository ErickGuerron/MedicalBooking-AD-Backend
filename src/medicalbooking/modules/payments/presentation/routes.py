"""HTTP adapter for payments: the Stripe webhook (E4), billing (invoices and checkout) and saved
payment methods (/me/payment-methods).

The webhook has no JWT: Stripe cannot send one. Trust comes from the signature over the raw
body, which is read before any parsing. Billing and saved-card endpoints require a JWT and
per-resource authorization in the application layer. Errors go through the shared problem
handler.
"""

from __future__ import annotations

import uuid
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, ValidationError

from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.modules.payments.application.billing_service import (
    BillingService,
    CheckoutStart,
    InvoiceRecord,
)
from medicalbooking.modules.payments.application.payment_methods import (
    PaymentMethodService,
    SavedCard,
)
from medicalbooking.modules.payments.application.webhook_service import (
    StripeWebhookService,
    WebhookNotConfigured,
)
from medicalbooking.modules.payments.domain.payment_methods import parse_page_limit
from medicalbooking.shared.domain.errors import ValidationFailed

payments_bp = Blueprint("payments", __name__, url_prefix="/api/v1")

WEBHOOK_EXTENSION = "medicalbooking.payments_webhook"
BILLING_EXTENSION = "medicalbooking.billing"
PAYMENT_METHODS_EXTENSION = "medicalbooking.payment_methods"
IDEMPOTENCY_HEADER = "Idempotency-Key"


class CreateInvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cita_id: uuid.UUID


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factura_id: uuid.UUID


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


@payments_bp.post("/invoices")
@authenticated
def create_invoice() -> tuple[Response, int]:
    body = _parse(CreateInvoiceRequest)
    invoice = _billing().create_invoice(g.actor, cita_id=str(body.cita_id))
    return jsonify(_invoice_json(invoice)), 201


@payments_bp.post("/payments/checkout")
@authenticated
def start_checkout() -> tuple[Response, int]:
    body = _parse(CheckoutRequest)
    key = request.headers.get(IDEMPOTENCY_HEADER, "")
    if not key:
        raise ValidationFailed("Idempotency-Key header is required")
    result = _billing().start_checkout(
        g.actor,
        factura_id=str(body.factura_id),
        idempotency_key=key,
    )
    return jsonify(_checkout_json(result)), 200 if result.replayed else 201


@payments_bp.get("/me/payment-methods")
@authenticated
def list_payment_methods() -> tuple[Response, int]:
    limit = parse_page_limit(request.args.get("limit"))
    page = _payment_methods().list_cards(
        g.actor,
        limit=limit,
        starting_after=request.args.get("starting_after"),
    )
    return (
        jsonify(
            {
                "items": [_card_json(card) for card in page.items],
                "has_more": page.has_more,
                "next_cursor": page.next_cursor,
            }
        ),
        200,
    )


@payments_bp.post("/me/payment-methods/setup-intents")
@authenticated
def start_setup_intent() -> tuple[Response, int]:
    key = request.headers.get(IDEMPOTENCY_HEADER, "")
    if not key:
        raise ValidationFailed("Idempotency-Key header is required")
    result = _payment_methods().start_setup(g.actor, idempotency_key=key)
    return (
        jsonify(
            {
                "client_secret": result.client_secret,
                "publishable_key": result.publishable_key,
            }
        ),
        201,
    )


@payments_bp.delete("/me/payment-methods/<path:payment_method_id>")
@authenticated
def remove_payment_method(payment_method_id: str) -> tuple[Response, int]:
    _payment_methods().remove_card(g.actor, payment_method_id=payment_method_id)
    return Response(status=204), 204


def _billing() -> BillingService:
    return current_app.extensions[BILLING_EXTENSION]


def _payment_methods() -> PaymentMethodService:
    return current_app.extensions[PAYMENT_METHODS_EXTENSION]


def _card_json(card: SavedCard) -> dict[str, Any]:
    return {
        "id": card.id,
        "brand": card.brand,
        "last4": card.last4,
        "exp_month": card.exp_month,
        "exp_year": card.exp_year,
        "is_default": card.is_default,
    }


def _parse(model: type[BaseModel]) -> Any:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationFailed("Invalid fields: " + ", ".join(fields)) from None


def _invoice_json(invoice: InvoiceRecord) -> dict[str, Any]:
    return {
        "id": invoice.id,
        "estado": invoice.estado,
        "total": str(invoice.total),
        "moneda": invoice.moneda,
    }


def _checkout_json(result: CheckoutStart) -> dict[str, Any]:
    return {
        "pago_id": result.pago_id,
        "client_secret": result.client_secret,
        "publishable_key": result.publishable_key,
    }
