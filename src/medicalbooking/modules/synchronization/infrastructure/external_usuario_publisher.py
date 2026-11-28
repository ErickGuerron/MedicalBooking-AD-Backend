"""SQL Server publisher for user changes: queue ``usuario.actualizado`` events.

It also advances the users watermark.

Every change becomes one outbox event, numbered by the shared outbox helper, so it follows the same
path as an API write. The replica projection applies it with the handler of ``usuario.creado``, an
upsert of the account state, the roles and the patient identity. The watermark advance runs in the
same transaction, so a batch is never half queued.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine

from medicalbooking.modules.synchronization.infrastructure.changed_usuarios import UsuarioChange
from medicalbooking.modules.synchronization.infrastructure.watermark_store import advance_in
from medicalbooking.shared.infrastructure.outbox import append_outbox
from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock

_AGGREGATE_USUARIO = "usuario"
_EVENT_USUARIO_ACTUALIZADO = "usuario.actualizado"
TABLE_USUARIOS = "usuarios"


class SqlUsuarioOutboxPublisher:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, table: str, changes: Sequence[UsuarioChange], watermark: int) -> None:
        if table != TABLE_USUARIOS:
            raise ValueError(f"no user publisher for table {table!r}")

        def work() -> None:
            now = datetime.now(UTC).replace(tzinfo=None)
            with self._engine.begin() as conn:
                for change in changes:
                    append_outbox(
                        conn,
                        _AGGREGATE_USUARIO,
                        change.payload["usuario"]["id"],
                        _EVENT_USUARIO_ACTUALIZADO,
                        change.payload,
                        now,
                    )
                advance_in(conn, table, watermark)

        retry_on_deadlock(work)
