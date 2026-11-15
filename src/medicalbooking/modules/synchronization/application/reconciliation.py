"""Replica reconciliation: requeue the latest event of every aggregate whose replica disagrees.

One cycle reads the latest event of each tracked aggregate created since a cutoff, asks each
destination which of those aggregates it holds, plans the repairs (see domain.reconciliation),
and requeues the delivery of each repaired event. A requeue changes only a delivery that is
ENTREGADO or missing; the dispatcher keeps ownership of every other state.

The report counts requeues that changed a row, so a repair that is already PENDIENTE is not
counted again when the cycle runs twice.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from medicalbooking.modules.synchronization.domain.reconciliation import (
    TRACKED_AGGREGATE_TYPES,
    LatestEvent,
    plan_orphans,
    plan_repairs,
)


class LatestEventSource(Protocol):
    def latest_events(self, since: datetime) -> list[LatestEvent]: ...


class PresenceCheck(Protocol):
    """One destination. ``present_ids`` returns the subset of ``ids`` the destination holds."""

    destination_code: str

    def present_ids(self, aggregate_type: str, ids: Sequence[str]) -> set[str]: ...


class RepairStore(Protocol):
    def requeue(self, event_id: str, destination_code: str) -> bool: ...


@dataclass(frozen=True)
class ReconcileReport:
    aggregates: int
    # Requeues that changed a delivery, per destination code.
    repairs: dict[str, int] = field(default_factory=dict)


class ReplicaIdLister(Protocol):
    """One destination. ``list_ids`` returns every id of the aggregate type it holds."""

    destination_code: str

    def list_ids(self, aggregate_type: str) -> frozenset[str]: ...


class SourceIdReader(Protocol):
    def source_ids(self, aggregate_type: str) -> frozenset[str]: ...


class TombstoneWriter(Protocol):
    def tombstone(self, aggregate_type: str, aggregate_id: str, now: datetime) -> bool: ...


@dataclass(frozen=True)
class HardDeleteReport:
    # Replica ids with no source row, counted once per aggregate.
    orphans: int
    # Tombstones appended this cycle. An orphan that already had a delete event is not counted.
    tombstones: int


class HardDeleteDetector:
    """Find replica ids with no source row and tombstone them through the outbox.

    Replica ids are read first and source ids second. A replica only holds rows the outbox
    delivered, so a row created during the cycle is in the source before it can reach a replica.
    Read in this order, that row is never mistaken for a hard delete. Any failed listing raises
    before the first tombstone is written, so a partial replica view never creates a delete.
    """

    def __init__(
        self,
        source: SourceIdReader,
        replicas: Sequence[ReplicaIdLister],
        tombstones: TombstoneWriter,
    ) -> None:
        self._source = source
        self._replicas = list(replicas)
        self._tombstones = tombstones

    def run(self, now: datetime) -> HardDeleteReport:
        if not self._replicas:
            return HardDeleteReport(orphans=0, tombstones=0)
        aggregate_types = sorted(TRACKED_AGGREGATE_TYPES)
        replica_ids: dict[str, set[str]] = {
            aggregate_type: set() for aggregate_type in aggregate_types
        }
        for aggregate_type in aggregate_types:
            for replica in self._replicas:
                replica_ids[aggregate_type].update(replica.list_ids(aggregate_type))

        orphans = 0
        tombstones = 0
        for aggregate_type in aggregate_types:
            if not replica_ids[aggregate_type]:
                continue
            source = self._source.source_ids(aggregate_type)
            for aggregate_id in sorted(
                plan_orphans(source, frozenset(replica_ids[aggregate_type]))
            ):
                orphans += 1
                if self._tombstones.tombstone(aggregate_type, aggregate_id, now):
                    tombstones += 1
        return HardDeleteReport(orphans=orphans, tombstones=tombstones)


class ReplicaReconciler:
    def __init__(
        self,
        source: LatestEventSource,
        targets: Sequence[PresenceCheck],
        repairs: RepairStore,
    ) -> None:
        self._source = source
        self._targets = list(targets)
        self._repairs = repairs

    def run(self, since: datetime) -> ReconcileReport:
        events = [
            event
            for event in self._source.latest_events(since)
            if event.aggregate_type in TRACKED_AGGREGATE_TYPES
        ]
        present: dict[tuple[str, str], frozenset[str]] = {}
        for target in self._targets:
            for aggregate_type in sorted(TRACKED_AGGREGATE_TYPES):
                ids = sorted(
                    {
                        event.aggregate_id
                        for event in events
                        if event.aggregate_type == aggregate_type
                    }
                )
                if ids:
                    found = target.present_ids(aggregate_type, ids)
                    present[(target.destination_code, aggregate_type)] = frozenset(found)

        applied: Counter[str] = Counter()
        for repair in plan_repairs(events, present):
            if self._repairs.requeue(repair.event_id, repair.destination_code):
                applied[repair.destination_code] += 1
        return ReconcileReport(aggregates=len(events), repairs=dict(applied))
