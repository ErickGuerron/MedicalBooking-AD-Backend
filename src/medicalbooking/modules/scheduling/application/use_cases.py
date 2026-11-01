"""Scheduling use cases. Business rules live here, not in routes or SQL."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.ports import (
    Appointment,
    AvailableSlot,
    CancelCommand,
    CancelStatus,
    ReserveCommand,
    ReserveStatus,
    SchedulingStore,
)
from medicalbooking.modules.scheduling.domain.policies import (
    PERM_BOOK,
    is_same_request,
    validate_idempotency_key,
    validate_window,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

MAX_RESULTS = 200


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
    ) -> None:
        self._store = store
        self._clock = clock
        self._hold = timedelta(minutes=hold_minutes)

    def list_availability(
        self,
        actor: AccessProfile,
        start: datetime,
        end: datetime,
        medico_id: str | None = None,
    ) -> list[AvailableSlot]:
        _require(actor, PERM_BOOK)
        validate_window(start, end)
        # Past slots are never offered, even when the window starts in the past.
        effective_start = max(start, self._clock())
        if effective_start >= end:
            return []
        return self._store.list_available(effective_start, end, medico_id, MAX_RESULTS)

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

    def cancel(self, actor: AccessProfile, appointment_id: str) -> Appointment:
        _require(actor, PERM_BOOK)
        outcome = self._store.cancel(
            CancelCommand(
                actor_user_id=actor.user_id,
                appointment_id=str(appointment_id),
                now=self._clock(),
            )
        )
        if outcome.status is CancelStatus.NOT_FOUND_OR_FORBIDDEN:
            # Same answer for missing and foreign appointments: identifiers do not leak.
            raise Forbidden("Access to this resource is not allowed")
        if outcome.status is CancelStatus.NOT_CANCELLABLE:
            raise Conflict("The appointment can no longer be cancelled")
        assert outcome.appointment is not None
        return outcome.appointment

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
