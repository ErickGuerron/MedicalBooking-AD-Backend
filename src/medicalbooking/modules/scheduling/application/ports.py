"""Ports for scheduling. The SQL adapter implements them; use cases never import SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Protocol

from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.scheduling.domain.clinic_hours import WorkBlock


@dataclass(frozen=True)
class AvailableSlot:
    horario_id: str
    medico_id: str
    inicio_utc: datetime
    fin_utc: datetime
    # Public name of the doctor; None when the adapter does not know it.
    medico_nombre: str | None = None


@dataclass(frozen=True)
class Appointment:
    id: str
    key: str
    patient_user_id: str
    horario_id: str
    servicio_id: str
    estado: str
    motivo: str | None
    expiracion_reserva: datetime | None


@dataclass(frozen=True)
class AppointmentSummary:
    """Listing row. The patient is set on doctor agenda and admin listing,
    never on a patient's."""

    id: str
    estado: str
    inicio_utc: datetime
    fin_utc: datetime
    paciente_id: str | None = None
    paciente_nombre: str | None = None
    medico_id: str | None = None
    medico_nombre: str | None = None
    medico_registro: str | None = None
    medico_telefono: str | None = None
    tiene_aprobacion: bool = False
    # The patient's own listing: the confirmed payment to refund,
    # and the state of the newest refund.
    pago_id: str | None = None
    reembolso_estado: str | None = None


@dataclass(frozen=True)
class ReserveCommand:
    key: str
    patient_user_id: str
    horario_id: str
    servicio_id: str
    motivo: str | None
    now: datetime
    hold_until: datetime


class ReserveStatus(Enum):
    CREATED = "created"
    SLOT_UNAVAILABLE = "slot_unavailable"
    NO_PATIENT_PROFILE = "no_patient_profile"


@dataclass(frozen=True)
class ReserveOutcome:
    status: ReserveStatus
    appointment: Appointment | None


@dataclass(frozen=True)
class CancelCommand:
    actor_user_id: str
    appointment_id: str
    now: datetime
    # True: the actor cancels as the doctor of the appointment's agenda. False: as its patient.
    as_medico: bool = False
    # Only set for a doctor's cancellation; a patient's cancellation carries None.
    motivo_codigo: str | None = None
    motivo_texto: str | None = None


class CancelStatus(Enum):
    CANCELLED = "cancelled"
    NOT_FOUND_OR_FORBIDDEN = "not_found_or_forbidden"
    NOT_CANCELLABLE = "not_cancellable"
    WINDOW_CLOSED = "window_closed"
    REQUIRES_APPROVAL = "requires_approval"


@dataclass(frozen=True)
class CancelOutcome:
    status: CancelStatus
    appointment: Appointment | None
    # Only for REQUIRES_APPROVAL: the stable codes that blocked the doctor's cancellation.
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CloseConsultationCommand:
    actor_user_id: str
    appointment_id: str
    now: datetime
    allowed_states: frozenset[str]
    observaciones: str | None
    plan_tratamiento: str | None


@dataclass(frozen=True)
class OpenConsultationCommand:
    actor_user_id: str
    appointment_id: str
    now: datetime
    allowed_states: frozenset[str]


class ConsultationStatus(Enum):
    CREATED = "created"
    EXISTING = "existing"
    NOT_FOUND_OR_FORBIDDEN = "not_found_or_forbidden"
    STATE_NOT_ALLOWED = "state_not_allowed"
    CLOSED = "closed"
    ALREADY_CLOSED = "already_closed"


@dataclass(frozen=True)
class Consultation:
    id: str
    cita_id: str
    estado: str
    inicio_atencion: datetime | None
    motivo_consulta: str | None
    observaciones: str | None = None
    plan_tratamiento: str | None = None
    fin_atencion: datetime | None = None


@dataclass(frozen=True)
class ConsultationOutcome:
    status: ConsultationStatus
    consultation: Consultation | None


class ApprovalStatus(Enum):
    APPROVED = "approved"
    NOT_FOUND = "not_found"
    NOT_CANCELLABLE = "not_cancellable"
    WINDOW_CLOSED = "window_closed"
    ALREADY_APPROVED = "already_approved"


@dataclass(frozen=True)
class Consultorio:
    id: str
    nombre: str
    sede: str


@dataclass(frozen=True)
class ConsultorioDetail:
    id: str
    nombre: str
    activo: bool


@dataclass(frozen=True)
class SedeDetail:
    id: str
    nombre: str
    direccion: str | None
    activo: bool
    consultorios: tuple[ConsultorioDetail, ...]


@dataclass(frozen=True)
class ConsultorioOwned:
    """A consultorio with the sede it belongs to: what the admin edits and checks."""

    id: str
    sede_id: str
    nombre: str
    activo: bool


