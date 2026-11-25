"""SQL Server adapter for the scheduling ports.

Each reservation and cancellation runs in one transaction: the conditional UPDATE on the
slot is the first guard, the filtered unique index is the second, and the history and
outbox rows commit with the appointment or not at all. Parameterized SQL only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Connection, Engine, bindparam, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.scheduling.application.ports import (
    Appointment,
    AppointmentSummary,
    ApprovalStatus,
    AvailableSlot,
    CancelCommand,
    CancelOutcome,
    CancelStatus,
    CloseConsultationCommand,
    Consultation,
    ConsultationOutcome,
    ConsultationStatus,
    Consultorio,
    ConsultorioDetail,
    ConsultorioOwned,
    DoctorProfile,
    OpenConsultationCommand,
    ReserveCommand,
    ReserveOutcome,
    ReserveStatus,
    SedeDetail,
)
from medicalbooking.modules.scheduling.domain.clinic_hours import (
    FRANJAS,
    WorkBlock,
    declared_hours_payload,
)
from medicalbooking.modules.scheduling.domain.policies import (
    ACTIVE_BOOKING_STATES,
    ACTIVE_STATES,
    cancellation_window_closed,
    doctor_cancellation_blockers,
    month_bounds,
)
from medicalbooking.shared.infrastructure.outbox import append_outbox
from medicalbooking.shared.infrastructure.sql_errors import is_unique_violation, retry_on_deadlock

_BOOKED = "PENDIENTE_PAGO"
_CANCELLED = "CANCELADA"


def _as_guid(value: object) -> uuid.UUID | None:
    """The value as a UUID, or None when it is not one (so no SQL is sent for a malformed id)."""
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


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
            "c.motivo_cancelacion_texto, h.inicio_utc, h.fin_utc, a.medico_id "
            "FROM citas AS c JOIN horarios_disponibles AS h ON h.id = c.horario_id "
            "JOIN agendas AS a ON a.id = h.agenda_id "
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
                "medico_id": _canonical(row.medico_id),
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


def confirm_appointment_of_invoice(conn: Connection, factura_id: str, moment: datetime) -> bool:
    """Move the appointment of a paid invoice from PENDIENTE_PAGO to CONFIRMADA.

    Runs inside the caller's payment transaction: the state, ``confirmada_en``, the history row
    and the replica snapshot commit with the payment or not at all. Only PENDIENTE_PAGO moves, so
    a cancelled or expired appointment is never resurrected. Returns False (and changes nothing)
    when the invoice has no appointment or it is already in another state; that never fails the
    payment confirmation. ``moment`` is naive UTC, like the payment timestamps.
    """
    cita_id = conn.execute(
        text("SELECT cita_id FROM facturas WHERE id = :f"), {"f": factura_id}
    ).scalar()
    if cita_id is None:
        return False
    moved = conn.execute(
        text(
            "UPDATE citas SET estado = 'CONFIRMADA', confirmada_en = :ahora "
            "WHERE id = :c AND estado = 'PENDIENTE_PAGO'"
        ),
        {"c": str(cita_id), "ahora": moment},
    ).rowcount
    if moved != 1:
        return False
    appointment_id = _canonical(cita_id)
    _history(conn, appointment_id, None, _BOOKED, "CONFIRMADA", moment)
    emit_cita_snapshot(conn, appointment_id, moment)
    return True


_LOCK_AGENDA_APPOINTMENT = text(
    "SELECT c.id, c.estado, c.paciente_id, h.agenda_id, a.medico_id "
    "FROM citas AS c WITH (UPDLOCK, ROWLOCK) "
    "JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id JOIN medicos m ON m.id = a.medico_id "
    "WHERE c.id = :cita AND m.usuario_id = :usuario"
)


def lock_agenda_appointment(conn: Connection, cita_id: str, doctor_user_id: str):
    """Lock an appointment of the doctor's own agenda for the rest of the transaction.

    Returns the row (id, estado, paciente_id, agenda_id, medico_id), or None when the
    appointment does not exist or belongs to another doctor's agenda.
    """
    return conn.execute(
        _LOCK_AGENDA_APPOINTMENT, {"cita": cita_id, "usuario": doctor_user_id}
    ).first()


def ensure_consultation(conn: Connection, cita_id: str, now: datetime) -> tuple[str, bool]:
    """Return (consultation id, created) for an appointment, creating it when missing.

    One consultation per appointment (UNIQUE cita_id). The caller holds the appointment lock
    (``lock_agenda_appointment``), so two concurrent callers cannot both insert.
    """
    existing = conn.execute(
        text("SELECT id FROM consultas_medicas WHERE cita_id = :c"), {"c": cita_id}
    ).scalar()
    if existing is not None:
        return _canonical(existing), False
    consulta_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO consultas_medicas (id, cita_id, inicio_atencion, motivo_consulta) "
            "SELECT :id, c.id, :ahora, c.motivo FROM citas c WHERE c.id = :c"
        ),
        {"id": consulta_id, "c": cita_id, "ahora": _utc_naive(now)},
    )
    return consulta_id, True


class SqlSchedulingStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---------- doctor work hours and slot generation ----------

    def doctor_profile_for_user(self, user_id: str) -> DoctorProfile | None:
        return self._doctor_profile("usuario_id", user_id)

    def doctor_profile_by_id(self, medico_id: str) -> DoctorProfile | None:
        return self._doctor_profile("id", medico_id)

    def _doctor_profile(self, column: str, value: str) -> DoctorProfile | None:
        # `column` is a fixed name written in this module, never user input.
        if column == "id" and _as_guid(value) is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT id, duracion_cita_min, activo FROM medicos WHERE {column} = :v"  # noqa: S608
                ),
                {"v": value},
            ).first()
        if row is None:
            return None
        return DoctorProfile(
            medico_id=_canonical(row.id),
            duracion_cita_min=row.duracion_cita_min,
            activo=bool(row.activo),
        )

    def get_work_blocks(self, medico_id: str, on: date) -> list[WorkBlock]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT b.dia_semana, b.inicio, b.fin "
                    "FROM horarios_trabajo_medico b "
                    "JOIN horarios_trabajo_version v ON v.id = b.version_id "
                    "WHERE v.medico_id = :m AND v.vigente_desde = ("
                    "SELECT MAX(v2.vigente_desde) FROM horarios_trabajo_version v2 "
                    "WHERE v2.medico_id = :m AND v2.vigente_desde <= :d) "
                    "ORDER BY b.dia_semana, b.inicio"
                ),
                {"m": medico_id, "d": on},
            ).all()
        return [
            WorkBlock(dia_semana=row.dia_semana, inicio=row.inicio, fin=row.fin) for row in rows
        ]

    def list_consultorios(self) -> list[Consultorio]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.id, c.nombre, s.nombre AS sede FROM consultorios c "
                    "JOIN sedes s ON s.id = c.sede_id WHERE c.activo = 1 AND s.activo = 1 "
                    "ORDER BY s.nombre, c.nombre"
                )
            ).all()
        return [
            Consultorio(id=_canonical(row.id), nombre=row.nombre, sede=row.sede) for row in rows
        ]

    # ---------- sedes and consultorios (administration) ----------

    def list_sedes(self) -> list[SedeDetail]:
        with self._engine.connect() as conn:
            sedes = conn.execute(
                text("SELECT id, nombre, direccion, activo FROM sedes ORDER BY nombre")
            ).all()
            offices = conn.execute(
                text("SELECT id, sede_id, nombre, activo FROM consultorios ORDER BY nombre")
            ).all()
        by_sede: dict[str, list[ConsultorioDetail]] = {}
        for office in offices:
            by_sede.setdefault(_canonical(office.sede_id), []).append(
                ConsultorioDetail(
                    id=_canonical(office.id), nombre=office.nombre, activo=bool(office.activo)
                )
            )
        return [
            SedeDetail(
                id=_canonical(row.id),
                nombre=row.nombre,
                direccion=row.direccion,
                activo=bool(row.activo),
                consultorios=tuple(by_sede.get(_canonical(row.id), [])),
            )
            for row in sedes
        ]

    def get_sede(self, sede_id: str) -> SedeDetail | None:
        guid = _as_guid(sede_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, nombre, direccion, activo FROM sedes WHERE id = :s"),
                {"s": str(guid)},
            ).first()
            if row is None:
                return None
            offices = conn.execute(
                text(
                    "SELECT id, nombre, activo FROM consultorios WHERE sede_id = :s ORDER BY nombre"
                ),
                {"s": str(guid)},
            ).all()
        return SedeDetail(
            id=_canonical(row.id),
            nombre=row.nombre,
            direccion=row.direccion,
            activo=bool(row.activo),
            consultorios=tuple(
                ConsultorioDetail(id=_canonical(o.id), nombre=o.nombre, activo=bool(o.activo))
                for o in offices
            ),
        )

    def insert_sede(self, sede_id: str, nombre: str, direccion: str | None) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("INSERT INTO sedes (id, nombre, direccion) VALUES (:id, :n, :d)"),
                {"id": sede_id, "n": nombre, "d": direccion},
            )

    def update_sede(self, sede_id: str, nombre: str, direccion: str | None, activo: bool) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("UPDATE sedes SET nombre = :n, direccion = :d, activo = :a WHERE id = :id"),
                {"id": sede_id, "n": nombre, "d": direccion, "a": int(activo)},
            )

    def get_consultorio(self, consultorio_id: str) -> ConsultorioOwned | None:
        guid = _as_guid(consultorio_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, sede_id, nombre, activo FROM consultorios WHERE id = :c"),
                {"c": str(guid)},
            ).first()
        if row is None:
            return None
        return ConsultorioOwned(
            id=_canonical(row.id),
            sede_id=_canonical(row.sede_id),
            nombre=row.nombre,
            activo=bool(row.activo),
        )

    def insert_consultorio(self, consultorio_id: str, sede_id: str, nombre: str) -> bool:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO consultorios (id, sede_id, nombre) VALUES (:id, :s, :n)"),
                    {"id": consultorio_id, "s": sede_id, "n": nombre},
                )
        except IntegrityError as error:
            if not is_unique_violation(str(error.orig)):
                raise
            return False
        return True

    def update_consultorio(self, consultorio_id: str, nombre: str, activo: bool) -> bool:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text("UPDATE consultorios SET nombre = :n, activo = :a WHERE id = :c"),
                    {"c": consultorio_id, "n": nombre, "a": int(activo)},
                )
        except IntegrityError as error:
            if not is_unique_violation(str(error.orig)):
                raise
            return False
        return True

    def has_future_bookings(self, consultorio_ids: list[str], now: datetime) -> bool:
        guids = [str(guid) for guid in (_as_guid(c) for c in consultorio_ids) if guid is not None]
        if not guids:
            return False
        statement = text(
            "SELECT TOP 1 1 FROM citas ct "
            "JOIN horarios_disponibles h ON h.id = ct.horario_id "
            "JOIN agendas a ON a.id = h.agenda_id "
            "WHERE a.consultorio_id IN :consultorios AND ct.estado IN :estados "
            "AND h.inicio_utc > :ahora"
        ).bindparams(
            bindparam("consultorios", expanding=True), bindparam("estados", expanding=True)
        )
        with self._engine.connect() as conn:
            found = conn.execute(
                statement,
                {
                    "consultorios": guids,
                    "estados": list(ACTIVE_BOOKING_STATES),
                    "ahora": _utc_naive(now),
                },
            ).first()
        return found is not None

    def declared_hours_exist(self, medico_id: str) -> bool:
        with self._engine.connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM horarios_trabajo_version WHERE medico_id = :m"),
                {"m": medico_id},
            ).first()
        return found is not None

    def replace_work_blocks(
        self, medico_id: str, blocks: list[WorkBlock], effective_from: date, now: datetime
    ) -> None:
        moment = _utc_naive(now)
        with self._engine.begin() as conn:
            version = conn.execute(
                text(
                    "SELECT id FROM horarios_trabajo_version "
                    "WHERE medico_id = :m AND vigente_desde = :d"
                ),
                {"m": medico_id, "d": effective_from},
            ).first()
            if version is None:
                version_id = str(uuid.uuid4())
                conn.execute(
                    text(
                        "INSERT INTO horarios_trabajo_version "
                        "(id, medico_id, vigente_desde, creado_en) "
                        "VALUES (:id, :m, :d, :ahora)"
                    ),
                    {"id": version_id, "m": medico_id, "d": effective_from, "ahora": moment},
                )
            else:
                version_id = str(version.id)
                conn.execute(
                    text("DELETE FROM horarios_trabajo_medico WHERE version_id = :v"),
                    {"v": version_id},
                )
            for block in blocks:
                conn.execute(
                    text(
                        "INSERT INTO horarios_trabajo_medico "
                        "(id, version_id, dia_semana, inicio, fin, actualizado_en) "
                        "VALUES (:id, :v, :d, :i, :f, :ahora)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "v": version_id,
                        "d": block.dia_semana,
                        "i": block.inicio,
                        "f": block.fin,
                        "ahora": moment,
                    },
                )
            # Replica event (replica-work-hours S1): the whole week in the same transaction.
            append_outbox(
                conn,
                "medico_horario",
                _canonical(medico_id),
                "horario_trabajo.declarado",
                declared_hours_payload(_canonical(medico_id), effective_from, blocks),
                moment,
            )

    def consultorio_is_active(self, consultorio_id: str) -> bool:
        if _as_guid(consultorio_id) is None:
            return False
        with self._engine.connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM consultorios WHERE id = :c AND activo = 1"),
                {"c": consultorio_id},
            ).first()
        return found is not None

    def insert_generated_slots(
        self,
        medico_id: str,
        consultorio_id: str,
        desde: date,
        hasta: date,
        slots: list[tuple[datetime, datetime]],
    ) -> tuple[int, int]:
        with self._engine.begin() as conn:
            agenda = conn.execute(
                text(
                    "SELECT TOP 1 id FROM agendas WHERE medico_id = :m AND consultorio_id = :c "
                    "AND activa = 1 AND fecha_inicio <= :d "
                    "AND (fecha_fin IS NULL OR fecha_fin >= :h)"
                ),
                {"m": medico_id, "c": consultorio_id, "d": desde, "h": hasta},
            ).first()
            agenda_id = str(agenda.id) if agenda else str(uuid.uuid4())
            if agenda is None:
                conn.execute(
                    text(
                        "INSERT INTO agendas "
                        "(id, medico_id, consultorio_id, fecha_inicio, fecha_fin, activa) "
                        "VALUES (:id, :m, :c, :d, :h, 1)"
                    ),
                    {"id": agenda_id, "m": medico_id, "c": consultorio_id, "d": desde, "h": hasta},
                )
            created = 0
            for start, end in slots:
                # A doctor never has two slots at the same start, in any agenda.
                created += conn.execute(
                    text(
                        "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc) "
                        "SELECT :id, :a, :s, :e WHERE NOT EXISTS ("
                        "SELECT 1 FROM horarios_disponibles h "
                        "JOIN agendas ag ON ag.id = h.agenda_id "
                        "WHERE ag.medico_id = :m AND h.inicio_utc = :s)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "a": agenda_id,
                        "s": _utc_naive(start),
                        "e": _utc_naive(end),
                        "m": medico_id,
                    },
                ).rowcount
        return created, len(slots) - created

    def service_franja(self, servicio_id: str) -> str | None:
        if _as_guid(servicio_id) is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT franja FROM servicios_medicos WHERE id = :s"), {"s": servicio_id}
            ).first()
        return row.franja if row else None

    def list_available(
        self, start, end, medico_id, limit, especialidad_id=None, franja=None
    ) -> list[AvailableSlot]:
        sql = (
            "SELECT TOP (:limite) h.id, m.id AS medico_id, h.inicio_utc, h.fin_utc, "
            "u.nombre, u.apellido "
            "FROM horarios_disponibles h "
            "JOIN agendas a ON a.id = h.agenda_id "
            "JOIN medicos m ON m.id = a.medico_id "
            "JOIN usuarios u ON u.id = m.usuario_id "
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
        if especialidad_id is not None:
            sql += (
                "AND EXISTS (SELECT 1 FROM medicos_especialidades me WHERE me.medico_id = m.id "
                "AND me.especialidad_id = :esp) "
            )
            params["esp"] = especialidad_id
        if franja is not None:
            # Branch time is UTC-5, as the slot generation uses.
            local = "CAST(DATEADD(hour, -5, h.inicio_utc) AS time)"
            sql += f"AND {local} >= :franja_ini AND {local} < :franja_fin "
            params["franja_ini"], params["franja_fin"] = FRANJAS[franja]
        sql += "ORDER BY h.inicio_utc"
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
        return [
            AvailableSlot(
                horario_id=_canonical(row.id),
                medico_id=_canonical(row.medico_id),
                inicio_utc=_utc_aware(row.inicio_utc),
                fin_utc=_utc_aware(row.fin_utc),
                medico_nombre=f"{row.nombre} {row.apellido}".strip(),
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
        return self._list_summaries(
            _PATIENT_SOURCE, patient_user_id, estado, limit, offset, with_medico=True
        )

    def list_agenda_appointments(self, doctor_user_id, estado, limit, offset):
        return self._list_summaries(
            _AGENDA_SOURCE, doctor_user_id, estado, limit, offset, with_patient=True
        )

    def list_cancellation_candidates(
        self,
        estado: str | None,
        limit: int,
        offset: int,
        medico_registro: str | None = None,
    ) -> list[AppointmentSummary]:
        """Doctor identity only: no patient and no doctor phone are selected."""
        sql = _CANDIDATE_SELECT + _CANDIDATE_SOURCE
        params: dict[str, object] = {
            "saltar": offset,
            "cantidad": limit,
        }
        if estado is not None:
            sql += "AND c.estado = :estado "
            params["estado"] = estado
        if medico_registro:
            sql += "AND m.registro_profesional = :reg "
            params["reg"] = medico_registro
        sql += "ORDER BY h.inicio_utc, c.id OFFSET :saltar ROWS FETCH NEXT :cantidad ROWS ONLY"
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
        return [
            AppointmentSummary(
                id=_canonical(row.id),
                estado=row.estado,
                inicio_utc=_utc_aware(row.inicio_utc),
                fin_utc=_utc_aware(row.fin_utc),
                medico_id=_canonical(row.medico_id),
                medico_nombre=f"{row.medico_nombre} {row.medico_apellido}".strip(),
                medico_registro=row.medico_registro,
                tiene_aprobacion=bool(row.tiene_aprobacion),
            )
            for row in rows
        ]

    def record_audit(self, event: AuditEvent) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO auditoria_accesos (id, usuario_id, paciente_id, entidad, "
                    "entidad_id, accion, fecha_evento, resultado) "
                    "VALUES (:id, :u, :p, :entidad, :entidad_id, :accion, :ahora, :resultado)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "u": event.actor_user_id,
                    "p": event.paciente_id,
                    "entidad": event.entidad,
                    "entidad_id": event.entidad_id,
                    "accion": event.accion,
                    "ahora": _utc_naive(event.occurred_at),
                    "resultado": event.resultado,
                },
            )

    def _list_summaries(
        self, source: str, user_id, estado, limit, offset, with_patient=False, with_medico=False
    ):
        """A patient's listing carries the doctor of each row (contact data); the doctor's agenda
        adds the patient. Nothing else is selected."""
        if with_patient:
            select = _AGENDA_SUMMARY_SELECT
        elif with_medico:
            select = _PATIENT_SUMMARY_SELECT
        else:
            select = _SUMMARY_SELECT
        sql = select + source
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
                **(
                    {
                        "paciente_id": _canonical(row.paciente_id),
                        "paciente_nombre": f"{row.paciente_nombre} {row.paciente_apellido}".strip(),
                    }
                    if with_patient
                    else {}
                ),
                **(
                    {
                        "medico_id": _canonical(row.medico_id),
                        "medico_nombre": f"{row.medico_nombre} {row.medico_apellido}".strip(),
                        "medico_registro": row.medico_registro,
                        "medico_telefono": row.medico_telefono,
                    }
                    if with_medico
                    else {}
                ),
                pago_id=_canonical(row._mapping["pago_id"])
                if row._mapping.get("pago_id")
                else None,
                reembolso_estado=row._mapping.get("reembolso_estado"),
            )
            for row in rows
        ]

    def open_consultation(self, command: OpenConsultationCommand) -> ConsultationOutcome:
        return retry_on_deadlock(lambda: self._open_consultation_once(command))

    def _open_consultation_once(self, command: OpenConsultationCommand) -> ConsultationOutcome:
        with self._engine.begin() as conn:
            cita = lock_agenda_appointment(conn, command.appointment_id, command.actor_user_id)
            if cita is None:
                return ConsultationOutcome(ConsultationStatus.NOT_FOUND_OR_FORBIDDEN, None)
            if cita.estado not in command.allowed_states:
                return ConsultationOutcome(ConsultationStatus.STATE_NOT_ALLOWED, None)
            consulta_id, created = ensure_consultation(conn, str(cita.id), command.now)
            if created:
                conn.execute(
                    text(
                        "INSERT INTO auditoria_accesos (id, usuario_id, paciente_id, entidad, "
                        "entidad_id, accion, fecha_evento, resultado) "
                        "VALUES (:id, :u, :p, 'consulta_medica', :e, 'abrir', :ahora, 'OK')"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "u": command.actor_user_id,
                        "p": str(cita.paciente_id),
                        "e": consulta_id,
                        "ahora": _utc_naive(command.now),
                    },
                )
            row = conn.execute(
                text(
                    "SELECT id, cita_id, estado, inicio_atencion, motivo_consulta "
                    "FROM consultas_medicas WHERE id = :id"
                ),
                {"id": consulta_id},
            ).one()
        status = ConsultationStatus.CREATED if created else ConsultationStatus.EXISTING
        return ConsultationOutcome(
            status,
            Consultation(
                id=_canonical(row.id),
                cita_id=_canonical(row.cita_id),
                estado=row.estado,
                inicio_atencion=_utc_aware(row.inicio_atencion),
                motivo_consulta=row.motivo_consulta,
            ),
        )

    def close_consultation(self, command: CloseConsultationCommand) -> ConsultationOutcome:
        return retry_on_deadlock(lambda: self._close_consultation_once(command))

    def _close_consultation_once(self, command: CloseConsultationCommand) -> ConsultationOutcome:
        with self._engine.begin() as conn:
            cita = lock_agenda_appointment(conn, command.appointment_id, command.actor_user_id)
            if cita is None:
                return ConsultationOutcome(ConsultationStatus.NOT_FOUND_OR_FORBIDDEN, None)
            if cita.estado not in command.allowed_states:
                return ConsultationOutcome(ConsultationStatus.STATE_NOT_ALLOWED, None)
            consulta_id, _ = ensure_consultation(conn, str(cita.id), command.now)
            estado = conn.execute(
                text("SELECT estado FROM consultas_medicas WHERE id = :id"), {"id": consulta_id}
            ).scalar()
            if estado == "CERRADA":
                return ConsultationOutcome(ConsultationStatus.ALREADY_CLOSED, None)
            conn.execute(
                text(
                    "UPDATE consultas_medicas SET observaciones = :o, plan_tratamiento = :p, "
                    "fin_atencion = :ahora, estado = 'CERRADA' WHERE id = :id"
                ),
                {
                    "o": command.observaciones,
                    "p": command.plan_tratamiento,
                    "ahora": _utc_naive(command.now),
                    "id": consulta_id,
                },
            )
            # The attention is done: the appointment moves to COMPLETADA (the consultation
            # states already accept it, so labs and a repeat open keep working).
            conn.execute(
                text("UPDATE citas SET estado = 'COMPLETADA' WHERE id = :c"),
                {"c": str(cita.id)},
            )
            conn.execute(
                text(
                    "INSERT INTO auditoria_accesos (id, usuario_id, paciente_id, entidad, "
                    "entidad_id, accion, fecha_evento, resultado) "
                    "VALUES (:id, :u, :p, 'consulta_medica', :e, 'cerrar', :ahora, 'OK')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "u": command.actor_user_id,
                    "p": str(cita.paciente_id),
                    "e": consulta_id,
                    "ahora": _utc_naive(command.now),
                },
            )
            row = conn.execute(
                text(
                    "SELECT id, cita_id, estado, inicio_atencion, motivo_consulta, observaciones, "
                    "plan_tratamiento, fin_atencion FROM consultas_medicas WHERE id = :id"
                ),
                {"id": consulta_id},
            ).one()
        return ConsultationOutcome(
            ConsultationStatus.CLOSED,
            Consultation(
                id=_canonical(row.id),
                cita_id=_canonical(row.cita_id),
                estado=row.estado,
                inicio_atencion=_utc_aware(row.inicio_atencion),
                motivo_consulta=row.motivo_consulta,
                observaciones=row.observaciones,
                plan_tratamiento=row.plan_tratamiento,
                fin_atencion=_utc_aware(row.fin_atencion),
            ),
        )

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
                    "c.clave_idempotencia, c.expiracion_reserva, p.usuario_id AS patient_user_id, "
                    "h.inicio_utc "
                    "FROM citas c WITH (UPDLOCK, ROWLOCK) "
                    "JOIN pacientes p ON p.id = c.paciente_id "
                    "JOIN horarios_disponibles h ON h.id = c.horario_id WHERE c.id = :id"
                ),
                {"id": command.appointment_id},
            ).first()
            if row is None or not _may_cancel(conn, command, row):
                return CancelOutcome(CancelStatus.NOT_FOUND_OR_FORBIDDEN, None)
            if row.estado not in ACTIVE_STATES:
                return CancelOutcome(CancelStatus.NOT_CANCELLABLE, None)
            if cancellation_window_closed(row.inicio_utc, now):
                return CancelOutcome(CancelStatus.WINDOW_CLOSED, None)
            if command.as_medico:
                blockers = _doctor_blockers(conn, command, row.id)
                if blockers:
                    return CancelOutcome(CancelStatus.REQUIRES_APPROVAL, None, blockers)
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

    def approve_doctor_cancellation(
        self, appointment_id: str, approver_user_id: str, motivo: str, now: datetime
    ) -> ApprovalStatus:
        """Record one admin approval for an appointment. The unique index on the appointment is
        the last guard against two admins approving at the same time."""
        moment = _utc_naive(now)
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        "SELECT c.estado, h.inicio_utc FROM citas c WITH (UPDLOCK, ROWLOCK) "
                        "JOIN horarios_disponibles h ON h.id = c.horario_id WHERE c.id = :id"
                    ),
                    {"id": appointment_id},
                ).first()
                if row is None:
                    return ApprovalStatus.NOT_FOUND
                if row.estado not in ACTIVE_STATES:
                    return ApprovalStatus.NOT_CANCELLABLE
                if cancellation_window_closed(row.inicio_utc, moment):
                    return ApprovalStatus.WINDOW_CLOSED
                conn.execute(
                    text(
                        "INSERT INTO aprobaciones_cancelacion_medico "
                        "(id, cita_id, aprobado_por, motivo, aprobado_en) "
                        "VALUES (:id, :cita, :admin, :motivo, :ahora)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "cita": appointment_id,
                        "admin": approver_user_id,
                        "motivo": motivo,
                        "ahora": moment,
                    },
                )
        except IntegrityError as error:
            if not is_unique_violation(str(error.orig)):
                raise
            return ApprovalStatus.ALREADY_APPROVED
        return ApprovalStatus.APPROVED

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
_AGENDA_SUMMARY_SELECT = (
    "SELECT c.id, c.estado, h.inicio_utc, h.fin_utc, c.paciente_id, "
    "pu.nombre AS paciente_nombre, pu.apellido AS paciente_apellido "
)
# Patient listing: the doctor of each row, for contact. Ownership: citas.paciente_id -> pacientes.
_PATIENT_SUMMARY_SELECT = (
    "SELECT c.id, c.estado, h.inicio_utc, h.fin_utc, m.id AS medico_id, "
    "mu.nombre AS medico_nombre, mu.apellido AS medico_apellido, "
    "m.registro_profesional AS medico_registro, mu.telefono AS medico_telefono, "
    "(SELECT TOP (1) p2.id FROM pagos AS p2 JOIN facturas AS f2 ON f2.id = p2.factura_id "
    "WHERE f2.cita_id = c.id AND p2.estado = 'CONFIRMADO' ORDER BY p2.creado_en DESC) AS pago_id, "
    "(SELECT TOP (1) r.estado FROM reembolsos AS r JOIN pagos AS p3 ON p3.id = r.pago_id "
    "JOIN facturas AS f3 ON f3.id = p3.factura_id WHERE f3.cita_id = c.id "
    "ORDER BY CASE WHEN r.estado IN ('PENDIENTE', 'PROCESADO') THEN 0 ELSE 1 END, "
    "r.solicitado_en DESC) AS reembolso_estado "
)
_PATIENT_SOURCE = (
    "FROM citas c JOIN pacientes p ON p.id = c.paciente_id "
    "JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id JOIN medicos m ON m.id = a.medico_id "
    "JOIN usuarios mu ON mu.id = m.usuario_id "
    "WHERE p.usuario_id = :usuario "
)
# Agenda ownership: slot -> agenda -> medico -> usuario.
_AGENDA_SOURCE = (
    "FROM citas c JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id JOIN medicos m ON m.id = a.medico_id "
    "JOIN pacientes pa ON pa.id = c.paciente_id JOIN usuarios pu ON pu.id = pa.usuario_id "
    "WHERE m.usuario_id = :usuario "
)
# Cancellation review: the doctor's identity and approval status only. No patient, no phone.
_CANDIDATE_SELECT = (
    "SELECT c.id, c.estado, h.inicio_utc, h.fin_utc, m.id AS medico_id, "
    "mu.nombre AS medico_nombre, mu.apellido AS medico_apellido, "
    "m.registro_profesional AS medico_registro, "
    "CASE WHEN acm.id IS NOT NULL THEN 1 ELSE 0 END AS tiene_aprobacion "
)
_CANDIDATE_SOURCE = (
    "FROM citas c JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id JOIN medicos m ON m.id = a.medico_id "
    "JOIN usuarios mu ON mu.id = m.usuario_id "
    "LEFT JOIN aprobaciones_cancelacion_medico acm ON acm.cita_id = c.id "
    "WHERE 1=1 "
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


_DOCTOR_SLOT = text(
    "SELECT m.id AS medico_id, h.inicio_utc FROM citas c "
    "JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id JOIN medicos m ON m.id = a.medico_id "
    "WHERE c.id = :cita"
)
# Serializes doctor cancellations of one doctor, so two concurrent requests cannot both pass
# the monthly quota.
_LOCK_MEDICO = text("SELECT id FROM medicos WITH (UPDLOCK, ROWLOCK) WHERE id = :medico")
_DOCTOR_CANCELS_IN_MONTH = text(
    "SELECT COUNT(*) FROM citas c JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id "
    "WHERE a.medico_id = :medico AND c.estado = 'CANCELADA' "
    "AND c.motivo_cancelacion_codigo IN ('IMPREVISTO_MEDICO', 'OTRO_MEDICO') "
    "AND c.cancelada_en >= :desde AND c.cancelada_en < :hasta"
)
_HAS_APPROVAL = text("SELECT 1 FROM aprobaciones_cancelacion_medico WHERE cita_id = :cita")


def _doctor_blockers(conn: Connection, command: CancelCommand, cita_id) -> tuple[str, ...]:
    """Limits a doctor's cancellation must clear; an admin approval for the appointment clears
    them all. Counts only earlier doctor cancellations of the same doctor in the UTC month."""
    slot = conn.execute(_DOCTOR_SLOT, {"cita": cita_id}).one()
    conn.execute(_LOCK_MEDICO, {"medico": slot.medico_id}).first()
    if conn.execute(_HAS_APPROVAL, {"cita": cita_id}).first() is not None:
        return ()
    desde, hasta = month_bounds(command.now)
    cancellations = conn.execute(
        _DOCTOR_CANCELS_IN_MONTH,
        {
            "medico": slot.medico_id,
            "desde": _utc_naive(desde),
            "hasta": _utc_naive(hasta),
        },
    ).scalar_one()
    return doctor_cancellation_blockers(
        now=command.now,
        slot_start=_utc_aware(slot.inicio_utc),
        cancellations_this_month=cancellations,
    )


def _may_cancel(conn: Connection, command: CancelCommand, row) -> bool:
    """The patient who owns the appointment, or the doctor whose agenda holds it."""
    if not command.as_medico:
        return _canonical(row.patient_user_id) == command.actor_user_id
    owned = conn.execute(
        _AGENDA_OWNER, {"cita": command.appointment_id, "usuario": command.actor_user_id}
    ).first()
    return owned is not None
