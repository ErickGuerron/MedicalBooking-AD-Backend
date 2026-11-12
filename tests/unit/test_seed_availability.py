import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "seed_availability", Path(__file__).parents[2] / "scripts" / "seed_availability.py"
)
seed = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed)

THURSDAY = date(2026, 11, 12)
SUNDAY = date(2026, 11, 15)
LONG_AGO = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_next_sunday_from_midweek_and_from_sunday():
    assert seed.next_sunday(THURSDAY) == SUNDAY
    assert seed.next_sunday(SUNDAY) == SUNDAY


def test_a_day_has_slots_from_opening_to_closing_in_branch_time():
    slots = seed.build_slots(THURSDAY, THURSDAY, 30, LONG_AGO)
    assert len(slots) == 18  # 08:00-17:00 in 30-minute steps
    assert slots[0][0] == datetime(2026, 11, 12, 13, 0)  # 08:00 UTC-5 stored as UTC
    assert slots[-1][1] == datetime(2026, 11, 12, 22, 0)  # 17:00 UTC-5


def test_range_covers_every_day_through_sunday():
    slots = seed.build_slots(THURSDAY, SUNDAY, 30, LONG_AGO)
    assert len(slots) == 4 * 18


def test_slots_that_already_started_are_skipped():
    now = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)  # 10:00 branch time
    slots = seed.build_slots(THURSDAY, THURSDAY, 30, now)
    assert slots[0][0] == datetime(2026, 11, 12, 15, 30)
