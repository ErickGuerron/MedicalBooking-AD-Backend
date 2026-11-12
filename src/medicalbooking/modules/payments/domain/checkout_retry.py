"""Retry rules for a checkout session the provider failed to create (checkout-retry S1-S3, S8).

Pure functions: the service decides when a retry is due, the worker only asks which attempts
are due. Each retry sends a new Idempotency-Key, because Stripe replays the first answer of a
keyed POST, including a 5xx.
"""

from __future__ import annotations

from datetime import datetime, timedelta

MAX_CHECKOUT_RETRIES = 5

# Gap before retry 1, 2, 3, 4 and 5, counted from the failure that precedes it.
_RETRY_GAPS = (
    timedelta(minutes=1),
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=2),
    timedelta(minutes=5),
)

CHECKOUT_UNAVAILABLE_CODE = "PROVEEDOR_NO_DISPONIBLE"


def next_retry_at(retries_made: int, failed_at: datetime) -> datetime | None:
    """When the next retry is due after `retries_made` retries have failed, or None when the
    limit is reached and the attempt is exhausted."""
    if retries_made >= MAX_CHECKOUT_RETRIES:
        return None
    return failed_at + _RETRY_GAPS[retries_made]


def retry_key(attempt_key: str, retry_number: int) -> str:
    """The Idempotency-Key sent to Stripe for retry number `retry_number` (1-based)."""
    return f"{attempt_key}:{retry_number}"
