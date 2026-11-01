"""Scheduling use cases against an in-memory store: rules, idempotency and ownership."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.ports import (
    Appointment,
    AvailableSlot,
    CancelCommand,
    CancelOutcome,
    CancelStatus,
    ReserveCommand,
    ReserveOutcome,
    ReserveStatus,
)
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=2)
KEY = "booking-key-0001"


def _id() -> str:
    return str(uuid.uuid4())


def _actor(*, permissions=frozenset({"citas.reservar"}), user_id=None) -> AccessProfile:
    return AccessProfile(
        user_id=user_id or _id(),
        status="ACTIVO",
        roles=frozenset({"PACIENTE"}),
        permissions=permissions,
    )


@dataclass
class FakeStore:
    """In-memory stand-in for the SQL adapter, honouring the same contract."""

    slots: dict[str, datetime] = field(default_factory=dict)
    services: set[str] = field(default_factory=set)
    patients: set[str] = field(default_factory=set)  # user ids with a patient profile
    appointments: dict[str, Appointment] = field(default_factory=dict)
    reserve_calls: int = 0
    taken: set[str] = field(default_factory=set)
    slot_id: str = ""

    def list_available(self, start, end, medico_id, limit):
        return [
            AvailableSlot(horario_id=sid, medico_id="medico", inicio_utc=ini, fin_utc=ini)
            for sid, ini in sorted(self.slots.items(), key=lambda item: item[1])
            if start <= ini < end and sid not in self.taken
        ][:limit]

    def find_slot(self, horario_id):
        if horario_id not in self.slots:
            return None
        return self.slots[horario_id]

    def service_is_active(self, servicio_id):
        return servicio_id in self.services

    def find_appointment_by_key(self, key):
        return next((a for a in self.appointments.values() if a.key == key), None)

    def reserve(self, command: ReserveCommand) -> ReserveOutcome:
        self.reserve_calls += 1
        if command.patient_user_id not in self.patients:
            return ReserveOutcome(ReserveStatus.NO_PATIENT_PROFILE, None)
        if command.horario_id in self.taken:
            return ReserveOutcome(ReserveStatus.SLOT_UNAVAILABLE, None)
        self.taken.add(command.horario_id)
        appointment = Appointment(
            id=_id(),
            key=command.key,
            patient_user_id=command.patient_user_id,
            horario_id=command.horario_id,
            servicio_id=command.servicio_id,
            estado="PENDIENTE_PAGO",
            motivo=command.motivo,
            expiracion_reserva=command.hold_until,
        )
        self.appointments[appointment.id] = appointment
        return ReserveOutcome(ReserveStatus.CREATED, appointment)

    def cancel(self, command: CancelCommand) -> CancelOutcome:
        appointment = self.appointments.get(command.appointment_id)
        if appointment is None or appointment.patient_user_id != command.actor_user_id:
            return CancelOutcome(CancelStatus.NOT_FOUND_OR_FORBIDDEN, None)
        if appointment.estado not in ("PENDIENTE_PAGO", "CONFIRMADA"):
            return CancelOutcome(CancelStatus.NOT_CANCELLABLE, None)
        cancelled = Appointment(**{**appointment.__dict__, "estado": "CANCELADA"})
        self.appointments[cancelled.id] = cancelled
        self.taken.discard(cancelled.horario_id)
        return CancelOutcome(CancelStatus.CANCELLED, cancelled)


@pytest.fixture
def store() -> FakeStore:
    fake = FakeStore(slot_id=_id())
    fake.slots[fake.slot_id] = FUTURE
    fake.services.add("servicio-activo")
    return fake


@pytest.fixture
def patient(store: FakeStore) -> AccessProfile:
    actor = _actor()
    store.patients.add(actor.user_id)
    return actor


def _service(store: FakeStore) -> SchedulingService:
    return SchedulingService(store=store, clock=lambda: NOW, hold_minutes=15)


def _book(service, actor, store, **overrides):
    values = {
        "idempotency_key": KEY,
        "horario_id": store.slot_id,
        "servicio_id": "servicio-activo",
        "motivo": None,
    }
    values.update(overrides)
    return service.book(actor, **values)


# ---------- availability ----------


def test_availability_lists_only_free_slots_inside_the_window(store, patient) -> None:
    service = _service(store)
    start, end = NOW, NOW + timedelta(days=7)

    assert len(service.list_availability(patient, start, end)) == 1

    _book(service, patient, store)
    assert service.list_availability(patient, start, end) == []


def test_availability_rejects_windows_longer_than_31_days(store, patient) -> None:
    with pytest.raises(ValidationFailed):
        _service(store).list_availability(patient, NOW, NOW + timedelta(days=32))


def test_availability_rejects_empty_or_reversed_windows(store, patient) -> None:
    with pytest.raises(ValidationFailed):
        _service(store).list_availability(patient, NOW, NOW)


def test_availability_requires_the_booking_permission(store) -> None:
    outsider = _actor(permissions=frozenset())

    with pytest.raises(Forbidden):
        _service(store).list_availability(outsider, NOW, NOW + timedelta(days=1))


# ---------- booking ----------


@pytest.mark.parametrize("bad_key", ["", "short", "has spaces in it!", "x" * 101])
def test_booking_rejects_missing_or_malformed_idempotency_keys(store, patient, bad_key) -> None:
    with pytest.raises(ValidationFailed):
        _book(_service(store), patient, store, idempotency_key=bad_key)


def test_booking_rejects_unknown_slots(store, patient) -> None:
    with pytest.raises(NotFound):
        _book(_service(store), patient, store, horario_id=_id())


def test_booking_rejects_slots_already_in_the_past(store, patient) -> None:
    past = _id()
    store.slots[past] = NOW - timedelta(minutes=1)

    with pytest.raises(ValidationFailed):
        _book(_service(store), patient, store, horario_id=past)


def test_booking_rejects_inactive_or_unknown_services(store, patient) -> None:
    with pytest.raises(NotFound):
        _book(_service(store), patient, store, servicio_id="servicio-inexistente")


def test_booking_without_patient_profile_is_forbidden(store) -> None:
    stranger = _actor()  # has the permission but no patient record

    with pytest.raises(Forbidden):
        _book(_service(store), stranger, store)


def test_booking_creates_one_pending_appointment_with_a_hold(store, patient) -> None:
    result = _book(_service(store), patient, store)

    assert result.replayed is False
    assert result.appointment.estado == "PENDIENTE_PAGO"
    assert result.appointment.expiracion_reserva == NOW + timedelta(minutes=15)


def test_same_key_and_same_request_replays_without_a_second_reservation(store, patient) -> None:
    service = _service(store)
    first = _book(service, patient, store)

    second = _book(service, patient, store)

    assert second.replayed is True
    assert second.appointment.id == first.appointment.id
    assert store.reserve_calls == 1


def test_same_key_with_a_different_request_is_rejected(store, patient) -> None:
    service = _service(store)
    _book(service, patient, store)
    other_slot = _id()
    store.slots[other_slot] = FUTURE

    with pytest.raises(Conflict):
        _book(service, patient, store, horario_id=other_slot)


def test_competing_booking_for_a_taken_slot_conflicts(store, patient) -> None:
    service = _service(store)
    _book(service, patient, store)
    rival = _actor()
    store.patients.add(rival.user_id)

    with pytest.raises(Conflict):
        _book(service, rival, store, idempotency_key="another-key-0002")


# ---------- cancellation ----------


def test_patient_cancels_own_appointment_and_frees_the_slot(store, patient) -> None:
    service = _service(store)
    booked = _book(service, patient, store).appointment

    cancelled = service.cancel(patient, booked.id)

    assert cancelled.estado == "CANCELADA"
    assert store.slot_id not in store.taken


def test_cancelling_someone_elses_appointment_is_forbidden(store, patient) -> None:
    booked = _book(_service(store), patient, store).appointment
    other = _actor()
    store.patients.add(other.user_id)

    with pytest.raises(Forbidden):
        _service(store).cancel(other, booked.id)


def test_missing_and_forbidden_appointments_look_identical(store, patient) -> None:
    other = _actor()
    store.patients.add(other.user_id)
    with pytest.raises(Forbidden) as missing:
        _service(store).cancel(patient, _id())
    booked = _book(_service(store), patient, store).appointment
    with pytest.raises(Forbidden) as forbidden:
        _service(store).cancel(other, booked.id)

    assert missing.value.detail == forbidden.value.detail


def test_cancelling_twice_conflicts(store, patient) -> None:
    service = _service(store)
    booked = _book(service, patient, store).appointment
    service.cancel(patient, booked.id)

    with pytest.raises(Conflict):
        service.cancel(patient, booked.id)
