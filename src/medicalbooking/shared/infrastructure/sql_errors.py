"""Classify SQL Server errors so only expected conflicts become client answers.

Other integrity failures (foreign keys, checks) stay errors, so real defects are not
reported to clients as "slot taken". Deadlocks are retried once, then answered with a
retryable 503 instead of a generic 500.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.exc import DBAPIError

from medicalbooking.shared.domain.errors import ServiceUnavailable

_UNIQUE_MARKERS = ("(2601)", "(2627)", "violation of unique key", "duplicate key")
_DEADLOCK_MARKERS = ("(1205)", "(3960)", "deadlock victim", "[40001]")
MAX_ATTEMPTS = 2


def is_unique_violation(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _UNIQUE_MARKERS)


def is_deadlock(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _DEADLOCK_MARKERS)


def retry_on_deadlock[T](work: Callable[[], T]) -> T:
    """Run a transaction body, retrying it when SQL Server chose it as a deadlock victim."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return work()
        except DBAPIError as error:
            if not is_deadlock(str(error.orig) if error.orig else str(error)):
                raise
            if attempt == MAX_ATTEMPTS:
                raise ServiceUnavailable("The database is busy. Retry the request.") from error
    raise AssertionError("unreachable")
