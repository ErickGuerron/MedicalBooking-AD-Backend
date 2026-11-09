"""Haemoglobin adjustments for altitude and smoking (WHO 2024 Tables 4 and 5). Pure functions.

Adjustments are subtracted from the observed haemoglobin (g/L). Without elevation or smoking data
the result is None, never zero: a missing input must not look like "no adjustment".
WHO labels the top smoking band ">20 cigarettes/day", so exactly 20 is not assigned and is None.
"""

from __future__ import annotations

import pytest

from medicalbooking.modules.clinical.domain.hb_adjustments import (
    altitude_adjustment,
    smoking_adjustment,
    total_adjustment,
)


@pytest.mark.parametrize(
    ("metres", "expected"),
    [
        (0, 0),
        (1, 0),
        (499, 0),
        (500, 4),
        (999, 4),
        (1000, 8),
        (1499, 8),
        (1500, 11),
        (2000, 14),
        (2500, 18),
        (3000, 21),
        (3500, 25),
        (4000, 29),
        (4500, 33),
        (4999, 33),
    ],
)
def test_altitude_adjustment_follows_who_table_4(metres: int, expected: int) -> None:
    assert altitude_adjustment(metres) == expected


def test_altitude_outside_the_table_is_not_evaluable() -> None:
    assert altitude_adjustment(5000) is None
    assert altitude_adjustment(-1) is None
    assert altitude_adjustment(None) is None


@pytest.mark.parametrize(
    ("smoker", "cigarettes", "expected"),
    [
        (False, None, 0),
        (True, None, 3),  # smoker, quantity unknown
        (True, 5, 3),  # fewer than 10
        (True, 9, 3),
        (True, 10, 5),
        (True, 19, 5),
        (True, 21, 6),
        (True, 40, 6),
    ],
)
def test_smoking_adjustment_follows_who_table_5(
    smoker: bool, cigarettes: int | None, expected: int
) -> None:
    assert smoking_adjustment(smoker, cigarettes) == expected


def test_exactly_twenty_cigarettes_is_not_assigned_to_a_band() -> None:
    assert smoking_adjustment(True, 20) is None


def test_unknown_smoking_status_is_not_evaluable() -> None:
    assert smoking_adjustment(None, None) is None


def test_total_adds_altitude_and_smoking() -> None:
    assert total_adjustment(altitude_m=2000, smoker=True, cigarettes=12) == 14 + 5


def test_total_is_not_evaluable_when_one_input_is_missing() -> None:
    assert total_adjustment(altitude_m=None, smoker=False, cigarettes=None) is None
    assert total_adjustment(altitude_m=0, smoker=True, cigarettes=20) is None
