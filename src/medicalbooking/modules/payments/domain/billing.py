"""Billing rules (facturas and checkout): permissions, states and money conversion.

Pure functions and constants. Persistence and the Stripe call live in other layers.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from medicalbooking.shared.domain.errors import ValidationFailed

PERM_INVOICE_CREATE = "facturas.crear"
PERM_CHECKOUT = "pagos.iniciar"

# Only appointments that still hold or already hold a slot can be invoiced (S7).
INVOICEABLE_APPOINTMENT_STATES = frozenset({"PENDIENTE_PAGO", "CONFIRMADA"})

FACTURA_PENDIENTE = "PENDIENTE"
PAGO_PENDIENTE = "PENDIENTE"
PAGO_RECHAZADO = "RECHAZADO"

MIN_IDEMPOTENCY_KEY = 8
MAX_IDEMPOTENCY_KEY = 100


def validate_idempotency_key(key: str) -> None:
    if not MIN_IDEMPOTENCY_KEY <= len(key) <= MAX_IDEMPOTENCY_KEY:
        raise ValidationFailed(
            f"Idempotency-Key must be {MIN_IDEMPOTENCY_KEY} to {MAX_IDEMPOTENCY_KEY} characters"
        )


def to_cents(amount: Decimal) -> int:
    """Stripe amounts are integers in the smallest currency unit."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
