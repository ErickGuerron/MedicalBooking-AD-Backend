"""SQL Server adapter for the outbox dispatcher. Parameterized SQL only."""

from __future__ import annotations

from sqlalchemy import Engine, text

from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock

# The key-range hint on the NOT EXISTS probe makes two concurrent materializations
# serialize instead of racing into the UQ (evento_id, destino_id).
_MATERIALIZE = text(
    "INSERT INTO entregas_eventos (id, evento_id, destino_id, estado, intentos) "
    "SELECT NEWID(), ev.id, d.id, 'PENDIENTE', 0 "
    "FROM eventos_outbox AS ev "
    "CROSS JOIN destinos_sincronizacion AS d "
    "WHERE d.activo = 1 "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM entregas_eventos AS en WITH (UPDLOCK, HOLDLOCK) "
    "  WHERE en.evento_id = ev.id AND en.destino_id = d.id)"
)


class SqlOutboxStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def materialize(self) -> int:
        def work() -> int:
            with self._engine.begin() as conn:
                return conn.execute(_MATERIALIZE).rowcount

        return retry_on_deadlock(work)
