"""Clinic hours and the two windows of the day.

Sample collection happens in the morning and consultations in the afternoon.
"""

from __future__ import annotations

from datetime import time

import pytest

from medicalbooking.modules.scheduling.domain.clinic_hours import (
    WorkBlock,
    franja_of_start,
    slot_starts,
    validate_work_blocks,
)
from medicalbooking.shared.domain.errors import ValidationFailed


def block(day: int, start: str, end: str) -> WorkBlock:
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    return WorkBlock(dia_semana=day, inicio=time(h1, m1), fin=time(h2, m2))


@pytest.mark.parametrize(
    ("start", "franja"),
    [
        ("08:00", "MANANA"),
        ("09:30", "MANANA"),
        ("10:00", None),
        ("11:30", None),
        ("12:00", "TARDE"),
        ("16:30", "TARDE"),
        ("17:00", None),
    ],
)
def test_a_slot_start_belongs_to_the_morning_or_the_afternoon_window(start, franja) -> None:
    h, m = map(int, start.split(":"))

    assert franja_of_start(time(h, m)) == franja


def test_a_valid_work_day_is_accepted() -> None:
    validate_work_blocks([block(0, "08:00", "10:00"), block(0, "12:00", "17:00")])


@pytest.mark.parametrize(
    "blocks",
    [
        [block(0, "10:00", "09:00")],  # inverted
        [block(0, "07:30", "09:00")],  # opens before the clinic
        [block(0, "16:00", "17:30")],  # closes after the clinic
        [block(0, "08:05", "09:00")],  # not on a 15-minute step
        [block(7, "08:00", "09:00")],  # no such weekday
        [block(0, "08:00", "10:00"), block(0, "09:30", "11:00")],  # overlap
        [
            block(0, "08:00", "09:00"),
            block(0, "09:00", "10:00"),
            block(0, "10:00", "11:00"),
            block(0, "11:00", "12:00"),
            block(0, "12:00", "13:00"),
        ],  # more than four blocks
    ],
)
def test_an_invalid_work_day_is_refused(blocks) -> None:
    with pytest.raises(ValidationFailed):
        validate_work_blocks(blocks)


def test_consultation_slots_fill_the_afternoon_block() -> None:
    starts = slot_starts(block(0, "13:00", "14:30"), duration_min=45)

    assert starts == [time(13, 0), time(13, 45)]


def test_a_morning_slot_must_end_before_the_sample_window_closes() -> None:
    starts = slot_starts(block(0, "09:30", "10:30"), duration_min=30)

    assert starts == [time(9, 30)]


def test_a_slot_that_would_end_after_the_work_block_is_not_offered() -> None:
    starts = slot_starts(block(0, "11:30", "12:45"), duration_min=30)

    assert starts == [time(12, 0)]
