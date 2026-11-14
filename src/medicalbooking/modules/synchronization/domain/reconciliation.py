"""Replica reconciliation rules: what each aggregate's latest event says its replica must hold.

Pure functions with no I/O. A latest event is expected to be present in a replica unless its
type deletes the aggregate. The caller supplies which ids each destination actually holds, keyed
by (destination code, aggregate type), and only the keys it supplies are checked. Destinations
are independent of each other.

Phase 1 tracks ``usuario`` and ``cita``; other aggregate types are ignored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

TRACKED_AGGREGATE_TYPES: frozenset[str] = frozenset({"usuario", "cita"})
DELETED_EVENT_TYPES: frozenset[str] = frozenset({"usuario.eliminado", "cita.eliminada"})

REASON_MISSING = "missing"
REASON_PRESENT_AFTER_DELETE = "present_after_delete"


@dataclass(frozen=True)
class LatestEvent:
    """The event with the highest version of its aggregate. Ids are lower-case canonical text."""

    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str


@dataclass(frozen=True)
class Repair:
    """Requeue the delivery of ``event_id`` for ``destination_code``."""

    event_id: str
    destination_code: str
    reason: str


def expected_present(event_type: str) -> bool:
    return event_type not in DELETED_EVENT_TYPES


def plan_repairs(
    events: Iterable[LatestEvent],
    present: Mapping[tuple[str, str], frozenset[str]],
) -> list[Repair]:
    """Compare each latest event with the ids found in every checked destination.

    ``present`` maps (destination code, aggregate type) to the aggregate ids that destination
    holds. A destination that is not a key of ``present`` is not checked.
    """
    repairs: list[Repair] = []
    for event in events:
        if event.aggregate_type not in TRACKED_AGGREGATE_TYPES:
            continue
        should_exist = expected_present(event.event_type)
        for (destination, aggregate_type), found in sorted(present.items()):
            if aggregate_type != event.aggregate_type:
                continue
            is_present = event.aggregate_id in found
            if should_exist and not is_present:
                repairs.append(Repair(event.event_id, destination, REASON_MISSING))
            elif not should_exist and is_present:
                repairs.append(Repair(event.event_id, destination, REASON_PRESENT_AFTER_DELETE))
    return repairs
