"""Haemoglobin adjustments for altitude and smoking (WHO 2024, Tables 4 and 5). Pure functions.

The adjustment is subtracted from the observed haemoglobin in g/L, or added to the anaemia cut-off.
Each function returns None when its input is missing or outside the table: a missing input is
never read as "no adjustment". This module is not yet wired into any finding: the patient does not
have elevation or smoking data in the system (feature doc S12).
Source: WHO. Guideline on haemoglobin cutoffs to define anaemia in individuals and populations
(2024), ISBN 978-92-4-008854-2, Tables 4 and 5 and their remarks.
"""

from __future__ import annotations

# Table 4: (upper bound of the band in metres, adjustment in g/L). The bands are 500 m wide.
_ALTITUDE_BANDS: tuple[tuple[int, int], ...] = (
    (499, 0),
    (999, 4),
    (1499, 8),
    (1999, 11),
    (2499, 14),
    (2999, 18),
    (3499, 21),
    (3999, 25),
    (4499, 29),
    (4999, 33),
)


def altitude_adjustment(metres: int | None) -> int | None:
    """WHO 2024 Table 4. Up to 499 m needs no adjustment; above 4999 m is None."""
    if metres is None or metres < 0:
        return None
    for upper, adjustment in _ALTITUDE_BANDS:
        if metres <= upper:
            return adjustment
    return None


def smoking_adjustment(smoker: bool | None, cigarettes_per_day: int | None) -> int | None:
    """WHO 2024 Table 5. A non-smoker needs no adjustment. A smoker of unknown quantity is 3.

    Exactly 20 cigarettes a day is not in the categorical table (its top band is ">20"). WHO Table 5
    note b gives the formula 0.4565 x n - 0.0078 x n^2, which is 6.01 at n = 20: the top band value.
    """
    if smoker is None:
        return None
    if not smoker:
        return 0
    if cigarettes_per_day is None:
        return 3
    if cigarettes_per_day < 0:
        return None
    if cigarettes_per_day < 10:
        return 3
    if cigarettes_per_day <= 19:
        return 5
    return 6


def total_adjustment(
    altitude_m: int | None, smoker: bool | None, cigarettes: int | None
) -> int | None:
    """Sum of both adjustments in g/L, or None when either one is not evaluable."""
    altitude = altitude_adjustment(altitude_m)
    smoking = smoking_adjustment(smoker, cigarettes)
    if altitude is None or smoking is None:
        return None
    return altitude + smoking
