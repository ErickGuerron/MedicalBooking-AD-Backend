"""SQL Server adapter for the scheduling ports.

Each reservation and cancellation runs in one transaction: the conditional UPDATE on the
slot is the first guard, the filtered unique index is the second, and the history and
outbox rows commit with the appointment or not at all. Parameterized SQL only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.scheduling.application.ports import (
    Appointment,
    AppointmentSummary,
    AvailableSlot,
    CancelCommand,
    CancelOutcome,
    CancelStatus,
    ReserveCommand,
    ReserveOutcome,
    ReserveStatus,
)
from medicalbooking.modules.scheduling.domain.policies import ACTIVE_STATES
from medicalbooking.shared.infrastructure.outbox import append_outbox
from medicalbooking.shared.infrastructure.sql_errors import is_unique_violation, retry_on_deadlock

_BOOKED = "PENDIENTE_PAGO"
_CANCELLED = "CANCELADA"


def _canonical(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the API uses lower case. Normalize both ways."""
    return str(uuid.UUID(str(value)))


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _utc_aware(moment: datetime | None) -> datetime | None:
    return None if moment is None else moment.replace(tzinfo=UTC)


def _appointment(row) -> Appointment:
    return Appointment(
        id=_canonical(row.id),
        key=row.clave_idempotencia,
        patient_user_id=_canonical(row.patient_user_id),
        horario_id=_canonical(row.horario_id),
        servicio_id=_canonical(row.servicio_id),
        estado=row.estado,
        motivo=row.motivo,
        expiracion_reserva=_utc_aware(row.expiracion_reserva),
    )


def _outbox(
    conn: Connection, appointment_id: str, event: str, payload: dict, now: datetime
) -> None:
    """Each event takes the next version of its appointment (the outbox is unique per version)."""
    append_outbox(conn, "cita", appointment_id, event, payload, now)


def emit_cita_snapshot(conn: Connection, cita_id: str, moment: datetime) -> None:
    """Replica snapshot of an appointment and its slot window, written with the change itself.

    The booking's free text, the idempotency key and the row version are never read here, so
    they can never leave SQL Server. The snapshot is read inside the caller's transaction, after
    the change.
    """
    row = conn.execute(
        text(
            "SELECT c.id, c.paciente_id, c.servicio_id, c.estado, c.reservada_en, "
            "c.confirmada_en, c.cancelada_en, c.motivo_cancelacion_codigo, "
            "c.motivo_cancelacion_texto, h.inicio_utc, h.fin_utc "
            "FROM citas AS c JOIN horarios_disponibles AS h ON h.id = c.horario_id "
            "WHERE c.id = :c"
        ),
        {"c": cita_id},
    ).one()
    appointment_id = _canonical(row.id)
    append_outbox(
        conn,
        "cita",
        appointment_id,
        "cita.actualizada",
        {
            "cita": {
                "id": appointment_id,
                "paciente_id": _canonical(row.paciente_id),
                "servicio_id": _canonical(row.servicio_id),
                "estado": row.estado,
                "reservada_en": row.reservada_en.isoformat(),
                "confirmada_en": row.confirmada_en.isoformat() if row.confirmada_en else None,
                "cancelada_en": row.cancelada_en.isoformat() if row.cancelada_en else None,
                "motivo_cancelacion_codigo": row.motivo_cancelacion_codigo,
                "motivo_cancelacion_texto": row.motivo_cancelacion_texto,
                "inicio": row.inicio_utc.isoformat(),
                "fin": row.fin_utc.isoformat(),
            }
        },
        moment,
    )


def _history(
    conn: Connection,
    appointment_id: str,
    actor: str | None,
    before: str | None,
    after: str,
    now: datetime,
) -> None:
    conn.execute(
        text(
            "INSERT INTO historial_estados_cita (id, cita_id, usuario_id, estado_anterior, "
            "estado_nuevo, cambiado_en) VALUES (:id, :cita, :usuario, :antes, :despues, :ahora)"
        ),
        {
            "id": str(uuid.uuid4()),
            "cita": appointment_id,
            "usuario": actor,
            "antes": before,
            "despues": after,
            "ahora": now,
        },
    )


class SqlSchedulingStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_available(self, start, end, medico_id, limit) -> list[AvailableSlot]:
        sql = (
            "SELECT TOP (:limite) h.id, m.id AS medico_id, h.inicio_utc, h.fin_utc "
            "FROM horarios_disponibles h "
            "JOIN agendas a ON a.id = h.agenda_id "
            "JOIN medicos m ON m.id = a.medico_id "
            "WHERE h.estado = 'DISPONIBLE' AND a.activa = 1 AND m.activo = 1 "
            "AND h.inicio_utc >= :inicio AND h.inicio_utc < :fin "
        )
        params: dict[str, object] = {
            "limite": limit,
            "inicio": _utc_naive(start),
            "fin": _utc_naive(end),
        }
        if medico_id is not None:
            sql += "AND m.id = :medico "
            params["medico"] = str(medico_id)
        sql += "ORDER BY h.inicio_utc"
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
        return [
            AvailableSlot(
                horario_id=_canonical(row.id),
                medico_id=_canonical(row.medico_id),
                inicio_utc=_utc_aware(row.inicio_utc),
                fin_utc=_utc_aware(row.fin_utc),
            )
            for row in rows
        ]

    def find_slot(self, horario_id: str) -> datetime | None:
        with self._engine.connect() as conn:
            start = conn.execute(
                text("SELECT inicio_utc FROM horarios_disponibles WHERE id = :h"),
                {"h": horario_id},
            ).scalar()
        return _utc_aware(start)

    def service_is_active(self, servicio_id: str) -> bool:
        with self._engine.connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM servicios_medicos WHERE id = :s AND activo = 1"),
                {"s": servicio_id},
            ).first()
        return found is not None

    def find_appointment_by_key(self, key: str) -> Appointment | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(_APPOINTMENT_SELECT + "WHERE c.clave_idempotencia = :k"), {"k": key}
            ).first()
        return None if row is None else _appointment(row)

    def has_doctor_profile(self, user_id: str) -> bool:
        with self._engine.connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM medicos WHERE usuario_id = :u"), {"u": user_id}
            ).first()
        return found is not None

    def list_patient_appointments(self, patient_user_id, estado, limit, offset):
        return self._list_summaries(_PATIENT_SOURCE, patient_user_id, estado, limit, offset)

    def list_agenda_appointments(self, doctor_user_id, estado, limit, offset):
        return self._list_summaries(_AGENDA_SOURCE, doctor_user_id, estado, limit, offset)

    def _list_summaries(self, source: str, user_id, estado, limit, offset):
        """Only id, estado and slot times leave the database: no patient columns are selected."""
        sql = _SUMMARY_SELECT + source
        params: dict[str, object] = {
            "usuario": str(user_id),
            "saltar": offset,
            "cantidad": limit,
        }
        if estado is not None:
            sql += "AND c.estado = :estado "
            params["estado"] = estado
        sql += "ORDER BY h.inicio_utc, c.id OFFSET :saltar ROWS FETCH NEXT :cantidad ROWS ONLY"
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
        return [
            AppointmentSummary(
                id=_canonical(row.id),
                estado=row.estado,
                inicio_utc=_utc_aware(row.inicio_utc),
                fin_utc=_utc_aware(row.fin_utc),
            )
            for row in rows
        ]

    def reserve(self, command: ReserveCommand) -> ReserveOutcome:
        return retry_on_deadlock(lambda: self._reserve_once(command))

    def _reserve_once(self, command: ReserveCommand) -> ReserveOutcome:
        appointment_id = str(uuid.uuid4())
        try:
            with self._engine.begin() as conn:
                patient = conn.execute(
                    text("SELECT id FROM pacientes WHERE usuario_id = :u"),
                    {"u": command.patient_user_id},
                ).scalar()
                if patient is None:
                    return ReserveOutcome(ReserveStatus.NO_PATIENT_PROFILE, None)
                claimed = conn.execute(
                    text(
                        "UPDATE horarios_disponibles SET estado = 'RESERVADO' "
                        "WHERE id = :h AND estado = 'DISPONIBLE' AND inicio_utc > :ahora"
                    ),
                    {"h": command.horario_id, "ahora": _utc_naive(command.now)},
                ).rowcount
                if claimed != 1:
                    return ReserveOutcome(ReserveStatus.SLOT_UNAVAILABLE, None)
                conn.execute(
                    text(
                        "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, "
                        "motivo, "
                        "reservada_en, expiracion_reserva, clave_idempotencia) "
                        "VALUES (:id, :p, :h, :s, :estado, :motivo, :ahora, :expira, :clave)"
                    ),
                    {
                        "id": appointment_id,
                        "p": patient,
                        "h": command.horario_id,
                        "s": command.servicio_id,
                        "estado": _BOOKED,
                        "motivo": command.motivo,
                        "ahora": _utc_naive(command.now),
                        "expira": _utc_naive(command.hold_until),
                        "clave": command.key,
                    },
                )
                _history(
                    conn,
                    appointment_id,
                    command.patient_user_id,
                    None,
                    _BOOKED,
                    _utc_naive(command.now),
                )
                _outbox(
                    conn,
                    appointment_id,
                    "cita.reservada",
                    {
                        "cita_id": appointment_id,
                        "horario_id": command.horario_id,
                        "estado": _BOOKED,
                    },
                    _utc_naive(command.now),
                )
                emit_cita_snapshot(conn, appointment_id, _utc_naive(command.now))
        except IntegrityError as error:
            # Only a unique conflict (the active-slot index or the idempotency key lost a
            # race) is an expected answer. The transaction was rolled back: nothing was
            # reserved. Any other integrity failure is a defect and must surface as an error.
            if not is_unique_violation(str(error.orig)):
                raise
            return ReserveOutcome(ReserveStatus.SLOT_UNAVAILABLE, None)
        appointment = Appointment(
            id=appointment_id,
            key=command.key,
            patient_user_id=_canonical(command.patient_user_id),
            horario_id=_canonical(command.horario_id),
            servicio_id=_canonical(command.servicio_id),
            estado=_BOOKED,
            motivo=command.motivo,
            expiracion_reserva=command.hold_until,
        )
        return ReserveOutcome(ReserveStatus.CREATED, appointment)

    def cancel(self, command: CancelCommand) -> CancelOutcome:
        return retry_on_deadlock(lambda: self._cancel_once(command))

    def _cancel_once(self, command: CancelCommand) -> CancelOutcome:
        now = _utc_naive(command.now)
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT c.id, c.horario_id, c.servicio_id, c.estado, c.motivo, "
                    "c.clave_idempotencia, c.expiracion_reserva, p.usuario_id AS patient_user_id "
                    "FROM citas c WITH (UPDLOCK, ROWLOCK) "
                    "JOIN pacientes p ON p.id = c.paciente_id WHERE c.id = :id"
                ),
                {"id": command.appointment_id},
            ).first()
            if row is None or not _may_cancel(conn, command, row):
                return CancelOutcome(CancelStatus.NOT_FOUND_OR_FORBIDDEN, None)
            if row.estado not in ACTIVE_STATES:
                return CancelOutcome(CancelStatus.NOT_CANCELLABLE, None)
            updated = conn.execute(
                text(
                    "UPDATE citas SET estado = :nuevo, cancelada_en = :ahora, "
                    "motivo_cancelacion_codigo = :codigo, motivo_cancelacion_texto = :texto "
                    "WHERE id = :id AND estado IN ('PENDIENTE_PAGO', 'CONFIRMADA')"
                ),
                {
                    "nuevo": _CANCELLED,
                    "ahora": now,
                    "codigo": command.motivo_codigo,
                    "texto": command.motivo_texto,
                    "id": command.appointment_id,
                },
            ).rowcount
            if updated != 1:
                return CancelOutcome(CancelStatus.NOT_CANCELLABLE, None)
            conn.execute(
                text(
                    "UPDATE horarios_disponibles SET estado = 'DISPONIBLE' "
                    "WHERE id = :h AND estado = 'RESERVADO'"
                ),
                {"h": row.horario_id},
            )
            _history(
                conn, command.appointment_id, command.actor_user_id, row.estado, _CANCELLED, now
            )
            _outbox(
                conn,
                command.appointment_id,
                "cita.cancelada",
                {
                    "cita_id": command.appointment_id,
                    "horario_id": _canonical(row.horario_id),
                    "estado": _CANCELLED,
                },
                now,
            )
            emit_cita_snapshot(conn, command.appointment_id, now)
        return CancelOutcome(
            CancelStatus.CANCELLED,
            Appointment(
                id=_canonical(row.id),
                key=row.clave_idempotencia,
                patient_user_id=_canonical(row.patient_user_id),
                horario_id=_canonical(row.horario_id),
                servicio_id=_canonical(row.servicio_id),
                estado=_CANCELLED,
                motivo=row.motivo,
                expiracion_reserva=_utc_aware(row.expiracion_reserva),
            ),
        )

    def expire_overdue_holds(self, now: datetime, limit: int) -> int:
        return retry_on_deadlock(lambda: self._expire_once(now, limit))

    def _expire_once(self, now: datetime, limit: int) -> int:
        """Expire one batch. READPAST skips holds another worker already claims."""
        moment = _utc_naive(now)
        with self._engine.begin() as conn:
            overdue = conn.execute(
                text(
                    "SELECT TOP (:limite) c.id, c.horario_id FROM citas c "
                    "WITH (UPDLOCK, READPAST, ROWLOCK) "
                    "WHERE c.estado = :pendiente AND c.expiracion_reserva <= :ahora "
                    "ORDER BY c.expiracion_reserva"
                ),
                {"limite": limit, "pendiente": _BOOKED, "ahora": moment},
            ).all()
            for row in overdue:
                appointment_id = _canonical(row.id)
                expired = conn.execute(
                    text(
                        "UPDATE citas SET estado = 'EXPIRADA' "
                        "WHERE id = :id AND estado = :pendiente AND expiracion_reserva <= :ahora"
                    ),
                    {"id": row.id, "pendiente": _BOOKED, "ahora": moment},
                ).rowcount
                if expired != 1:
                    # The row is locked by this transaction, so this cannot happen: fail loudly.
                    raise RuntimeError("Locked hold changed state during expiration")
                conn.execute(
                    text(
                        "UPDATE horarios_disponibles SET estado = 'DISPONIBLE' "
                        "WHERE id = :h AND estado = 'RESERVADO'"
                    ),
                    {"h": row.horario_id},
                )
                _history(conn, appointment_id, None, _BOOKED, "EXPIRADA", moment)
                _outbox(
                    conn,
                    appointment_id,
                    "cita.expirada",
                    {
                        "cita_id": appointment_id,
                        "horario_id": _canonical(row.horario_id),
                        "estado": "EXPIRADA",
                    },
                    moment,
                )
                emit_cita_snapshot(conn, appointment_id, moment)
        return len(overdue)


