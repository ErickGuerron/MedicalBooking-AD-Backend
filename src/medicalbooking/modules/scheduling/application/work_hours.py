"""Doctor work hours and slot generation.

The doctor declares when they work; the administrator proposes the slots those hours give and
creates them. Business rules live here, not in routes or SQL.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.scheduling.application.ports import (
    DoctorProfile,
    ProposedSlot,
    SchedulingStore,
)
from medicalbooking.modules.scheduling.domain.clinic_hours import (
    BRANCH_TZ,
    WorkBlock,
    franja_of_start,
    slot_starts,
    validate_work_blocks,
)
from medicalbooking.modules.scheduling.domain.policies import (
    PERM_DECLARE_HOURS,
    PERM_GENERATE_SLOTS,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

MAX_RANGE_DAYS = 31


def _require(actor: AccessProfile, permission: str) -> None:
    if permission not in actor.permissions:
        raise Forbidden("Access to this resource is not allowed")


class WorkHoursService:
    def __init__(self, store: SchedulingStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    def get_my_work_hours(self, actor: AccessProfile) -> list[WorkBlock]:
        _require(actor, PERM_DECLARE_HOURS)
        doctor = self._own_doctor(actor)
        return self._store.get_work_blocks(doctor.medico_id)

    def set_my_work_hours(self, actor: AccessProfile, blocks: list[WorkBlock]) -> list[WorkBlock]:
        """The doctor's whole weekly schedule. An empty list means no hours are declared."""
        _require(actor, PERM_DECLARE_HOURS)
        doctor = self._own_doctor(actor)
        validate_work_blocks(blocks)
        now = self._clock()
        self._store.replace_work_blocks(doctor.medico_id, list(blocks), now)
        self._audit(actor, "horario_trabajo", doctor.medico_id, "declarar_horario", now)
        return list(blocks)

    def propose_slots(
        self, actor: AccessProfile, medico_id: str, desde: date, hasta: date
    ) -> list[ProposedSlot]:
        """What the doctor's declared hours would create in the range. Writes nothing."""
        _require(actor, PERM_GENERATE_SLOTS)
        doctor, blocks = self._proposal_inputs(medico_id, desde, hasta)
        return self._slots(doctor, blocks, desde, hasta)

    def generate_slots(
        self,
        actor: AccessProfile,
        medico_id: str,
        consultorio_id: str,
        desde: date,
        hasta: date,
    ) -> tuple[int, int]:
        """Create the proposed slots in one consultorio. Returns (created, already there)."""
        _require(actor, PERM_GENERATE_SLOTS)
        doctor, blocks = self._proposal_inputs(medico_id, desde, hasta)
        if not self._store.consultorio_is_active(consultorio_id):
            raise NotFound("Consultorio not found")
        slots = self._slots(doctor, blocks, desde, hasta)
        created, existing = self._store.insert_generated_slots(
            doctor.medico_id,
            consultorio_id,
            desde,
            hasta,
            [(slot.inicio_utc, slot.fin_utc) for slot in slots],
        )
        self._audit(actor, "horario", doctor.medico_id, "generar_horarios", self._clock())
        return created, existing

    # ---------- helpers ----------

    def _own_doctor(self, actor: AccessProfile) -> DoctorProfile:
        doctor = self._store.doctor_profile_for_user(actor.user_id)
        if doctor is None or not doctor.activo:
            raise Forbidden("Access to this resource is not allowed")
        return doctor

    def _proposal_inputs(
        self, medico_id: str, desde: date, hasta: date
    ) -> tuple[DoctorProfile, list[WorkBlock]]:
        self._validate_range(desde, hasta)
        doctor = self._store.doctor_profile_by_id(str(medico_id))
        if doctor is None or not doctor.activo:
            raise NotFound("Doctor not found")
        blocks = self._store.get_work_blocks(doctor.medico_id)
        if not blocks:
            raise Conflict("The doctor has not declared work hours")
        return doctor, blocks

    def _validate_range(self, desde: date, hasta: date) -> None:
        if desde > hasta:
            raise ValidationFailed("'desde' must be on or before 'hasta'")
        if (hasta - desde).days + 1 > MAX_RANGE_DAYS:
            raise ValidationFailed(f"The range cannot be longer than {MAX_RANGE_DAYS} days")
        today = self._clock().astimezone(BRANCH_TZ).date()
        if desde < today:
            raise ValidationFailed("The range cannot start in the past")

    def _slots(
        self, doctor: DoctorProfile, blocks: list[WorkBlock], desde: date, hasta: date
    ) -> list[ProposedSlot]:
        now = self._clock()
        proposed: list[ProposedSlot] = []
        day = desde
        while day <= hasta:
            for block in sorted(blocks, key=lambda b: b.inicio):
                if block.dia_semana != day.weekday():
                    continue
                for start in slot_starts(block, doctor.duracion_cita_min):
                    begins = datetime.combine(day, start, BRANCH_TZ).astimezone(UTC)
                    if begins <= now:
                        continue
                    ends = begins + timedelta(minutes=doctor.duracion_cita_min)
                    franja = franja_of_start(start)
                    assert franja is not None  # slot_starts only yields slots inside a window
                    proposed.append(ProposedSlot(inicio_utc=begins, fin_utc=ends, franja=franja))
            day += timedelta(days=1)
        return proposed

    def _audit(
        self, actor: AccessProfile, entidad: str, entidad_id: str, accion: str, when: datetime
    ) -> None:
        self._store.record_audit(
            AuditEvent(
                actor_user_id=actor.user_id,
                paciente_id=None,
                entidad=entidad,
                entidad_id=entidad_id,
                accion=accion,
                resultado="OK",
                occurred_at=when,
            )
        )
