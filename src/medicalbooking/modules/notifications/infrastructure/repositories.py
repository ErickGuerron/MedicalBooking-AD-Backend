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
    CitaCancellation,
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
    "pago": (
        "SELECT u.id, u.email FROM pagos g JOIN facturas f ON f.id = g.factura_id "
        "JOIN pacientes p ON p.id = f.paciente_id JOIN usuarios u ON u.id = p.usuario_id "
        "WHERE g.id = :id"
    ),
}


# The doctor of an appointment (its agenda's doctor), who receives the doctor's copy.
_DOCTOR_CONTACT_SQL = {
    "cita": (
        "SELECT um.id, um.email FROM citas c "
        "JOIN horarios_disponibles h ON h.id = c.horario_id "
        "JOIN agendas a ON a.id = h.agenda_id "
        "JOIN medicos me ON me.id = a.medico_id "
        "JOIN usuarios um ON um.id = me.usuario_id WHERE c.id = :id"
    ),
}


def _utc_naive(moment: datetime) -> datetime:
    """Store UTC as naive datetime2, the convention used by the rest of the schema."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


class SqlNotificationStore:
    def patient_first_name(self, email: str) -> str | None:
        with self._engine.connect() as conn:
            return conn.execute(
                text("SELECT nombre FROM usuarios WHERE email = :e"), {"e": email}
            ).scalar()

    def cita_cancellation(self, cita_id: str) -> CitaCancellation | None:
        """Who cancelled the appointment (its last CANCELADA change) and the stored reason."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT TOP 1 c.motivo_cancelacion_codigo, c.motivo_cancelacion_texto, "
                    "CASE WHEN hc.usuario_id = um.id THEN 1 ELSE 0 END AS por_medico, "
                    "c.reembolso_automatico AS reembolso_resultado, "
                    "(SELECT TOP 1 p.monto FROM pagos AS p JOIN facturas AS f ON f.id = p.factura_id "
                    "WHERE f.cita_id = c.id AND p.estado = 'CONFIRMADO' ORDER BY p.creado_en DESC) AS monto_pagado "
                    "FROM citas c "
                    "JOIN horarios_disponibles h ON h.id = c.horario_id "
                    "JOIN agendas a ON a.id = h.agenda_id "
                    "JOIN medicos me ON me.id = a.medico_id "
                    "JOIN usuarios um ON um.id = me.usuario_id "
                    "OUTER APPLY (SELECT TOP 1 x.usuario_id FROM historial_estados_cita x "
                    "WHERE x.cita_id = c.id AND x.estado_nuevo = 'CANCELADA' "
                    "ORDER BY x.cambiado_en DESC) hc "
                    "WHERE c.id = :c"
                ),
                {"c": cita_id},
            ).first()
        if row is None:
            return None
        return CitaCancellation(
            por_medico=bool(row.por_medico),
            motivo_codigo=row.motivo_cancelacion_codigo,
            motivo_texto=row.motivo_cancelacion_texto,
            monto_pagado=row.monto_pagado,
            reembolso_resultado=row.reembolso_resultado,
        )

    def cita_patient_name(self, cita_id: str) -> str | None:
        """The patient's full name of an appointment, for the doctor's copy."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT u.nombre, u.apellido FROM citas c "
                    "JOIN pacientes p ON p.id = c.paciente_id "
                    "JOIN usuarios u ON u.id = p.usuario_id WHERE c.id = :c"
                ),
                {"c": cita_id},
            ).first()
        if row is None:
            return None
        return f"{row.nombre} {row.apellido}".strip()

    def cita_reminder(self, cita_id: str) -> tuple[str, datetime] | None:
        """The doctor's full name and the start (UTC) of an appointment, for the reminder."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT um.nombre, um.apellido, h.inicio_utc FROM citas c "
                    "JOIN horarios_disponibles h ON h.id = c.horario_id "
                    "JOIN agendas a ON a.id = h.agenda_id "
                    "JOIN medicos me ON me.id = a.medico_id "
                    "JOIN usuarios um ON um.id = me.usuario_id WHERE c.id = :c"
                ),
                {"c": cita_id},
            ).first()
        if row is None:
            return None
        return f"{row.nombre} {row.apellido}".strip(), row.inicio_utc

    def paid_invoice(self, pago_id: str) -> tuple[str, str] | None:
        """The invoice number and its total with currency, for the payment confirmation."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT f.numero_factura, f.total, f.moneda FROM pagos g "
                    "JOIN facturas f ON f.id = g.factura_id WHERE g.id = :p"
                ),
                {"p": pago_id},
            ).first()
        if row is None or not row.numero_factura:
            return None
        return row.numero_factura, f"{row.total:.2f} {row.moneda.strip()}"

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

    def find_doctor_contact(self, aggregate_type: str, aggregate_id: UUID) -> PatientContact | None:
        sql = _DOCTOR_CONTACT_SQL.get(aggregate_type)
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
                    "SELECT TOP (:limite) n.id, n.tipo, n.destinatario, n.numero_intentos, "
                    "e.agregado_id FROM notificaciones n WITH (UPDLOCK, READPAST, ROWLOCK) "
                    "LEFT JOIN eventos_outbox e ON e.id = n.evento_origen_id "
                    "WHERE n.estado = 'PENDIENTE' AND n.siguiente_intento_en <= :ahora "
                    "ORDER BY n.siguiente_intento_en"
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
                aggregate_id=None
                if row.agregado_id is None
                else str(uuid.UUID(str(row.agregado_id))),
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
