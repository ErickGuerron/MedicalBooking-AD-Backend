"""OpenAPI fragment for payments: the Stripe webhook (E4).

The webhook has no bearer security: Stripe signs each delivery with the endpoint secret and
the signature over the raw body is the only trust boundary.
"""

from __future__ import annotations

from typing import Any

_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM_CONTENT}


def payments_fragment() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/payments/webhook": {
                "post": {
                    "tags": ["Payments"],
                    "operationId": "stripeWebhook",
                    "summary": "Receive a signed Stripe event",
                    "description": (
                        "Verifies the Stripe-Signature header over the raw body (HMAC-SHA256, "
                        "v1, 5 minute tolerance), records the event once and confirms, rejects "
                        "or cancels the matching checkout payment. Payments are never confirmed "
                        "by a browser redirect."
                    ),
                    "parameters": [
                        {
                            "name": "Stripe-Signature",
                            "in": "header",
                            "required": True,
                            "description": "Signature header sent by Stripe",
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {
                        "200": {
                            "description": "Event accepted (processed, duplicate or ignored)",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "recibido": {"type": "boolean"},
                                            "resultado": {
                                                "type": "string",
                                                "enum": [
                                                    "PROCESADO",
                                                    "DUPLICADO",
                                                    "IGNORADO",
                                                    "FALLIDO",
                                                ],
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        "400": _problem(
                            "Missing, stale or invalid signature, or a malformed event"
                        ),
                        "503": _problem("Webhook secret not configured; the endpoint fails closed"),
                    },
                }
            }
        }
    }
