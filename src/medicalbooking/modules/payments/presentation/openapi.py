"""OpenAPI fragment for payments: the Stripe webhook (E4).

The webhook has no bearer security: Stripe signs each delivery with the endpoint secret and
the signature over the raw body is the only trust boundary.
"""

from __future__ import annotations

from typing import Any

_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}
_BEARER = [{"bearerAuth": []}]
_IDEMPOTENCY_KEY = {
    "name": "Idempotency-Key",
    "in": "header",
    "required": True,
    "description": "8 to 100 characters. The same key with the same invoice returns the same "
    "checkout; the same key with another invoice answers 409.",
    "schema": {"type": "string", "minLength": 8, "maxLength": 100},
}


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM_CONTENT}


def payments_fragment() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/invoices": {
                "post": {
                    "tags": ["Payments"],
                    "operationId": "createInvoice",
                    "summary": "Invoice an appointment (owner only)",
                    "description": (
                        "Creates one invoice with one detail line for the appointment price. "
                        "Only the patient who owns an active appointment (PENDIENTE_PAGO or "
                        "CONFIRMADA) may invoice it; one invoice per appointment."
                    ),
                    "security": _BEARER,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["cita_id"],
                                    "additionalProperties": False,
                                    "properties": {"cita_id": {"type": "string", "format": "uuid"}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Invoice created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string", "format": "uuid"},
                                            "estado": {"type": "string", "enum": ["PENDIENTE"]},
                                            "total": {"type": "string"},
                                            "moneda": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "401": _problem("Missing or invalid bearer token"),
                        "403": _problem("Not the owner, or the appointment does not exist"),
                        "409": _problem("Appointment not invoiceable or already invoiced"),
                        "422": _problem("Invalid body"),
                    },
                }
            },
            "/api/v1/payments/checkout": {
                "post": {
                    "tags": ["Payments"],
                    "operationId": "startCheckout",
                    "summary": "Start a Stripe Checkout for an invoice (owner only)",
                    "description": (
                        "Reserves one pending payment attempt, opens a Stripe Checkout Session "
                        "and returns its URL. The invoice is paid only when the verified "
                        "webhook confirms the session; a redirect never confirms payment."
                    ),
                    "security": _BEARER,
                    "parameters": [_IDEMPOTENCY_KEY],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["factura_id"],
                                    "additionalProperties": False,
                                    "properties": {
                                        "factura_id": {"type": "string", "format": "uuid"}
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Checkout session created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "pago_id": {"type": "string", "format": "uuid"},
                                            "checkout_url": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "200": {
                            "description": "Replay of the same key and invoice",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "pago_id": {"type": "string", "format": "uuid"},
                                            "checkout_url": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "401": _problem("Missing or invalid bearer token"),
                        "403": _problem("Not the owner, or the invoice does not exist"),
                        "409": _problem(
                            "Invoice not pending, or the key belongs to another invoice"
                        ),
                        "422": _problem("Missing Idempotency-Key or invalid body"),
                        "503": _problem("Payment provider not configured or unavailable"),
                    },
                }
            },
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
            },
        }
    }
