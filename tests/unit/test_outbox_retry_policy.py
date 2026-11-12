"""Outbox retry backoff: 1, 2, 4, 8, 16, 32 minutes, then capped at 32."""

from __future__ import annotations

from datetime import timedelta

import pytest

from medicalbooking.modules.synchronization.domain.retry import MAX_ATTEMPTS, backoff_after


def test_the_outbox_gives_up_after_ten_attempts() -> None:
    assert MAX_ATTEMPTS == 10


@pytest.mark.parametrize(
    ("attempts", "minutes"),
    [(1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 32), (7, 32), (9, 32)],
)
def test_backoff_doubles_each_attempt_and_stops_at_thirty_two_minutes(
    attempts: int, minutes: int
) -> None:
    assert backoff_after(attempts) == timedelta(minutes=minutes)


def test_backoff_rejects_an_attempt_number_below_one() -> None:
    with pytest.raises(ValueError):
        backoff_after(0)
