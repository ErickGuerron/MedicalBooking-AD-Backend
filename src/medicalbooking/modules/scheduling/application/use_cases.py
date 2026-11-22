"""Scheduling use cases. Business rules live here, not in routes or SQL."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.scheduling.application.ports import (
    Appointment,
    AppointmentSummary,
    ApprovalStatus,
    AvailableSlot,
    CancelCommand,
    CancelStatus,
    CloseConsultationCommand,
    Consultation,
    ConsultationStatus,
    OpenConsultationCommand,
    ReserveCommand,
    ReserveStatus,
    SchedulingStore,
)
from medicalbooking.modules.scheduling.domain.policies import (
    CONSULTATION_APPOINTMENT_STATES,
    PERM_BOOK,
    PERM_CANCEL_MEDICO,
    PERM_OPEN_CONSULTATION,
    PERM_REVIEW_CANCELLATIONS,
    ROLE_PATIENT,
    is_same_request,
    normalize_clinical_note,
    normalize_patient_reason,
    validate_approval_reason,
    validate_cancellation_reason,
    validate_estado,
    validate_idempotency_key,
    validate_page,
    validate_window,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

MAX_RESULTS = 200
EXPIRY_BATCH_SIZE = 100


@dataclass(frozen=True)
class BookingResult:
    appointment: Appointment
    replayed: bool


class SchedulingService:
    def __init__(
        self,
        store: SchedulingStore,
        clock: Callable[[], datetime],
        hold_minutes: int,
        expiry_batch_size: int = EXPIRY_BATCH_SIZE,
    ) -> None:
        self._store = store
        self._clock = clock
        self._hold = timedelta(minutes=hold_minutes)
        self._expiry_batch_size = expiry_batch_size

    def expire_overdue_holds(self) -> int:
        """System job, not an HTTP action: release holds whose payment window has ended.

        Batches keep each transaction short. Stops when a batch is not full.
        """
        now = self._clock()
        total = 0
        while True:
            expired = self._store.expire_overdue_holds(now, self._expiry_batch_size)
            total += expired
            if expired < self._expiry_batch_size:
                return total

    def list_availability(
        self,
        actor: AccessProfile,
        start: datetime,
        end: datetime,
        medico_id: str | None = None,
        especialidad_id: int | None = None,
    ) -> list[AvailableSlot]:
        _require(actor, PERM_BOOK)
        validate_window(start, end)
        # Past slots are never offered, even when the window starts in the past.
        effective_start = max(start, self._clock())
        if effective_start >= end:
            return []
        return self._store.list_available(
            effective_start, end, medico_id, MAX_RESULTS, especialidad_id=especialidad_id
        )

    def book(
        self,
        actor: AccessProfile,
        *,
        idempotency_key: str,
        horario_id: str,
        servicio_id: str,
        motivo: str | None,
    ) -> BookingResult:
        _require(actor, PERM_BOOK)
        validate_idempotency_key(idempotency_key)
        horario_id, servicio_id = str(horario_id), str(servicio_id)

        existing = self._store.find_appointment_by_key(idempotency_key)
        if existing is not None:
            return self._replay_or_conflict(existing, actor, horario_id, servicio_id, motivo)

        slot_start = self._store.find_slot(horario_id)
        if slot_start is None:
            raise NotFound("Schedule slot not found")
        now = self._clock()
        if slot_start <= now:
            raise ValidationFailed("The schedule slot has already started")
        if not self._store.service_is_active(servicio_id):
            raise NotFound("Medical service not found")

        outcome = self._store.reserve(
            ReserveCommand(
                key=idempotency_key,
                patient_user_id=actor.user_id,
                horario_id=horario_id,
                servicio_id=servicio_id,
                motivo=motivo,
                now=now,
                hold_until=now + self._hold,
            )
        )
        if outcome.status is ReserveStatus.NO_PATIENT_PROFILE:
            raise Forbidden("A patient profile is required to book appointments")
        if outcome.status is ReserveStatus.SLOT_UNAVAILABLE:
            # A concurrent request with the same key may have won the race: answer with its outcome.
            winner = self._store.find_appointment_by_key(idempotency_key)
            if winner is not None:
                return self._replay_or_conflict(winner, actor, horario_id, servicio_id, motivo)
            raise Conflict("The schedule slot is no longer available")
        assert outcome.appointment is not None
        return BookingResult(appointment=outcome.appointment, replayed=False)

    def cancel(
        self,
        actor: AccessProfile,
        appointment_id: str,
        *,
        motivo_codigo: str | None = None,
        motivo_texto: str | None = None,
    ) -> Appointment:
        """A patient cancels an own appointment; a doctor cancels one of an own agenda.

        A doctor must give a coded reason. A patient may give an optional text, stored with no code.
        Both paths answer the same 403 for a missing or foreign appointment.
        """
        as_medico = _cancels_as_medico(actor)
        if as_medico:
            validate_cancellation_reason(motivo_codigo, motivo_texto)
        else:
            motivo_codigo = None
            motivo_texto = normalize_patient_reason(motivo_texto)
        outcome = self._store.cancel(
            CancelCommand(
                actor_user_id=actor.user_id,
                appointment_id=str(appointment_id),
                now=self._clock(),
                as_medico=as_medico,
                motivo_codigo=motivo_codigo,
                motivo_texto=motivo_texto,
            )
        )
        if outcome.status is CancelStatus.NOT_FOUND_OR_FORBIDDEN:
            # Same answer for missing and foreign appointments: identifiers do not leak.
            raise Forbidden("Access to this resource is not allowed")
        if outcome.status is CancelStatus.NOT_CANCELLABLE:
            raise Conflict("The appointment can no longer be cancelled")
        if outcome.status is CancelStatus.REQUIRES_APPROVAL:
            # Stable codes: the client matches them, so they are not translated.
            raise Conflict(",".join(outcome.blockers))
        assert outcome.appointment is not None
        return outcome.appointment

    def open_consultation(
        self, actor: AccessProfile, appointment_id: str
    ) -> tuple[Consultation, bool]:
        """The doctor of the agenda opens the consultation of one own appointment.

        Idempotent: a repeat returns the existing consultation (created=False). Missing and
        foreign appointments answer the same 403; a state outside the allowed set is a 409.
        """
        _require(actor, PERM_OPEN_CONSULTATION)
        outcome = self._store.open_consultation(
            OpenConsultationCommand(
                actor_user_id=actor.user_id,
                appointment_id=str(appointment_id),
                now=self._clock(),
                allowed_states=CONSULTATION_APPOINTMENT_STATES,
            )
        )
        if outcome.status is ConsultationStatus.NOT_FOUND_OR_FORBIDDEN:
            raise Forbidden("Access to this resource is not allowed")
        if outcome.status is ConsultationStatus.STATE_NOT_ALLOWED:
            raise Conflict("The appointment is not in a state that allows a consultation")
        assert outcome.consultation is not None
        return outcome.consultation, outcome.status is ConsultationStatus.CREATED

    def close_consultation(
        self,
        actor: AccessProfile,
        appointment_id: str,
        *,
        observaciones: object,
        plan_tratamiento: object,
    ) -> Consultation:
        """The doctor of the agenda registers the attention: notes and treatment plan, then the
        consultation is closed (CERRADA). Closing is final: a second attempt is a 409."""
        _require(actor, PERM_OPEN_CONSULTATION)
        diagnosis = normalize_clinical_note("observaciones", observaciones)
        if diagnosis is None:
            raise ValidationFailed("'observaciones' (the diagnosis) is required")
        outcome = self._store.close_consultation(
            CloseConsultationCommand(
                actor_user_id=actor.user_id,
                appointment_id=str(appointment_id),
                now=self._clock(),
                allowed_states=CONSULTATION_APPOINTMENT_STATES,
                observaciones=diagnosis,
                plan_tratamiento=normalize_clinical_note("plan_tratamiento", plan_tratamiento),
            )
        )
        if outcome.status is ConsultationStatus.NOT_FOUND_OR_FORBIDDEN:
            raise Forbidden("Access to this resource is not allowed")
        if outcome.status is ConsultationStatus.STATE_NOT_ALLOWED:
            raise Conflict("The appointment is not in a state that allows a consultation")
        if outcome.status is ConsultationStatus.ALREADY_CLOSED:
            raise Conflict("The consultation is already registered")
        assert outcome.consultation is not None
        return outcome.consultation

    def approve_doctor_cancellation(
        self, actor: AccessProfile, appointment_id: str, *, motivo: str | None
    ) -> None:
        """An admin records the approval that lets a doctor cancel one appointment beyond the
        monthly quota or inside the 24-hour notice. The doctor still performs the cancellation."""
        _require(actor, PERM_REVIEW_CANCELLATIONS)
        validate_approval_reason(motivo)
        status = self._store.approve_doctor_cancellation(
            str(appointment_id), actor.user_id, str(motivo), self._clock()
        )
        if status is ApprovalStatus.NOT_FOUND:
            raise NotFound("Appointment not found")
        if status is ApprovalStatus.NOT_CANCELLABLE:
            raise Conflict("The appointment can no longer be cancelled")
        if status is ApprovalStatus.ALREADY_APPROVED:
            raise Conflict("The appointment already has an approval")

    def list_appointments(
        self,
        actor: AccessProfile,
        *,
        estado: str | None,
        limit: int,
        offset: int,
    ) -> list[AppointmentSummary]:
        """Appointments the actor owns: a patient's own, or those of the doctor's agenda.

        Patients are checked first, so a user with both roles sees only their own
        appointments as a patient. Any other actor is refused before paging is checked.
        Administrators review cancellations through list_cancellation_candidates instead.
        """
        if ROLE_PATIENT in actor.roles:
            query = self._store.list_patient_appointments
        elif self._store.has_doctor_profile(actor.user_id):
            query = self._store.list_agenda_appointments
        else:
            raise Forbidden("Access to this resource is not allowed")
        validate_page(limit, offset)
        if estado is not None:
            validate_estado(estado)
        return query(actor.user_id, estado, limit, offset)

    def list_cancellation_candidates(
        self,
        actor: AccessProfile,
        *,
        estado: str | None,
        medico_registro: str | None,
        limit: int,
        offset: int,
    ) -> list[AppointmentSummary]:
        """Appointments for the administrator's cancellation review: doctor identity only.

        Every read and every denial is audited. A blank registration means no filter.
        """
        if PERM_REVIEW_CANCELLATIONS not in actor.permissions:
            self._audit(actor, "DENEGADO")
            raise Forbidden("Access to this resource is not allowed")
        validate_page(limit, offset)
        if estado is not None:
            validate_estado(estado)
        registration = (medico_registro or "").strip() or None
        items = self._store.list_cancellation_candidates(estado, limit, offset, registration)
        self._audit(actor, "OK")
        return items

    def _audit(self, actor: AccessProfile, resultado: str) -> None:
        self._store.record_audit(
            AuditEvent(
                actor_user_id=actor.user_id,
                paciente_id=None,
                entidad="cita",
                entidad_id=None,
                accion="listar_revision_cancelacion",
                resultado=resultado,
                occurred_at=self._clock(),
            )
        )

    @staticmethod
    def _replay_or_conflict(
        existing: Appointment,
        actor: AccessProfile,
        horario_id: str,
        servicio_id: str,
        motivo: str | None,
    ) -> BookingResult:
        if not is_same_request(
            existing_patient=existing.patient_user_id,
            existing_slot=existing.horario_id,
            existing_service=existing.servicio_id,
            existing_reason=existing.motivo,
            patient=actor.user_id,
            slot=horario_id,
            service=servicio_id,
            reason=motivo,
        ):
            raise Conflict("Idempotency-Key was already used with a different request")
        return BookingResult(appointment=existing, replayed=True)


def _require(actor: AccessProfile, permission: str) -> None:
    if permission not in actor.permissions:
        raise Forbidden("Access to this resource is not allowed")


def _cancels_as_medico(actor: AccessProfile) -> bool:
    """Patients are checked first, as for listing. Only a doctor with the cancel permission
    may cancel as the agenda's doctor; anyone else is refused before the store is reached."""
    if PERM_BOOK in actor.permissions:
        return False
    if PERM_CANCEL_MEDICO in actor.permissions:
        return True
    raise Forbidden("Access to this resource is not allowed")