@dataclass(frozen=True)
class DoctorProfile:
    medico_id: str
    duracion_cita_min: int
    activo: bool


@dataclass(frozen=True)
class ProposedSlot:
    inicio_utc: datetime
    fin_utc: datetime
    franja: str


class OfficeStore(Protocol):
    """Sedes and consultorios as an administrator manages them. Writes return False (not raise)
    only for a duplicate consultorio name in the same sede; the service turns that into a 409."""

    def list_sedes(self) -> list[SedeDetail]: ...

    def get_sede(self, sede_id: str) -> SedeDetail | None: ...

    def insert_sede(self, sede_id: str, nombre: str, direccion: str | None) -> None: ...

    def update_sede(
        self, sede_id: str, nombre: str, direccion: str | None, activo: bool
    ) -> None: ...

    def get_consultorio(self, consultorio_id: str) -> ConsultorioOwned | None: ...

    def insert_consultorio(self, consultorio_id: str, sede_id: str, nombre: str) -> bool: ...

    def update_consultorio(self, consultorio_id: str, nombre: str, activo: bool) -> bool: ...

    def has_future_bookings(self, consultorio_ids: list[str], now: datetime) -> bool:
        """True when an active booking holds a slot in these consultorios starting after `now`."""
        ...


class SchedulingStore(Protocol):
    def doctor_profile_for_user(self, user_id: str) -> DoctorProfile | None: ...

    def doctor_profile_by_id(self, medico_id: str) -> DoctorProfile | None: ...

    def get_work_blocks(self, medico_id: str, on: date) -> list[WorkBlock]:
        """The hours in force on `on`: the latest version effective on or before that day."""
        ...

    def declared_hours_exist(self, medico_id: str) -> bool: ...

    def list_consultorios(self) -> list[Consultorio]: ...

    def replace_work_blocks(
        self, medico_id: str, blocks: list[WorkBlock], effective_from: date, now: datetime
    ) -> None:
        """Set the hours from `effective_from` on. A later change for the same day replaces it."""
        ...

    def consultorio_is_active(self, consultorio_id: str) -> bool: ...

    def insert_generated_slots(
        self,
        medico_id: str,
        consultorio_id: str,
        desde: date,
        hasta: date,
        slots: list[tuple[datetime, datetime]],
    ) -> tuple[int, int]:
        """Create the slots that do not exist yet: (created, already there)."""
        ...

    def service_franja(self, servicio_id: str) -> str | None: ...

    def list_available(
        self,
        start: datetime,
        end: datetime,
        medico_id: str | None,
        limit: int,
        especialidad_id: int | None = None,
        franja: str | None = None,
    ) -> list[AvailableSlot]: ...

    def find_slot(self, horario_id: str) -> datetime | None:
        """Return the slot start (timezone-aware UTC), or None when it does not exist."""
        ...

    def service_is_active(self, servicio_id: str) -> bool: ...

    def find_appointment_by_key(self, key: str) -> Appointment | None: ...

    def has_doctor_profile(self, user_id: str) -> bool: ...

    def list_patient_appointments(
        self, patient_user_id: str, estado: str | None, limit: int, offset: int
    ) -> list[AppointmentSummary]:
        """One page of the patient's own appointments, ordered by slot start then id."""
        ...

    def list_agenda_appointments(
        self, doctor_user_id: str, estado: str | None, limit: int, offset: int
    ) -> list[AppointmentSummary]:
        """One page of appointments booked on the doctor's own agendas."""
        ...

    def list_cancellation_candidates(
        self,
        estado: str | None,
        limit: int,
        offset: int,
        medico_registro: str | None = None,
    ) -> list[AppointmentSummary]:
        """One page of appointments for cancellation review. Doctor identity only: no patient
        and no doctor phone are returned."""
        ...

    def record_audit(self, event: AuditEvent) -> None: ...

    def reserve(self, command: ReserveCommand) -> ReserveOutcome: ...

    def open_consultation(self, command: OpenConsultationCommand) -> ConsultationOutcome:
        """Open (once) the consultation of an appointment of the actor's own agenda."""
        ...

    def close_consultation(self, command: CloseConsultationCommand) -> ConsultationOutcome:
        """Record the attention notes and close the consultation (CERRADA), once."""
        ...

    def cancel(self, command: CancelCommand) -> CancelOutcome: ...

    def approve_doctor_cancellation(
        self, appointment_id: str, approver_user_id: str, motivo: str, now: datetime
    ) -> ApprovalStatus: ...

    def expire_overdue_holds(self, now: datetime, limit: int) -> int:
        """Expire up to ``limit`` pending holds whose hold ended by ``now``; return how many."""
        ...
