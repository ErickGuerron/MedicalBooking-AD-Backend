"""Retry schedule for patient notifications.

Exponential backoff: the wait after attempt k is BACKOFF_MINUTES[k - 1], so the attempts run at
0, 1, 3, 7 and 15 minutes. After MAX_ATTEMPTS failed attempts the notification is FALLIDA.
"""

from __future__ import annotations

from datetime import datetime, timedelta

MAX_ATTEMPTS = 5
BACKOFF_MINUTES = (1, 2, 4, 8)


def next_attempt_at(attempts: int, now: datetime) -> datetime | None:
    """When to try again after ``attempts`` failed attempts; None when no attempts remain."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return now + timedelta(minutes=BACKOFF_MINUTES[attempts - 1])
