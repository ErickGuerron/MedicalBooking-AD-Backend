"""SQL Server adapter for replica reconciliation. Parameterized SQL only.

The requeue changes only a delivery in state ENTREGADO, or inserts the missing delivery row.
Rows in ERROR_TEMPORAL, ERROR_PERMANENTE or PENDIENTE are left to the dispatcher. The insert uses
the same UPDLOCK/HOLDLOCK probe as the dispatcher's materialization, so both cannot create the
same (evento_id, destino_id) row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, bindparam, text

from medicalbooking.modules.synchronization.domain.reconciliation import (
    TRACKED_AGGREGATE_TYPES,
    LatestEvent,
)
from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock

# The newest event of each aggregate, when that event was created at or after the cutoff.
_LATEST_EVENTS = text(
    "SELECT ev.id, ev.agregado_tipo, ev.agregado_id, ev.tipo_evento "
    "FROM eventos_outbox AS ev "
    "JOIN ("
    "  SELECT agregado_tipo, agregado_id, MAX(version_agregado) AS version_max "
    "  FROM eventos_outbox "
    "  WHERE agregado_tipo IN :tipos "
    "  GROUP BY agregado_tipo, agregado_id"
    ") AS ultima "
    "ON ultima.agregado_tipo = ev.agregado_tipo "
    "AND ultima.agregado_id = ev.agregado_id "
    "AND ultima.version_max = ev.version_agregado "
    "WHERE ev.creado_en >= :desde"
).bindparams(bindparam("tipos", expanding=True))

_REQUEUE = text(
    "UPDATE entregas_eventos SET estado = 'PENDIENTE', intentos = 0, proximo_intento = NULL, "
    "bloqueado_hasta = NULL, ultimo_error = NULL, entregado_en = NULL "
    "OUTPUT INSERTED.id "
    "WHERE evento_id = :evento AND estado = 'ENTREGADO' "
    "AND destino_id = (SELECT id FROM destinos_sincronizacion WHERE codigo = :codigo)"
)

_INSERT_MISSING = text(
    "INSERT INTO entregas_eventos (id, evento_id, destino_id, estado, intentos) "
    "OUTPUT INSERTED.id "
    "SELECT NEWID(), :evento, d.id, 'PENDIENTE', 0 "
    "FROM destinos_sincronizacion AS d "
    "WHERE d.codigo = :codigo AND d.activo = 1 "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM entregas_eventos AS en WITH (UPDLOCK, HOLDLOCK) "
    "  WHERE en.evento_id = :evento AND en.destino_id = d.id)"
)


def _lower_guid(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the reconciliation compares lower-case text."""
    return str(uuid.UUID(str(value)))


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


class SqlReconciliationStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def latest_events(self, since: datetime) -> list[LatestEvent]:
        """``since`` must be timezone-aware; it is compared with the UTC creation time."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                _LATEST_EVENTS,
                {
                    "tipos": sorted(TRACKED_AGGREGATE_TYPES),
                    "desde": _utc_naive(since),
                },
            ).all()
        return [
            LatestEvent(
                event_id=_lower_guid(row.id),
                aggregate_type=row.agregado_tipo,
                aggregate_id=_lower_guid(row.agregado_id),
                event_type=row.tipo_evento,
            )
            for row in rows
        ]

    def requeue(self, event_id: str, destination_code: str) -> bool:
        """Return True when a row changed. A PENDIENTE or dispatcher-owned row returns False."""

        def work() -> bool:
            params = {"evento": event_id, "codigo": destination_code}
            with self._engine.begin() as conn:
                if conn.execute(_REQUEUE, params).all():
                    return True
                return bool(conn.execute(_INSERT_MISSING, params).all())

        return retry_on_deadlock(work)
