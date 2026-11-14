"""SQL Server store for patient notifications.

Claims lease rows by moving ``siguiente_intento_en`` past the lease end inside the claiming
transaction. The row lock (UPDLOCK, READPAST) only covers that short transaction, so no lock is
held while the provider is called; the moved timestamp keeps other claimants away afterwards.
If a worker crashes, the lease simply expires and the row becomes due again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    NewNotification,
    PatientContact,
)
from medicalbooking.shared.infrastructure.sql_errors import is_unique_violation

# The patient behind each aggregate that can produce a notification.
_CONTACT_SQL = {
    "cita": (
        "SELECT u.id, u.email FROM citas c JOIN pacientes p ON p.id = c.paciente_id "
        "JOIN usuarios u ON u.id = p.usuario_id WHERE c.id = :id"
    ),
    "orden_laboratorio": (
        "SELECT u.id, u.email FROM ordenes_laboratorio o JOIN pacientes p ON p.id = o.paciente_id "
        "JOIN usuarios u ON u.id = p.usuario_id WHERE o.id = :id"
    ),
}


def _utc_naive(moment: datetime) -> datetime:
    """Store UTC as naive datetime2, the convention used by the rest of the schema."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


class SqlNotificationStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---------- producer side: NotificationRepository ----------

    def find_patient_contact(
        self, aggregate_type: str, aggregate_id: UUID
    ) -> PatientContact | None:
        sql = _CONTACT_SQL.get(aggregate_type)
        if sql is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(text(sql), {"id": str(aggregate_id)}).first()
        if row is None:
            return None
        return PatientContact(user_id=str(uuid.UUID(str(row.id))), email=row.email)

    def enqueue(self, notification: NewNotification, now: datetime) -> bool:
        moment = _utc_naive(now)
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO notificaciones (id, usuario_id, evento_origen_id, tipo, "
                        "destinatario, estado, clave_idempotencia, numero_intentos, "
                        "siguiente_intento_en, creada_en) VALUES (:id, :usuario, :evento, :tipo, "
                        ":destinatario, 'PENDIENTE', :clave, 0, :ahora, :ahora)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "usuario": notification.usuario_id,
                        "evento": str(notification.event_id),
                        "tipo": notification.tipo,
                        "destinatario": notification.destinatario,
                        "clave": notification.key,
                        "ahora": moment,
                    },
                )
        except IntegrityError as error:
            # Only the idempotency key (unique) is an expected answer: the event was seen before.
            if not is_unique_violation(str(error.orig)):
                raise
            return False
        return True

    # ---------- sender side: NotificationQueue ----------

    def claim_due(self, now: datetime, lease_until: datetime, limit: int) -> list[DueNotification]:
        moment = _utc_naive(now)
        lease = _utc_naive(lease_until)
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT TOP (:limite) id, tipo, destinatario, numero_intentos "
                    "FROM notificaciones WITH (UPDLOCK, READPAST, ROWLOCK) "
                    "WHERE estado = 'PENDIENTE' AND siguiente_intento_en <= :ahora "
                    "ORDER BY siguiente_intento_en"
                ),
                {"limite": limit, "ahora": moment},
            ).all()
            for row in rows:
                conn.execute(
                    text("UPDATE notificaciones SET siguiente_intento_en = :lease WHERE id = :id"),
                    {"lease": lease, "id": row.id},
                )
        return [
            DueNotification(
                id=str(uuid.UUID(str(row.id))),
                tipo=row.tipo,
                destinatario=row.destinatario,
                numero_intentos=row.numero_intentos,
            )
            for row in rows
        ]

    def mark_sent(
        self, notification_id: str, provider_message_id: str | None, now: datetime
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE notificaciones SET estado = 'ENVIADA', "
                    "numero_intentos = numero_intentos + 1, id_mensaje_externo = :mensaje, "
                    "enviada_en = :ahora, ultimo_error_codigo = NULL, siguiente_intento_en = NULL "
                    "WHERE id = :id"
                ),
                {
                    "mensaje": provider_message_id,
                    "ahora": _utc_naive(now),
                    "id": notification_id,
                },
            )

    def mark_attempt_failed(
        self,
        notification_id: str,
        attempts: int,
        error_code: str,
        next_attempt_at: datetime | None,
        now: datetime,
    ) -> None:
        # The state follows from the schedule: no next attempt means the notification is FALLIDA.
        estado = "FALLIDA" if next_attempt_at is None else "PENDIENTE"
        next_at = None if next_attempt_at is None else _utc_naive(next_attempt_at)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE notificaciones SET estado = :estado, numero_intentos = :intentos, "
                    "ultimo_error_codigo = :codigo, siguiente_intento_en = :proximo WHERE id = :id"
                ),
                {
                    "estado": estado,
                    "intentos": attempts,
                    "codigo": error_code,
                    "proximo": next_at,
                    "id": notification_id,
                },
            )
