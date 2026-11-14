"""Catalog rules that do not depend on infrastructure: permission and attention schedule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

PERM_ADMIN = "catalogo.administrar"


@dataclass(frozen=True)
class WorkingDay:
    """Working hours of one ISO weekday (1 = Monday ... 7 = Sunday), local time "HH:MM"."""

    dia_semana: int
    desde: str
    hasta: str


@dataclass(frozen=True)
class AttentionSchedule:
    vigente_desde: date | None
    vigente_hasta: date | None
    dias: tuple[WorkingDay, ...]


def derive_attention_schedule(
    agendas: list[tuple[date, date | None]],
    local_slots: list[tuple[datetime, datetime]],
) -> AttentionSchedule:
    """Attention schedule of one doctor, derived from existing data only.

    ``agendas`` are the (fecha_inicio, fecha_fin) of the doctor's active agendas: the schedule is
    valid from the earliest start to the latest end (open-ended when any agenda has no end).
    ``local_slots`` are the (start, end) of the doctor's upcoming slots, already in the local
    time of the agenda's site: per ISO weekday the schedule spans the earliest start to the
    latest end of those slots.

    shortcut: a gap inside a day (lunch) is not shown and a slot that crosses local midnight is
    clipped to its start day; upgrade when agendas need explicit weekly templates.
    """
    if not agendas:
        return AttentionSchedule(None, None, ())
    starts = [start for start, _ in agendas]
    ends = [end for _, end in agendas]
    until = None if any(end is None for end in ends) else max(e for e in ends if e is not None)
    days: dict[int, tuple[str, str]] = {}
    for start, end in local_slots:
        day = start.isoweekday()
        first = start.strftime("%H:%M")
        last = end.strftime("%H:%M") if end.date() == start.date() else "23:59"
        known = days.get(day)
        days[day] = (first, last) if known is None else (min(known[0], first), max(known[1], last))
    ordered = tuple(WorkingDay(day, *days[day]) for day in sorted(days))
    return AttentionSchedule(min(starts), until, ordered)
