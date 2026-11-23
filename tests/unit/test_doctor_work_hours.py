"""Doctor work hours and slot generation rules (unit, in-memory store)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

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


# ---------- the administrator plans and creates a week of one-hour slots ----------

NEXT_MONDAY = date(2026, 11, 30)


def test_the_week_offers_one_hour_slots_in_each_window() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "10:30"), _block(0, "12:00", "13:00")]

    proposal = _service(store).propose_week(_admin(), medico.medico_id, MONDAY)

    utc_minutes = [
        item.inicio_utc.astimezone(UTC).hour * 60 + item.inicio_utc.minute for item in proposal
    ]
    # Branch 08:00 and 09:00 (UTC 13:00, 14:00) are samples; 12:00 (UTC 17:00) a consultation.
    assert utc_minutes == [13 * 60, 14 * 60, 17 * 60]
    assert [item.franja for item in proposal] == ["MANANA", "MANANA", "TARDE"]
    assert all(item.fin_utc - item.inicio_utc == timedelta(minutes=60) for item in proposal)


def test_a_morning_block_from_nine_to_twelve_offers_only_the_sample_hour() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "09:00", "12:00")]

    proposal = _service(store).propose_week(_admin(), medico.medico_id, MONDAY)

    assert [(item.inicio_utc.astimezone(UTC).hour, item.franja) for item in proposal] == [
        (14, "MANANA")
    ]


def test_a_slot_that_already_started_is_not_proposed() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "10:00")]
    # The clock says 08:20 branch time on Monday: the 08:00 slot has started.
    service = WorkHoursService(
        store=store, clock=lambda: datetime(2026, 11, 23, 13, 20, tzinfo=UTC)
    )

    proposal = service.propose_week(_admin(), medico.medico_id, MONDAY)

    assert [item.inicio_utc.astimezone(UTC).hour for item in proposal] == [14]


def test_a_doctor_without_declared_hours_cannot_be_scheduled() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)

    with pytest.raises(Conflict):
        _service(store).propose_week(_admin(), medico.medico_id, MONDAY)


def test_a_week_starts_on_monday_and_cannot_be_long_past_or_too_far_ahead() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]
    service = _service(store)

    with pytest.raises(ValidationFailed):
        service.propose_week(_admin(), medico.medico_id, date(2026, 11, 24))  # a Tuesday
    with pytest.raises(ValidationFailed):
        service.propose_week(_admin(), medico.medico_id, date(2026, 11, 16))  # already over
    with pytest.raises(ValidationFailed):
        service.propose_week(_admin(), medico.medico_id, MONDAY + timedelta(weeks=13))


def test_the_current_week_can_still_be_planned() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]

    proposal = _service(store).propose_week(_admin(), medico.medico_id, MONDAY)

    assert len(proposal) == 1


def test_an_unknown_or_inactive_doctor_is_not_found() -> None:
    store = FakeHoursStore()
    inactive = _doctor(store, activo=False)
    store.blocks[inactive.medico_id] = [_block(0, "08:00", "09:00")]

    with pytest.raises(NotFound):
        _service(store).propose_week(_admin(), str(uuid.uuid4()), MONDAY)
    with pytest.raises(NotFound):
        _service(store).propose_week(_admin(), inactive.medico_id, MONDAY)


def test_generation_creates_the_week_and_is_idempotent() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "10:00"), _block(2, "12:00", "13:00")]
    service = _service(store)

    first = service.generate_week(_admin(), medico.medico_id, CONSULTORIO, MONDAY)
    second = service.generate_week(_admin(), medico.medico_id, CONSULTORIO, MONDAY)

    assert first == (3, 0)
    assert second == (0, 3)


def test_generation_needs_an_active_consultorio() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]

    with pytest.raises(NotFound):
        _service(store).generate_week(_admin(), medico.medico_id, str(uuid.uuid4()), MONDAY)


def test_only_an_administrator_generates_slots() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "09:00")]
    doctor = _doctor_user(store, medico)

    with pytest.raises(Forbidden):
        _service(store).generate_week(doctor, medico.medico_id, CONSULTORIO, MONDAY)


def test_the_administrator_reads_the_hours_a_doctor_declared() -> None:
    store = FakeHoursStore()
    medico = _doctor(store)
    store.blocks[medico.medico_id] = [_block(0, "08:00", "10:00")]

    assert _service(store).doctor_work_hours(_admin(), medico.medico_id) == [
        _block(0, "08:00", "10:00")
    ]
