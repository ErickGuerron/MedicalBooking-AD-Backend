"""Clinic hours and the two windows of the day.

Sample collection happens in the morning, until 10:00; medical consultations in the afternoon.
A slot belongs to exactly one window, and a service can only be booked into the window it
belongs to. Branch time is UTC-5 with no daylight saving, as in the rest of the scheduling code.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone

from medicalbooking.shared.domain.errors import ValidationFailed

BRANCH_TZ = timezone(timedelta(hours=-5))

# ASSUMPTION: opening and closing are the hours the seed has always used. Confirm with the clinic.
CLINIC_OPENS = time(8, 0)
CLINIC_CLOSES = time(17, 0)

# Franja names are the values stored in servicios_medicos.franja.
MANANA = "MANANA"  # sample collection: slots that start from 08:00 and end by 10:00
TARDE = "TARDE"  # consultations: slots that start from 12:00 and end by 17:00 (ASSUMPTION)
FRANJAS: dict[str, tuple[time, time]] = {
    MANANA: (time(8, 0), time(10, 0)),
    TARDE: (time(12, 0), time(17, 0)),
}

WORK_STEP_MINUTES = 15
# Every appointment is one hour: the clinic plans a week as one-hour slots.
SLOT_MINUTES = 60
MAX_BLOCKS_PER_DAY = 4


@dataclass(frozen=True)
class WorkBlock:
    """One stretch of work on a weekday (0 = Monday ... 6 = Sunday), in branch time."""

    dia_semana: int
    inicio: time
    fin: time


def franja_of_start(start: time) -> str | None:
    """The window a slot starting at `start` (branch time) belongs to, or None outside both."""
    for name, (opens, closes) in FRANJAS.items():
        if opens <= start < closes:
            return name
    return None


def validate_work_blocks(blocks: Sequence[WorkBlock]) -> None:
    """Refuse a weekly work schedule the clinic cannot take. An empty schedule is allowed."""
    per_day: dict[int, list[WorkBlock]] = {}
    for block in blocks:
        if not 0 <= block.dia_semana <= 6:
            raise ValidationFailed("'dia_semana' must be between 0 (Monday) and 6 (Sunday)")
        if block.inicio >= block.fin:
            raise ValidationFailed("'inicio' must be before 'fin'")
        if block.inicio < CLINIC_OPENS or block.fin > CLINIC_CLOSES:
            raise ValidationFailed("Work hours must be inside the clinic's opening hours")
        if block.inicio.minute % WORK_STEP_MINUTES or block.fin.minute % WORK_STEP_MINUTES:
            raise ValidationFailed("Work hours must start and end on a 15-minute step")
        per_day.setdefault(block.dia_semana, []).append(block)
    for day_blocks in per_day.values():
        if len(day_blocks) > MAX_BLOCKS_PER_DAY:
            raise ValidationFailed("A day can have at most four work blocks")
        ordered = sorted(day_blocks, key=lambda b: b.inicio)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.inicio < earlier.fin:
                raise ValidationFailed("Work blocks of the same day cannot overlap")


def slot_starts(block: WorkBlock, duration_min: int) -> list[time]:
    """Start times of the slots a work block offers.

    Each slot fits inside one window and inside the block.
    """
    starts: list[time] = []
    first = block.inicio.hour * 60 + block.inicio.minute
    last = block.fin.hour * 60 + block.fin.minute
    minute = first
    while minute + duration_min <= last:
        start = time(minute // 60, minute % 60)
        end_minute = minute + duration_min
        franja = franja_of_start(start)
        if franja is not None:
            closes = FRANJAS[franja][1]
            if end_minute <= closes.hour * 60 + closes.minute:
                starts.append(start)
        minute += duration_min
    return starts


def local_to_utc_naive(day: date, at: time) -> datetime:
    """A branch-time day and time as the naive UTC datetime the database stores."""
    return datetime.combine(day, at, BRANCH_TZ).astimezone(UTC).replace(tzinfo=None)


def declared_hours_payload(
    medico_id: str, vigente_desde: date, blocks: Sequence[WorkBlock]
) -> dict[str, object]:
    """Replica snapshot of one declared week: the whole block set, so a replay replaces the week."""
    ordered = sorted(blocks, key=lambda block: (block.dia_semana, block.inicio))
    return {
        "horario": {"medico_id": medico_id, "vigente_desde": vigente_desde.isoformat()},
        "bloques": [
            {
                "dia_semana": block.dia_semana,
                "inicio": block.inicio.isoformat(),
                "fin": block.fin.isoformat(),
            }
            for block in ordered
        ],
    }
