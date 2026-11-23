"""Doctor work hours and weekly slot generation.

The doctor declares the hours they work each week. The administrator looks at those hours, proposes
the one-hour appointment slots of a week, and creates them. Business rules live here, not in routes
or SQL.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.scheduling.application.ports import (
    Consultorio,
    DoctorProfile,
    ProposedSlot,
    SchedulingStore,
)
from medicalbooking.modules.scheduling.domain.clinic_hours import (
    BRANCH_TZ,
    SLOT_MINUTES,
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

# How far ahead the administrator can create a week of slots.
MAX_WEEKS_AHEAD = 12


def next_monday(today: date) -> date:
    """The first Monday after `today`: where a change to the doctor's hours starts to apply."""
    return today + timedelta(days=7 - today.weekday())


@dataclass(frozen=True)
class DoctorHours:
    """The hours in force this week and the hours from next week on (the ones the doctor edits)."""

    actual: tuple[WorkBlock, ...]
    proximo: tuple[WorkBlock, ...]
    vigente_desde: date


def _require(actor: AccessProfile, permission: str) -> None:
    if permission not in actor.permissions:
        raise Forbidden("Access to this resource is not allowed")


class WorkHoursService:
    def __init__(self, store: SchedulingStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    # ---------- the doctor ----------

    def get_my_work_hours(self, actor: AccessProfile) -> DoctorHours:
        _require(actor, PERM_DECLARE_HOURS)
        doctor = self._own_doctor(actor)
        return self._hours_of(doctor.medico_id)

    def set_my_work_hours(self, actor: AccessProfile, blocks: list[WorkBlock]) -> DoctorHours:
        """The doctor's weekly schedule from next week on. An empty list means no hours from then.

        Weeks already in progress or planned keep the hours they had: a change never reaches back.
        """
        _require(actor, PERM_DECLARE_HOURS)
        doctor = self._own_doctor(actor)
        validate_work_blocks(blocks)
        now = self._clock()
        effective = next_monday(now.astimezone(BRANCH_TZ).date())
        self._store.replace_work_blocks(doctor.medico_id, list(blocks), effective, now)
        self._audit(actor, "horario_trabajo", doctor.medico_id, "declarar_horario", now)
        return self._hours_of(doctor.medico_id)

    # ---------- the administrator ----------

    def doctor_work_hours(self, actor: AccessProfile, medico_id: str) -> DoctorHours:
        """What a doctor declared, for the administrator to plan from. Writes nothing."""
        _require(actor, PERM_GENERATE_SLOTS)
        doctor = self._active_doctor(medico_id)
        return self._hours_of(doctor.medico_id)

    def list_consultorios(self, actor: AccessProfile) -> list[Consultorio]:
        _require(actor, PERM_GENERATE_SLOTS)
        return self._store.list_consultorios()

    def propose_week(
        self, actor: AccessProfile, medico_id: str, semana: date
    ) -> list[ProposedSlot]:
        """The one-hour slots of the week that starts on `semana`. Writes nothing."""
        _require(actor, PERM_GENERATE_SLOTS)
        doctor, blocks = self._week_inputs(medico_id, semana)
        return self._slots(blocks, semana)

    def generate_week(
        self,
        actor: AccessProfile,
        medico_id: str,
        consultorio_id: str,
        semana: date,
    ) -> tuple[int, int]:
        """Create the week's slots in one consultorio. Returns (created, already there)."""
        _require(actor, PERM_GENERATE_SLOTS)
        doctor, blocks = self._week_inputs(medico_id, semana)
        if not self._store.consultorio_is_active(consultorio_id):
            raise NotFound("Consultorio not found")
        slots = self._slots(blocks, semana)
        created, existing = self._store.insert_generated_slots(
            doctor.medico_id,
            consultorio_id,
            semana,
            semana + timedelta(days=6),
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

    def _active_doctor(self, medico_id: str) -> DoctorProfile:
        doctor = self._store.doctor_profile_by_id(str(medico_id))
        if doctor is None or not doctor.activo:
            raise NotFound("Doctor not found")
        return doctor

    def _week_inputs(self, medico_id: str, semana: date) -> tuple[DoctorProfile, list[WorkBlock]]:
        self._validate_week(semana)
        doctor = self._active_doctor(medico_id)
        if not self._store.declared_hours_exist(doctor.medico_id):
            raise Conflict("The doctor has not declared work hours")
        # The hours in force on that Monday: a later change never alters the week already planned.
        return doctor, self._store.get_work_blocks(doctor.medico_id, semana)

    def _hours_of(self, medico_id: str) -> DoctorHours:
        today = self._clock().astimezone(BRANCH_TZ).date()
        return DoctorHours(
            actual=tuple(self._store.get_work_blocks(medico_id, today)),
            proximo=tuple(self._store.get_work_blocks(medico_id, next_monday(today))),
            vigente_desde=next_monday(today),
        )

    def _validate_week(self, semana: date) -> None:
        if semana.weekday() != 0:
            raise ValidationFailed("'semana' must be a Monday")
        today = self._clock().astimezone(BRANCH_TZ).date()
        if semana + timedelta(days=6) < today:
            raise ValidationFailed("That week has already passed")
        if semana > today + timedelta(weeks=MAX_WEEKS_AHEAD):
            raise ValidationFailed(f"Weeks can be created at most {MAX_WEEKS_AHEAD} weeks ahead")

    def _slots(self, blocks: list[WorkBlock], semana: date) -> list[ProposedSlot]:
        """One-hour slots of the seven days from `semana`, skipping the ones already started."""
        now = self._clock()
        proposed: list[ProposedSlot] = []
        for offset in range(7):
            day = semana + timedelta(days=offset)
            for block in sorted(blocks, key=lambda b: b.inicio):
                if block.dia_semana != day.weekday():
                    continue
                for start in slot_starts(block, SLOT_MINUTES):
                    begins = datetime.combine(day, start, BRANCH_TZ).astimezone(UTC)
                    if begins <= now:
                        continue
                    ends = begins + timedelta(minutes=SLOT_MINUTES)
                    franja = franja_of_start(start)
                    assert franja is not None  # slot_starts only yields slots inside a window
                    proposed.append(ProposedSlot(inicio_utc=begins, fin_utc=ends, franja=franja))
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
