"""SQL Server adapter for the outbox dispatcher. Parameterized SQL only.

Claims use UPDLOCK/READPAST so concurrent dispatchers skip each other's rows instead of
waiting on them. The claim transaction only sets the lease; the handler runs after it
commits, and the completion is fenced on the lease value (``bloqueado_hasta``).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, bindparam, text

from medicalbooking.modules.synchronization.application.ports import (
    DeliveryClaim,
    DeliveryOutcome,
)
from medicalbooking.modules.synchronization.domain.events import OutboxEvent
from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock

# The key-range hint on the NOT EXISTS probe makes two concurrent materializations
# serialize instead of racing into the UQ (evento_id, destino_id).
# OUTPUT returns one row per insert, so the count does not depend on the driver's rowcount,
# which pyodbc reports as -1 when the server does not send a count.
_MATERIALIZE = text(
    "INSERT INTO entregas_eventos (id, evento_id, destino_id, estado, intentos) "
    "OUTPUT INSERTED.id "
    "SELECT NEWID(), ev.id, d.id, 'PENDIENTE', 0 "
    "FROM eventos_outbox AS ev "
    "CROSS JOIN destinos_sincronizacion AS d "
    "WHERE d.activo = 1 "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM entregas_eventos AS en WITH (UPDLOCK, HOLDLOCK) "
    "  WHERE en.evento_id = ev.id AND en.destino_id = d.id)"
)

# A delivery is claimable only when no earlier event of the same aggregate and destination
# is still pending, awaiting a retry, or leased by a live dispatcher.
_CLAIM = text(
    "SELECT TOP (:limite) en.id AS entrega_id, en.intentos, d.codigo AS destino_codigo, "
    "ev.id AS evento_id, ev.agregado_tipo, ev.agregado_id, ev.tipo_evento, "
    "ev.version_agregado, ev.creado_en, ev.correlation_id, ev.payload "
    "FROM entregas_eventos AS en WITH (UPDLOCK, READPAST, ROWLOCK) "
    "JOIN eventos_outbox AS ev ON ev.id = en.evento_id "
    "JOIN destinos_sincronizacion AS d ON d.id = en.destino_id "
    "WHERE d.codigo IN :codigos "
    "AND en.estado IN ('PENDIENTE', 'ERROR_TEMPORAL') "
    "AND (en.proximo_intento IS NULL OR en.proximo_intento <= :ahora) "
    "AND (en.bloqueado_hasta IS NULL OR en.bloqueado_hasta <= :ahora) "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM eventos_outbox AS anterior "
    "  JOIN entregas_eventos AS previa "
    "    ON previa.evento_id = anterior.id AND previa.destino_id = en.destino_id "
    "  WHERE anterior.agregado_tipo = ev.agregado_tipo "
    "  AND anterior.agregado_id = ev.agregado_id "
    "  AND anterior.version_agregado < ev.version_agregado "
    "  AND (previa.estado IN ('PENDIENTE', 'ERROR_TEMPORAL') "
    "       OR previa.bloqueado_hasta > :ahora)) "
    "ORDER BY ev.agregado_tipo, ev.agregado_id, ev.version_agregado"
).bindparams(bindparam("codigos", expanding=True))

_LEASE = text("UPDATE entregas_eventos SET bloqueado_hasta = :hasta WHERE id = :id")

_COMPLETE = text(
    "UPDATE entregas_eventos SET estado = :estado, intentos = :intentos, "
    "proximo_intento = :proximo, bloqueado_hasta = NULL, ultimo_error = :error, "
    "entregado_en = :entregado "
    "WHERE id = :id AND bloqueado_hasta = :lease"
)


def _canonical(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the application uses lower case."""
    return str(uuid.UUID(str(value)))


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


class SqlOutboxStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def materialize(self) -> int:
        def work() -> int:
            with self._engine.begin() as conn:
                return len(conn.execute(_MATERIALIZE).all())

        return retry_on_deadlock(work)

    def claim_due(
        self,
        now: datetime,
        lease_until: datetime,
        destination_codes: tuple[str, ...],
        limit: int,
    ) -> list[DeliveryClaim]:
        def work() -> list[DeliveryClaim]:
            with self._engine.begin() as conn:
                rows = conn.execute(
                    _CLAIM,
                    {
                        "limite": limit,
                        "codigos": list(destination_codes),
                        "ahora": _utc_naive(now),
                    },
                ).all()
                if not rows:
                    return []
                lease = _utc_naive(lease_until)
                conn.execute(
                    _LEASE,
                    [{"hasta": lease, "id": row.entrega_id} for row in rows],
                )
            return [_claim(row, lease_until) for row in rows]

        return retry_on_deadlock(work)

    def complete(self, claim: DeliveryClaim, outcome: DeliveryOutcome, now: datetime) -> bool:
        def work() -> bool:
            with self._engine.begin() as conn:
                result = conn.execute(
                    _COMPLETE,
                    {
                        "estado": outcome.status,
                        "intentos": outcome.attempts,
                        "proximo": _utc_naive(outcome.next_attempt_at)
                        if outcome.next_attempt_at
                        else None,
                        "error": outcome.error_code,
                        "entregado": _utc_naive(now) if outcome.status == "ENTREGADO" else None,
                        "id": claim.delivery_id,
                        "lease": _utc_naive(claim.lease_until),
                    },
                )
                return result.rowcount == 1

        return retry_on_deadlock(work)


def _claim(row, lease_until: datetime) -> DeliveryClaim:
    correlation = row.correlation_id
    event = OutboxEvent(
        id=uuid.UUID(_canonical(row.evento_id)),
        aggregate_type=row.agregado_tipo,
        aggregate_id=uuid.UUID(_canonical(row.agregado_id)),
        event_type=row.tipo_evento,
        aggregate_version=int(row.version_agregado),
        created_at=row.creado_en.replace(tzinfo=UTC),
        correlation_id=None if correlation is None else uuid.UUID(_canonical(correlation)),
        payload=json.loads(row.payload),
    )
    return DeliveryClaim(
        delivery_id=_canonical(row.entrega_id),
        destination_code=row.destino_codigo,
        attempts=int(row.intentos),
        lease_until=lease_until,
        event=event,
    )
