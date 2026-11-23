"""Doctor work hours and slot generation rules (unit, in-memory store)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.ports import DoctorProfile
from medicalbooking.modules.scheduling.application.work_hours import WorkHoursService
from medicalbooking.modules.scheduling.domain.clinic_hours import WorkBlock
from medicalbooking.modules.scheduling.domain.policies import (
    PERM_DECLARE_HOURS,
    PERM_GENERATE_SLOTS,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

# Monday 23 November 2026, 07:00 branch time (12:00 UTC): the clinic has not opened yet.
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
MONDAY = date(2026, 11, 23)
CONSULTORIO = str(uuid.uuid4())


@dataclass
class FakeHoursStore:
    doctors_by_user: dict[str, DoctorProfile] = field(default_factory=dict)
    doctors: dict[str, DoctorProfile] = field(default_factory=dict)
    blocks: dict[str, list[WorkBlock]] = field(default_factory=dict)
    consultorios: set[str] = field(default_factory=set)
    slots: set[tuple[str, datetime]] = field(default_factory=set)
    audit: list = field(default_factory=list)

    def doctor_profile_for_user(self, user_id):
        return self.doctors_by_user.get(user_id)

    def doctor_profile_by_id(self, medico_id):
        return self.doctors.get(medico_id)

    def get_work_blocks(self, medico_id):
        return list(self.blocks.get(medico_id, []))

    def replace_work_blocks(self, medico_id, blocks, now):
        self.blocks[medico_id] = list(blocks)

    def consultorio_is_active(self, consultorio_id):
        return consultorio_id in self.consultorios

    def insert_generated_slots(self, medico_id, consultorio_id, desde, hasta, slots):
        created = 0
        for start, _end in slots:
            if (medico_id, start) not in self.slots:
                self.slots.add((medico_id, start))
                created += 1
        return created, len(slots) - created

    def record_audit(self, event):
        self.audit.append(event)


def _doctor(store: FakeHoursStore, *, activo: bool = True, duration: int = 30) -> DoctorProfile:
    medico = DoctorProfile(medico_id=str(uuid.uuid4()), duracion_cita_min=duration, activo=activo)
    store.doctors[medico.medico_id] = medico
    store.consultorios.add(CONSULTORIO)
    return medico


def _doctor_user(store: FakeHoursStore, medico: DoctorProfile) -> AccessProfile:
    user = str(uuid.uuid4())
    store.doctors_by_user[user] = medico
    return AccessProfile(
        status="ACTIVO",
        user_id=user,
        roles=frozenset({"MEDICO"}),
        permissions=frozenset({PERM_DECLARE_HOURS}),
    )


def _admin() -> AccessProfile:
    return AccessProfile(
        status="ACTIVO",
        user_id=str(uuid.uuid4()),
        roles=frozenset({"ADMINISTRADOR"}),
        permissions=frozenset({PERM_GENERATE_SLOTS}),
    )


def _service(store: FakeHoursStore) -> WorkHoursService:
    return WorkHoursService(store=store, clock=lambda: NOW)


def _block(day: int, start: str, end: str) -> WorkBlock:
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    return WorkBlock(dia_semana=day, inicio=time(h1, m1), fin=time(h2, m2))


# ---------- the doctor declares the hours of work ----------


def test_a_doctor_declares_the_hours_they_work() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    doctor = _doctor_user(store, medico)

    _service(store).set_my_work_hours(
        doctor, [_block(0, "08:00", "10:00"), _block(0, "12:00", "17:00")]
    )

    assert store.get_work_blocks(medico.medico_id) == [
        _block(0, "08:00", "10:00"),
        _block(0, "12:00", "17:00"),
    ]
    assert store.audit and store.audit[-1].accion == "declarar_horario"


def test_declared_hours_outside_the_clinic_are_refused() -> None:
    store = FakeHoursStore()
    doctor = _doctor_user(store, _doctor(store))

    with pytest.raises(ValidationFailed):
        _service(store).set_my_work_hours(doctor, [_block(0, "07:00", "09:00")])


def test_only_a_doctor_declares_their_own_hours() -> None:
    store = FakeHoursStore()
    stranger = AccessProfile(
        status="ACTIVO",
        user_id=str(uuid.uuid4()),
        roles=frozenset({"PACIENTE"}),
        permissions=frozenset({PERM_DECLARE_HOURS}),
    )

    with pytest.raises(Forbidden):
        _service(store).set_my_work_hours(stranger, [_block(0, "08:00", "09:00")])


def test_an_administrator_cannot_declare_hours_for_a_doctor() -> None:
    store = FakeHoursStore()
    _doctor(store)

    with pytest.raises(Forbidden):
        _service(store).set_my_work_hours(_admin(), [_block(0, "08:00", "09:00")])


# ---------- the administrator proposes and generates slots ----------


def test_the_proposal_offers_only_the_windows_of_the_day() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "10:30"), _block(0, "12:00", "13:00")]

    proposal = _service(store).propose_slots(_admin(), medico.medico_id, MONDAY, MONDAY)

    starts = [
        item.inicio_utc.astimezone(UTC).hour * 60 + item.inicio_utc.minute for item in proposal
    ]
    # Branch 08:00 to 09:30 (UTC 13:00 to 14:30), then 12:00 and 12:30 (UTC 17:00 and 17:30).
    assert starts == [13 * 60, 13 * 60 + 30, 14 * 60, 14 * 60 + 30, 17 * 60, 17 * 60 + 30]
    assert [item.franja for item in proposal] == ["MANANA"] * 4 + ["TARDE"] * 2


def test_a_slot_that_already_started_is_not_proposed() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]
    # The clock says 08:20 branch time on the same day.
    service = WorkHoursService(
        store=store, clock=lambda: datetime(2026, 11, 23, 13, 20, tzinfo=UTC)
    )

    proposal = service.propose_slots(_admin(), medico.medico_id, MONDAY, MONDAY)

    assert [
        (item.inicio_utc.astimezone(UTC).hour, item.inicio_utc.astimezone(UTC).minute)
        for item in proposal
    ] == [(13, 30)]


def test_a_doctor_without_declared_hours_cannot_be_scheduled() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)

    with pytest.raises(Conflict):
        _service(store).propose_slots(_admin(), medico.medico_id, MONDAY, MONDAY)


def test_the_range_cannot_be_longer_than_31_days_nor_in_the_past() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]
    service = _service(store)

    with pytest.raises(ValidationFailed):
        service.propose_slots(_admin(), medico.medico_id, MONDAY, date(2026, 12, 25))
    with pytest.raises(ValidationFailed):
        service.propose_slots(_admin(), medico.medico_id, date(2026, 11, 1), date(2026, 11, 2))
    with pytest.raises(ValidationFailed):
        service.propose_slots(_admin(), medico.medico_id, MONDAY, date(2026, 11, 20))


def test_an_unknown_or_inactive_doctor_is_not_found() -> None:
    store = FakeHoursStore()
    inactive = _doctor(store, activo=False)
    store.blocks[inactive.medico_id] = [_block(0, "08:00", "09:00")]

    with pytest.raises(NotFound):
        _service(store).propose_slots(_admin(), str(uuid.uuid4()), MONDAY, MONDAY)
    with pytest.raises(NotFound):
        _service(store).propose_slots(_admin(), inactive.medico_id, MONDAY, MONDAY)


def test_generation_creates_the_proposed_slots_and_is_idempotent() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]
    service = _service(store)

    first = service.generate_slots(_admin(), medico.medico_id, CONSULTORIO, MONDAY, MONDAY)
    second = service.generate_slots(_admin(), medico.medico_id, CONSULTORIO, MONDAY, MONDAY)

    assert first == (2, 0)
    assert second == (0, 2)


def test_generation_needs_an_active_consultorio() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]

    with pytest.raises(NotFound):
        _service(store).generate_slots(
            _admin(), medico.medico_id, str(uuid.uuid4()), MONDAY, MONDAY
        )


def test_only_an_administrator_generates_slots() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]
    doctor = _doctor_user(store, medico)

    with pytest.raises(Forbidden):
        _service(store).generate_slots(doctor, medico.medico_id, CONSULTORIO, MONDAY, MONDAY)
