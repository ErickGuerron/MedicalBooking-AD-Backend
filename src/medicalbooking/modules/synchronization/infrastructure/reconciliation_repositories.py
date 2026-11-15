"""SQL Server adapter for replica reconciliation. Parameterized SQL only.

The requeue changes only a delivery in state ENTREGADO, or inserts the missing delivery row.
Rows in ERROR_TEMPORAL, ERROR_PERMANENTE or PENDIENTE are left to the dispatcher. The insert uses
the same UPDLOCK/HOLDLOCK probe as the dispatcher's materialization, so both cannot create the
same (evento_id, destino_id) row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, TextClause, bindparam, text

from medicalbooking.modules.synchronization.domain.reconciliation import (
    DELETED_EVENT_TYPES,
    TOMBSTONE_EVENT_TYPES,
    TRACKED_AGGREGATE_TYPES,
    LatestEvent,
)
from medicalbooking.shared.infrastructure.outbox import append_outbox
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

# Every id of the source table of each tracked aggregate. The caller reads replica ids before these,
# so a row created in between is never mistaken for a hard delete.
_SOURCE_IDS: dict[str, TextClause] = {
    "usuario": text("SELECT id FROM usuarios"),
    "cita": text("SELECT id FROM citas"),
}

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


# Any delete event already announcing this aggregate. The UPDLOCK/HOLDLOCK range lock also
# serializes a concurrent writer that takes the next version of the same aggregate.
_DELETE_EVENT_PROBE = text(
    "SELECT TOP (1) 1 FROM eventos_outbox WITH (UPDLOCK, HOLDLOCK) "
    "WHERE agregado_tipo = :tipo AND agregado_id = :agregado AND tipo_evento IN :eliminados"
).bindparams(bindparam("eliminados", expanding=True))


def _lower_guid(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the reconciliation compares lower-case text."""
    return str(uuid.UUID(str(value)))


def _canonical_guid(value: str) -> str | None:
    """Lower-case canonical GUID text, or None when the value is not a GUID."""
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


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

    def tombstone(self, aggregate_type: str, aggregate_id: str, now: datetime) -> bool:
        """Append the delete event of an aggregate whose source row is gone; True when appended.

        The event takes the shared outbox helper's next version, so it follows the aggregate's
        latest event. An aggregate that already has a delete event is left alone. A value that is
        not a GUID cannot name a source row and is skipped.
        """
        if aggregate_type not in TOMBSTONE_EVENT_TYPES:
            raise ValueError(f"no tombstone event for aggregate type {aggregate_type!r}")
        canonical = _canonical_guid(aggregate_id)
        if canonical is None:
            return False

        def work() -> bool:
            with self._engine.begin() as conn:
                existing = conn.execute(
                    _DELETE_EVENT_PROBE,
                    {
                        "tipo": aggregate_type,
                        "agregado": canonical,
                        "eliminados": sorted(DELETED_EVENT_TYPES),
                    },
                ).first()
                if existing is not None:
                    return False
                append_outbox(
                    conn,
                    aggregate_type,
                    canonical,
                    TOMBSTONE_EVENT_TYPES[aggregate_type],
                    {aggregate_type: {"id": canonical}},
                    _utc_naive(now),
                )
                return True

        return retry_on_deadlock(work)

    def source_ids(self, aggregate_type: str) -> frozenset[str]:
        """Every id of the source table of a tracked aggregate, as lower-case text."""
        if aggregate_type not in _SOURCE_IDS:
            raise ValueError(f"no source table for aggregate type {aggregate_type!r}")
        with self._engine.connect() as conn:
            rows = conn.execute(_SOURCE_IDS[aggregate_type]).all()
        return frozenset(_lower_guid(row[0]) for row in rows)

    def requeue(self, event_id: str, destination_code: str) -> bool:
        """Return True when a row changed. A PENDIENTE or dispatcher-owned row returns False."""

        def work() -> bool:
            params = {"evento": event_id, "codigo": destination_code}
            with self._engine.begin() as conn:
                if conn.execute(_REQUEUE, params).all():
                    return True
                return bool(conn.execute(_INSERT_MISSING, params).all())

        return retry_on_deadlock(work)
