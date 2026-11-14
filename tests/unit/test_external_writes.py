"""Watermark sync cycle with in-memory fakes: batching, watermark order, failure safety."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from medicalbooking.modules.synchronization.application.external_writes import run_cycle
from medicalbooking.modules.synchronization.infrastructure.changed_citas import CitaChange
from medicalbooking.modules.synchronization.infrastructure.external_write_publisher import (
    SqlOutboxPublisher,
)


class FakeReader:
    """Serves changes with version above the watermark, oldest first, like the SQL reader."""

    def __init__(self, versions: list[int]) -> None:
        self._changes = [CitaChange(version=v, cita={"id": str(v)}) for v in sorted(versions)]
        self.calls: list[tuple[int, int]] = []

    def changes_since(self, watermark: int, limit: int) -> list[CitaChange]:
        self.calls.append((watermark, limit))
        return [c for c in self._changes if c.version > watermark][:limit]


class FakeStore:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def get(self, table: str) -> int:
        return self.values.get(table, 0)


class FakePublisher:
    """Records each batch and commits the watermark with it, as the SQL publisher does."""

    def __init__(self, store: FakeStore, fail_on_call: int | None = None) -> None:
        self._store = store
        self._fail_on_call = fail_on_call
        self.batches: list[tuple[str, list[int], int]] = []

    def publish(self, table: str, changes: Sequence[CitaChange], watermark: int) -> None:
        if len(self.batches) + 1 == self._fail_on_call:
            raise RuntimeError("queue unavailable")
        self.batches.append((table, [c.version for c in changes], watermark))
        self._store.values[table] = watermark


def test_no_pending_changes_queues_nothing() -> None:
    store = FakeStore()
    publisher = FakePublisher(store)

    queued = run_cycle(FakeReader([]), store, publisher, table="citas", batch_size=10)

    assert queued == 0
    assert publisher.batches == []


def test_one_short_batch_is_published_with_its_highest_version() -> None:
    store = FakeStore()
    publisher = FakePublisher(store)

    queued = run_cycle(FakeReader([3, 7]), store, publisher, table="citas", batch_size=10)

    assert queued == 2
    assert publisher.batches == [("citas", [3, 7], 7)]
    assert store.values["citas"] == 7


def test_full_batches_continue_from_the_advanced_watermark() -> None:
    store = FakeStore()
    reader = FakeReader([1, 2, 3, 4, 5])
    publisher = FakePublisher(store)

    queued = run_cycle(reader, store, publisher, table="citas", batch_size=2)

    assert queued == 5
    assert [batch[1] for batch in publisher.batches] == [[1, 2], [3, 4], [5]]
    # The short last batch stops the cycle, so the reader is asked three times, not four.
    assert reader.calls == [(0, 2), (2, 2), (4, 2)]


def test_failed_batch_leaves_the_watermark_where_it_was() -> None:
    store = FakeStore()
    store.values["citas"] = 2
    publisher = FakePublisher(store, fail_on_call=1)

    with pytest.raises(RuntimeError, match="queue unavailable"):
        run_cycle(FakeReader([3, 4]), store, publisher, table="citas", batch_size=10)

    assert store.values["citas"] == 2


def test_second_cycle_after_a_completed_one_queues_nothing_new() -> None:
    store = FakeStore()
    publisher = FakePublisher(store)
    reader = FakeReader([1, 2])

    first = run_cycle(reader, store, publisher, table="citas", batch_size=10)
    second = run_cycle(reader, store, publisher, table="citas", batch_size=10)

    assert (first, second) == (2, 0)


def test_non_positive_batch_size_is_rejected() -> None:
    store = FakeStore()

    with pytest.raises(ValueError, match="positive"):
        run_cycle(FakeReader([]), store, FakePublisher(store), table="citas", batch_size=0)


def test_publisher_rejects_tables_it_cannot_project_before_touching_the_database() -> None:
    publisher = SqlOutboxPublisher(engine=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="no publisher"):
        publisher.publish("usuarios", [], 0)
