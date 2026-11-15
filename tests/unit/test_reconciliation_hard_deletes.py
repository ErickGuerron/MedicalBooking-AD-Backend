"""Hard-delete detection cycle: replica ids first, source ids second, tombstones last.

Fakes only: no database, no network. The log records the order of every read and write.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from medicalbooking.modules.synchronization.application.reconciliation import (
    HardDeleteDetector,
)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
USUARIO_GONE = "11111111-1111-4111-8111-111111111111"
USUARIO_KEPT = "22222222-2222-4222-8222-222222222222"
CITA_GONE = "33333333-3333-4333-8333-333333333333"
CITA_KEPT = "44444444-4444-4444-8444-444444444444"


class _Log:
    def __init__(self) -> None:
        self.entries: list[tuple[str, ...]] = []


class _Lister:
    def __init__(self, destination: str, ids: dict[str, set[str]], log: _Log) -> None:
        self.destination_code = destination
        self._ids = ids
        self._log = log

    def list_ids(self, aggregate_type: str) -> frozenset[str]:
        self._log.entries.append(("replica", self.destination_code, aggregate_type))
        return frozenset(self._ids.get(aggregate_type, set()))


class _Failing:
    destination_code = "SUPABASE"

    def list_ids(self, aggregate_type: str) -> frozenset[str]:
        raise RuntimeError("replica unreachable")


class _Source:
    def __init__(self, ids: dict[str, set[str]], log: _Log) -> None:
        self._ids = ids
        self._log = log

    def source_ids(self, aggregate_type: str) -> frozenset[str]:
        self._log.entries.append(("source", aggregate_type))
        return frozenset(self._ids.get(aggregate_type, set()))


class _Tombstones:
    def __init__(self, log: _Log, already: frozenset[str] = frozenset()) -> None:
        self._log = log
        self._already = already
        self.calls: list[tuple[str, str, datetime]] = []

    def tombstone(self, aggregate_type: str, aggregate_id: str, now: datetime) -> bool:
        self._log.entries.append(("tombstone", aggregate_type, aggregate_id))
        self.calls.append((aggregate_type, aggregate_id, now))
        return aggregate_id not in self._already


def test_a_replica_id_missing_from_the_source_is_tombstoned_once() -> None:
    log = _Log()
    tombstones = _Tombstones(log)
    detector = HardDeleteDetector(
        source=_Source({"usuario": {USUARIO_KEPT}}, log),
        replicas=[_Lister("MONGODB", {"usuario": {USUARIO_GONE, USUARIO_KEPT}}, log)],
        tombstones=tombstones,
    )

    report = detector.run(now=NOW)

    assert tombstones.calls == [("usuario", USUARIO_GONE, NOW)]
    assert report.orphans == 1
    assert report.tombstones == 1


def test_a_cita_missing_from_the_source_gets_a_cita_tombstone() -> None:
    log = _Log()
    tombstones = _Tombstones(log)
    detector = HardDeleteDetector(
        source=_Source({"cita": {CITA_KEPT}}, log),
        replicas=[_Lister("SUPABASE", {"cita": {CITA_GONE, CITA_KEPT}}, log)],
        tombstones=tombstones,
    )

    detector.run(now=NOW)

    assert tombstones.calls == [("cita", CITA_GONE, NOW)]


def test_replica_ids_are_read_before_any_source_id() -> None:
    log = _Log()
    detector = HardDeleteDetector(
        source=_Source({"usuario": {USUARIO_KEPT}, "cita": {CITA_KEPT}}, log),
        replicas=[
            _Lister("MONGODB", {"usuario": {USUARIO_KEPT}, "cita": {CITA_KEPT}}, log),
            _Lister("SUPABASE", {"usuario": {USUARIO_KEPT}}, log),
        ],
        tombstones=_Tombstones(log),
    )

    detector.run(now=NOW)

    first_source = min(i for i, entry in enumerate(log.entries) if entry[0] == "source")
    last_replica = max(i for i, entry in enumerate(log.entries) if entry[0] == "replica")
    assert last_replica < first_source


def test_an_id_held_by_only_one_destination_is_still_a_hard_delete() -> None:
    log = _Log()
    tombstones = _Tombstones(log)
    detector = HardDeleteDetector(
        source=_Source({}, log),
        replicas=[
            _Lister("MONGODB", {"usuario": {USUARIO_GONE}}, log),
            _Lister("SUPABASE", {"usuario": set()}, log),
        ],
        tombstones=tombstones,
    )

    detector.run(now=NOW)

    assert tombstones.calls == [("usuario", USUARIO_GONE, NOW)]


def test_an_orphan_held_by_every_destination_is_tombstoned_once() -> None:
    log = _Log()
    tombstones = _Tombstones(log)
    detector = HardDeleteDetector(
        source=_Source({}, log),
        replicas=[
            _Lister("MONGODB", {"usuario": {USUARIO_GONE}}, log),
            _Lister("SUPABASE", {"usuario": {USUARIO_GONE}}, log),
        ],
        tombstones=tombstones,
    )

    report = detector.run(now=NOW)

    assert len(tombstones.calls) == 1
    assert report.orphans == 1


def test_an_already_tombstoned_orphan_is_counted_but_not_tombstoned_again() -> None:
    log = _Log()
    tombstones = _Tombstones(log, already=frozenset({USUARIO_GONE}))
    detector = HardDeleteDetector(
        source=_Source({}, log),
        replicas=[_Lister("MONGODB", {"usuario": {USUARIO_GONE}}, log)],
        tombstones=tombstones,
    )

    report = detector.run(now=NOW)

    assert report.orphans == 1
    assert report.tombstones == 0


def test_without_any_replica_target_nothing_is_read_or_written() -> None:
    log = _Log()
    tombstones = _Tombstones(log)
    detector = HardDeleteDetector(
        source=_Source({"usuario": {USUARIO_GONE}}, log), replicas=[], tombstones=tombstones
    )

    report = detector.run(now=NOW)

    assert log.entries == []
    assert tombstones.calls == []
    assert report.orphans == 0
    assert report.tombstones == 0


def test_a_failed_replica_listing_writes_no_tombstone() -> None:
    log = _Log()
    tombstones = _Tombstones(log)
    detector = HardDeleteDetector(
        source=_Source({}, log),
        replicas=[_Lister("MONGODB", {"usuario": {USUARIO_GONE}}, log), _Failing()],
        tombstones=tombstones,
    )

    with pytest.raises(RuntimeError):
        detector.run(now=NOW)

    assert tombstones.calls == []


def test_a_replica_with_no_ids_skips_the_source_read() -> None:
    log = _Log()
    detector = HardDeleteDetector(
        source=_Source({"usuario": {USUARIO_KEPT}}, log),
        replicas=[_Lister("MONGODB", {}, log)],
        tombstones=_Tombstones(log),
    )

    report = detector.run(now=NOW)

    assert not [entry for entry in log.entries if entry[0] == "source"]
    assert report.orphans == 0
