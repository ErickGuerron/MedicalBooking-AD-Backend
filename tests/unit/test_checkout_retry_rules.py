"""Checkout retry rules (checkout-retry S1-S3, S9): schedule, limit and per-retry keys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from medicalbooking.modules.payments.domain.checkout_retry import (
    MAX_CHECKOUT_RETRIES,
    next_retry_at,
    retry_key,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def test_the_first_retry_is_due_one_minute_after_the_first_failure():
    assert next_retry_at(0, NOW) == NOW + timedelta(minutes=1)


def test_the_gaps_between_retries_follow_the_agreed_schedule():
    gaps = []
    for made in range(MAX_CHECKOUT_RETRIES):
        due = next_retry_at(made, NOW)
        assert due is not None
        gaps.append(due - NOW)
    assert gaps == [
        timedelta(minutes=1),
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=2),
        timedelta(minutes=5),
    ]


def test_no_retry_is_scheduled_after_the_fifth_retry_failed():
    assert MAX_CHECKOUT_RETRIES == 5
    assert next_retry_at(MAX_CHECKOUT_RETRIES, NOW) is None


def test_each_retry_gets_its_own_key_so_stripe_never_replays_a_failure():
    keys = [retry_key("checkout-key-0001", n) for n in range(1, MAX_CHECKOUT_RETRIES + 1)]
    assert len(set(keys)) == MAX_CHECKOUT_RETRIES
    assert "checkout-key-0001" not in keys
    assert keys[0] == "checkout-key-0001:1"