_SUMMARY_SELECT = "SELECT c.id, c.estado, h.inicio_utc, h.fin_utc "
# Patient ownership: citas.paciente_id -> pacientes.usuario_id.
_PATIENT_SOURCE = (
    "FROM citas c JOIN pacientes p ON p.id = c.paciente_id "
    "JOIN horarios_disponibles h ON h.id = c.horario_id WHERE p.usuario_id = :usuario "
)
# Agenda ownership: slot -> agenda -> medico -> usuario.
_AGENDA_SOURCE = (
    "FROM citas c JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id JOIN medicos m ON m.id = a.medico_id "
    "WHERE m.usuario_id = :usuario "
)
_APPOINTMENT_SELECT = (
    "SELECT c.id, c.clave_idempotencia, c.horario_id, c.servicio_id, c.estado, c.motivo, "
    "c.expiracion_reserva, p.usuario_id AS patient_user_id "
    "FROM citas c JOIN pacientes p ON p.id = c.paciente_id "
)
# Doctor ownership of an appointment: citas -> horarios_disponibles -> agendas -> medicos.
_AGENDA_OWNER = text(
    "SELECT 1 FROM citas c JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id JOIN medicos m ON m.id = a.medico_id "
    "WHERE c.id = :cita AND m.usuario_id = :usuario"
)


def _may_cancel(conn: Connection, command: CancelCommand, row) -> bool:
    """The patient who owns the appointment, or the doctor whose agenda holds it."""
    if not command.as_medico:
        return _canonical(row.patient_user_id) == command.actor_user_id
    owned = conn.execute(
        _AGENDA_OWNER, {"cita": command.appointment_id, "usuario": command.actor_user_id}
    ).first()
    return owned is not None
