"""Slot times that fall in a clinic window, for fixtures that book consultations.

Consultations are booked in the afternoon (13:00 branch time), so a fixture's slot must start in
that window. `afternoon` keeps the day the fixture asked for and moves the start to 13:00 branch
time when it falls outside the afternoon, on the same day or the next one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

BRANCH_OFFSET = timedelta(hours=-5)


def afternoon(moment: datetime) -> datetime:
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    local = (aware + BRANCH_OFFSET).replace(second=0, microsecond=0)
    if local.hour < 12:
        local = local.replace(hour=13, minute=0)
    elif local.hour >= 17:
        local = (local + timedelta(days=1)).replace(hour=13, minute=0)
    return (local - BRANCH_OFFSET).astimezone(UTC)
