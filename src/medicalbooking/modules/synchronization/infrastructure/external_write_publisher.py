"""SQL Server publisher for the watermark sync: queue cita snapshots and advance the watermark.

Every change becomes one ``cita.actualizada`` outbox event, numbered by the shared outbox helper, so
it follows the same path as an API write. The watermark advance runs in the same transaction, so a
batch is never half queued.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine

from medicalbooking.modules.synchronization.infrastructure.changed_citas import CitaChange
from medicalbooking.modules.synchronization.infrastructure.watermark_store import advance_in
from medicalbooking.shared.infrastructure.outbox import append_outbox
from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock

_AGGREGATE_CITA = "cita"
_EVENT_CITA = "cita.actualizada"


class SqlOutboxPublisher:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, table: str, changes: Sequence[CitaChange], watermark: int) -> None:
        if table != "citas":
            raise ValueError(f"no publisher for table {table!r}")

        def work() -> None:
            now = datetime.now(UTC).replace(tzinfo=None)
            with self._engine.begin() as conn:
                for change in changes:
                    append_outbox(
                        conn,
                        _AGGREGATE_CITA,
                        change.cita["id"],
                        _EVENT_CITA,
                        {"cita": change.cita},
                        now,
                    )
                advance_in(conn, table, watermark)

        retry_on_deadlock(work)
