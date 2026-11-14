"""SQL Server adapter for the treatment port (S4-S6). Parameterized SQL only.

Profile lookups and the audit writer are the laboratory adapter's: they are delegated, not copied,
so both modules resolve doctors and patients the same way. The state change is a conditional
UPDATE on the stored estado, so two concurrent transitions cannot both apply. Follow-ups are only
inserted; the database also rejects UPDATE and DELETE on them (migration 0029).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.laboratory.infrastructure.repositories import SqlLabStore
from medicalbooking.modules.treatments.application.ports import (
    CitaContext,
    FollowUp,
    NewFollowUp,
    NewTreatment,
    Treatment,
    TreatmentUpdate,
)

# Treatment joined to its appointment, the patient and the doctor who owns the appointment.
_TREATMENT_SELECT = (
    "SELECT t.id, t.cita_id, c.paciente_id, pa.usuario_id AS paciente_user_id, "
    "m.id AS medico_id, m.usuario_id AS medico_user_id, t.descripcion, t.estado, "
    "t.fecha_inicio, t.fecha_fin FROM tratamientos t "
    "JOIN citas c ON c.id = t.cita_id "
    "JOIN pacientes pa ON pa.id = c.paciente_id "
    "JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id "
    "JOIN medicos m ON m.id = a.medico_id "
)


def _canonical(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the API uses lower case."""
    return str(uuid.UUID(str(value)))


def _as_guid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _utc_aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


class SqlTreatmentStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        # Reused: the laboratory adapter owns profile resolution and the audit table writer.
        self._shared = SqlLabStore(engine)

    # ---------- profiles and audit (delegated) ----------

    def resolve_patient_id(self, user_id: str) -> str | None:
        return self._shared.resolve_patient_id(user_id)

    def resolve_medico_id(self, user_id: str) -> str | None:
        return self._shared.resolve_medico_id(user_id)

    def record_audit(self, event: AuditEvent) -> None:
        self._shared.record_audit(event)

    # ---------- appointment ----------

    def get_cita(self, cita_id: str) -> CitaContext | None:
        guid = _as_guid(cita_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT c.id, c.paciente_id, m.id AS medico_id FROM citas c "
                    "JOIN horarios_disponibles h ON h.id = c.horario_id "
                    "JOIN agendas a ON a.id = h.agenda_id "
                    "JOIN medicos m ON m.id = a.medico_id WHERE c.id = :c"
                ),
                {"c": guid},
            ).first()
        if row is None:
            return None
        return CitaContext(
            cita_id=_canonical(row.id),
            paciente_id=_canonical(row.paciente_id),
            medico_id=_canonical(row.medico_id),
        )

    # ---------- treatments ----------

    def create_treatment(self, command: NewTreatment) -> Treatment:
        now = _utc_naive(command.creado_en)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tratamientos (id, cita_id, descripcion, estado, fecha_inicio, "
                    "fecha_fin, creado_en, actualizado_en) "
                    "VALUES (:id, :cita, :descripcion, :estado, :inicio, :fin, :ahora, :ahora)"
                ),
                {
                    "id": command.treatment_id,
                    "cita": command.cita_id,
                    "descripcion": command.descripcion,
                    "estado": command.estado,
                    "inicio": command.fecha_inicio,
                    "fin": command.fecha_fin,
                    "ahora": now,
                },
            )
        treatment = self.get_treatment(command.treatment_id)
        assert treatment is not None
        return treatment

    def get_treatment(self, treatment_id: str) -> Treatment | None:
        guid = _as_guid(treatment_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(text(_TREATMENT_SELECT + "WHERE t.id = :t"), {"t": guid}).first()
        return None if row is None else _treatment(row)

    def list_treatments(self, cita_id: str) -> list[Treatment]:
        guid = _as_guid(cita_id)
        if guid is None:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    _TREATMENT_SELECT
                    + "WHERE t.cita_id = :c ORDER BY t.fecha_inicio DESC, t.id ASC"
                ),
                {"c": guid},
            ).all()
        return [_treatment(row) for row in rows]

    def update_treatment(self, command: TreatmentUpdate) -> Treatment | None:
        now = _utc_naive(command.actualizado_en)
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE tratamientos SET descripcion = :descripcion, estado = :estado, "
                    "fecha_fin = :fin, actualizado_en = :ahora "
                    "WHERE id = :id AND estado = :expected"
                ),
                {
                    "descripcion": command.descripcion,
                    "estado": command.estado,
                    "fin": command.fecha_fin,
                    "ahora": now,
                    "id": command.treatment_id,
                    "expected": command.expected_estado,
                },
            )
            if result.rowcount == 0:
                return None
        return self.get_treatment(command.treatment_id)

    # ---------- follow-ups ----------

    def append_followup(self, command: NewFollowUp) -> FollowUp:
        registered = _utc_aware(command.registrado_en)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tratamientos_seguimientos (id, tratamiento_id, nota, "
                    "registrado_por, registrado_en) "
                    "VALUES (:id, :t, :nota, :u, :ahora)"
                ),
                {
                    "id": command.followup_id,
                    "t": command.tratamiento_id,
                    "nota": command.nota,
                    "u": command.registrado_por_user_id,
                    "ahora": _utc_naive(registered),
                },
            )
        return FollowUp(
            id=command.followup_id,
            tratamiento_id=command.tratamiento_id,
            nota=command.nota,
            registrado_en=registered,
        )

    def list_followups(self, tratamiento_id: str) -> list[FollowUp]:
        guid = _as_guid(tratamiento_id)
        if guid is None:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, tratamiento_id, nota, registrado_en FROM tratamientos_seguimientos "
                    "WHERE tratamiento_id = :t ORDER BY secuencia"
                ),
                {"t": guid},
            ).all()
        return [
            FollowUp(
                id=_canonical(row.id),
                tratamiento_id=_canonical(row.tratamiento_id),
                nota=row.nota,
                registrado_en=_utc_aware(row.registrado_en),
            )
            for row in rows
        ]


def _treatment(row) -> Treatment:
    return Treatment(
        id=_canonical(row.id),
        cita_id=_canonical(row.cita_id),
        paciente_id=_canonical(row.paciente_id),
        paciente_user_id=_canonical(row.paciente_user_id),
        medico_id=_canonical(row.medico_id),
        medico_user_id=_canonical(row.medico_user_id),
        descripcion=row.descripcion,
        estado=row.estado,
        fecha_inicio=row.fecha_inicio,
        fecha_fin=row.fecha_fin,
    )
