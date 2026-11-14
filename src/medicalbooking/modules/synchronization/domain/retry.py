"""Retry policy for outbox deliveries: 1, 2, 4, 8, 16 and 32 minutes, then 32 again."""

from __future__ import annotations

from datetime import timedelta

MAX_ATTEMPTS = 10
_MAX_BACKOFF_EXPONENT = 5  # 2**5 minutes = 32 minutes, the cap


def backoff_after(attempts: int) -> timedelta:
    """Delay before the next attempt, given how many attempts have already failed."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    return timedelta(minutes=2 ** min(attempts - 1, _MAX_BACKOFF_EXPONENT))
