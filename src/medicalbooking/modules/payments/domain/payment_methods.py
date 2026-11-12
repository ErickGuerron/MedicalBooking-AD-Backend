"""Saved payment method rules: the permission, the shape of a Stripe PaymentMethod id, the page
limits of the card listing and the scoped idempotency key of a SetupIntent.

Pure functions and constants. Ownership is checked in the application layer.
"""

from __future__ import annotations

import re

from medicalbooking.shared.domain.errors import ValidationFailed

PERM_PAYMENT_METHODS = "metodos_pago.gestionar"

DEFAULT_PAGE_LIMIT = 10
MAX_PAGE_LIMIT = 100

_LIMIT_MESSAGE = f"limit must be an integer from 1 to {MAX_PAGE_LIMIT}"
_STARTING_AFTER_MESSAGE = "starting_after must be a Stripe payment method id"

# Stripe ids look like pm_1Q0PsIJvEtkwdCNYMSaVuRz6. Anything else is treated as a missing card
# before Stripe is asked, so path characters never reach the provider URL.
_PAYMENT_METHOD_ID = re.compile(r"^pm_[A-Za-z0-9_]{1,100}$")
_DIGITS = re.compile(r"[0-9]{1,9}")


def is_payment_method_id(value: str) -> bool:
    return _PAYMENT_METHOD_ID.fullmatch(value) is not None


def validate_page_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_PAGE_LIMIT:
        raise ValidationFailed(_LIMIT_MESSAGE)


def parse_page_limit(raw: str | None) -> int:
    """The limit query value: absent means the default, anything else must be whole digits in
    range. Rejected values never reach the provider."""
    if raw is None:
        return DEFAULT_PAGE_LIMIT
    if _DIGITS.fullmatch(raw) is None:
        raise ValidationFailed(_LIMIT_MESSAGE)
    limit = int(raw)
    validate_page_limit(limit)
    return limit


def validate_starting_after(value: str) -> None:
    if not is_payment_method_id(value):
        raise ValidationFailed(_STARTING_AFTER_MESSAGE)


def setup_idempotency_key(usuario_id: str, client_key: str) -> str:
    """Scope the client's key to one user, so two users can never collide on the same key."""
    return f"setup-{usuario_id}-{client_key}"
