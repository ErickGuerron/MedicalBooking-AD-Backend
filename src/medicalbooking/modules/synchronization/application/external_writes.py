"""One watermark sync cycle: queue the rows written outside the API, batch by batch.

Each batch is published together with the watermark it covers. The publisher commits both in one
transaction, so a crash either keeps the whole batch and the advance, or neither. The worker
writes no replica itself: queued events reach Supabase and MongoDB through the outbox dispatcher,
which keeps retrying while a replica is down.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Versioned(Protocol):
    """A changed row: the watermark version it carries (citas and usuarios both comply)."""

    @property
    def version(self) -> int: ...


class ChangeReader[ChangeT: Versioned](Protocol):
    def changes_since(self, watermark: int, limit: int) -> list[ChangeT]: ...


class WatermarkReader(Protocol):
    def get(self, table: str) -> int: ...


class BatchPublisher[ChangeT: Versioned](Protocol):
    def publish(self, table: str, changes: Sequence[ChangeT], watermark: int) -> None:
        """Queue the batch and raise the watermark to ``watermark``, in one transaction."""


def run_cycle[ChangeT: Versioned](
    reader: ChangeReader[ChangeT],
    watermarks: WatermarkReader,
    publisher: BatchPublisher[ChangeT],
    *,
    table: str,
    batch_size: int,
) -> int:
    """Publish every pending change of ``table`` and return how many were queued.

    Stops at the first short batch. An error in the publisher propagates: the watermark for the
    failed batch did not move, so the next cycle reads the same rows again.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    queued = 0
    while True:
        changes = reader.changes_since(watermarks.get(table), batch_size)
        if not changes:
            return queued
        publisher.publish(table, changes, max(change.version for change in changes))
        queued += len(changes)
        if len(changes) < batch_size:
            return queued
