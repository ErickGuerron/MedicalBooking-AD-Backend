"""Doctor cancellation limits: monthly quota and 24-hour notice (S1, S2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from medicalbooking.modules.scheduling.domain.policies import (
    CANCELACION_MEDICO_FUERA_DE_PLAZO,
    LIMITE_CANCELACION_MEDICO,
    MAX_DOCTOR_CANCELLATIONS_PER_MONTH,
    doctor_cancellation_blockers,
    month_bounds,
)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
FAR_SLOT = NOW + timedelta(days=5)


def test_quota_is_three_per_month() -> None:
    assert MAX_DOCTOR_CANCELLATIONS_PER_MONTH == 3


def test_below_quota_with_enough_notice_has_no_blocker() -> None:
    assert (
        doctor_cancellation_blockers(now=NOW, slot_start=FAR_SLOT, cancellations_this_month=2)
        == ()
    )


def test_reaching_the_quota_blocks_until_approved() -> None:
    assert doctor_cancellation_blockers(
        now=NOW, slot_start=FAR_SLOT, cancellations_this_month=3
    ) == (LIMITE_CANCELACION_MEDICO,)


def test_less_than_24_hours_notice_blocks() -> None:
    slot = NOW + timedelta(hours=23, minutes=59)
    assert doctor_cancellation_blockers(now=NOW, slot_start=slot, cancellations_this_month=0) == (
        CANCELACION_MEDICO_FUERA_DE_PLAZO,
    )


def test_exactly_24_hours_notice_is_allowed() -> None:
    slot = NOW + timedelta(hours=24)
    assert doctor_cancellation_blockers(now=NOW, slot_start=slot, cancellations_this_month=0) == ()


def test_quota_and_notice_both_block_and_report_both() -> None:
    slot = NOW + timedelta(hours=1)
    assert doctor_cancellation_blockers(now=NOW, slot_start=slot, cancellations_this_month=3) == (
        LIMITE_CANCELACION_MEDICO,
        CANCELACION_MEDICO_FUERA_DE_PLAZO,
    )


def test_past_slot_is_late() -> None:
    slot = NOW - timedelta(minutes=5)
    assert CANCELACION_MEDICO_FUERA_DE_PLAZO in doctor_cancellation_blockers(
        now=NOW, slot_start=slot, cancellations_this_month=0
    )


@pytest.mark.parametrize(
    ("now", "start", "end"),
    [
        (
            datetime(2026, 11, 30, 23, 59, tzinfo=UTC),
            datetime(2026, 11, 1, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 12, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
        ),
    ],
)
def test_month_bounds_are_utc_calendar_months(
    now: datetime, start: datetime, end: datetime
) -> None:
    assert month_bounds(now) == (start, end)
