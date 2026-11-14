"""Treatment use cases (S4-S6, S10) against in-memory fakes.

SQL joins, locking and the append-only trigger are pinned by the integration suite. Here the
rules are: who may act on which resource, state transitions, validation before storage, and
audit records that never contain clinical text.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.treatments.application.ports import (
    CitaContext,
    FollowUp,
    NewFollowUp,
    NewTreatment,
    Treatment,
    TreatmentUpdate,
)
from medicalbooking.modules.treatments.application.use_cases import TreatmentService
from medicalbooking.shared.domain.errors import Conflict, Forbidden, ValidationFailed

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
INICIO = date(2026, 11, 2)
DESCRIPCION_SECRETA = "Reposo absoluto y dieta sin sal"
NOTA_SECRETA = "Presion arterial controlada tras la primera semana"

PACIENTE = frozenset({"resultados.leer_propios", "citas.reservar"})
MEDICO = frozenset({"resultados.leer_con_acceso", "revisiones.registrar", "ordenes.crear"})
ENFERMERO = frozenset({"resultados.registrar"})


def _id() -> str:
    return str(uuid.uuid4())


def _actor(permissions: frozenset[str], user_id: str | None = None) -> AccessProfile:
    return AccessProfile(
        user_id=user_id or _id(),
        status="ACTIVO",
        roles=frozenset(),
        permissions=permissions,
    )


def _owner(by_user: dict[str, str], entity_id: str) -> str:
    return next(user for user, value in by_user.items() if value == entity_id)


@dataclass
class FakeStore:
    """Honours the store contract without SQL. The adapter joins the owning user ids."""

    patient_by_user: dict[str, str] = field(default_factory=dict)
    medico_by_user: dict[str, str] = field(default_factory=dict)
    citas: dict[str, CitaContext] = field(default_factory=dict)
    treatments: dict[str, Treatment] = field(default_factory=dict)
    followups: list[FollowUp] = field(default_factory=list)
    followup_users: dict[str, str] = field(default_factory=dict)
    audit: list = field(default_factory=list)
    lose_update_race: bool = False

    def resolve_patient_id(self, user_id):
        return self.patient_by_user.get(user_id)

    def resolve_medico_id(self, user_id):
        return self.medico_by_user.get(user_id)

    def get_cita(self, cita_id):
        return self.citas.get(cita_id)

    def create_treatment(self, command: NewTreatment) -> Treatment:
        cita = self.citas[command.cita_id]
        treatment = Treatment(
            id=command.treatment_id,
            cita_id=command.cita_id,
            paciente_id=cita.paciente_id,
            paciente_user_id=_owner(self.patient_by_user, cita.paciente_id),
            medico_id=cita.medico_id,
            medico_user_id=_owner(self.medico_by_user, cita.medico_id),
            descripcion=command.descripcion,
            estado=command.estado,
            fecha_inicio=command.fecha_inicio,
            fecha_fin=command.fecha_fin,
        )
        self.treatments[treatment.id] = treatment
        return treatment

    def get_treatment(self, treatment_id):
        return self.treatments.get(treatment_id)

    def list_treatments(self, cita_id):
        return [t for t in self.treatments.values() if t.cita_id == cita_id]

    def update_treatment(self, command: TreatmentUpdate):
        current = self.treatments[command.treatment_id]
        if self.lose_update_race or current.estado != command.expected_estado:
            return None
        updated = replace(
            current,
            descripcion=command.descripcion,
            estado=command.estado,
            fecha_fin=command.fecha_fin,
        )
        self.treatments[updated.id] = updated
        return updated

    def append_followup(self, command: NewFollowUp) -> FollowUp:
        followup = FollowUp(
            id=command.followup_id,
            tratamiento_id=command.tratamiento_id,
            nota=command.nota,
            registrado_en=command.registrado_en,
        )
        self.followups.append(followup)
        self.followup_users[followup.id] = command.registrado_por_user_id
        return followup

    def list_followups(self, tratamiento_id):
        # Insertion order: the adapter orders by its identity sequence, not by id or time.
        return [f for f in self.followups if f.tratamiento_id == tratamiento_id]

    def record_audit(self, event):
        self.audit.append(event)


@dataclass
class World:
    store: FakeStore
    service: TreatmentService
    doctor: AccessProfile
    other_doctor: AccessProfile
    patient: AccessProfile
    other_patient: AccessProfile
    nurse: AccessProfile
    cita_id: str
    patient_id: str
    doctor_id: str

    def create(self, **overrides) -> Treatment:
        values = {
            "descripcion": DESCRIPCION_SECRETA,
            "fecha_inicio": INICIO,
            "estado": "ACTIVO",
            "fecha_fin": None,
        }
        values.update(overrides)
        return self.service.create_treatment(self.doctor, self.cita_id, **values)


@pytest.fixture
def world() -> World:
    store = FakeStore()
    doctor = _actor(MEDICO)
    other_doctor = _actor(MEDICO)
    patient = _actor(PACIENTE)
    other_patient = _actor(PACIENTE)
    store.medico_by_user[doctor.user_id] = _id()
    store.medico_by_user[other_doctor.user_id] = _id()
    store.patient_by_user[patient.user_id] = _id()
    store.patient_by_user[other_patient.user_id] = _id()
    cita_id = _id()
    patient_id = store.patient_by_user[patient.user_id]
    doctor_id = store.medico_by_user[doctor.user_id]
    store.citas[cita_id] = CitaContext(cita_id=cita_id, paciente_id=patient_id, medico_id=doctor_id)
    return World(
        store=store,
        service=TreatmentService(store=store, clock=lambda: NOW),
        doctor=doctor,
        other_doctor=other_doctor,
        patient=patient,
        other_patient=other_patient,
        nurse=_actor(ENFERMERO),
        cita_id=cita_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
    )


# ---------- creation (S4) ----------


def test_treating_doctor_creates_an_active_treatment_by_default(world) -> None:
    treatment = world.create()

    assert treatment.cita_id == world.cita_id
    assert treatment.estado == "ACTIVO"
    assert treatment.fecha_inicio == INICIO
    assert treatment.fecha_fin is None


def test_doctor_of_another_cita_cannot_create_and_nothing_is_stored(world) -> None:
    with pytest.raises(Forbidden):
        world.service.create_treatment(
            world.other_doctor,
            world.cita_id,
            descripcion=DESCRIPCION_SECRETA,
            fecha_inicio=INICIO,
        )

    assert world.store.treatments == {}


def test_missing_cita_answers_forbidden_like_a_foreign_one(world) -> None:
    with pytest.raises(Forbidden):
        world.service.create_treatment(
            world.doctor, _id(), descripcion=DESCRIPCION_SECRETA, fecha_inicio=INICIO
        )


def test_patient_cannot_create_a_treatment(world) -> None:
    with pytest.raises(Forbidden):
        world.service.create_treatment(
            world.patient, world.cita_id, descripcion=DESCRIPCION_SECRETA, fecha_inicio=INICIO
        )

    assert world.store.treatments == {}


def test_finalized_creation_without_end_date_is_rejected_and_nothing_stored(world) -> None:
    with pytest.raises(ValidationFailed):
        world.create(estado="FINALIZADO", fecha_fin=None)

    assert world.store.treatments == {}


def test_creation_is_audited_without_the_description(world) -> None:
    treatment = world.create()

    event = world.store.audit[-1]
    assert (event.entidad, event.entidad_id, event.accion) == ("tratamiento", treatment.id, "crear")
    assert event.paciente_id == world.patient_id
    assert event.resultado == "OK"
    assert DESCRIPCION_SECRETA not in repr(event)


# ---------- reads (S6) ----------


def test_owning_patient_lists_the_treatments_of_their_cita(world) -> None:
    treatment = world.create()

    items = world.service.list_treatments(world.patient, world.cita_id)

    assert [item.id for item in items] == [treatment.id]


def test_other_patient_cannot_list_the_treatments_of_a_cita(world) -> None:
    world.create()

    with pytest.raises(Forbidden):
        world.service.list_treatments(world.other_patient, world.cita_id)


def test_other_doctor_cannot_list_the_treatments_of_a_cita(world) -> None:
    world.create()

    with pytest.raises(Forbidden):
        world.service.list_treatments(world.other_doctor, world.cita_id)


def test_denied_read_is_audited_as_denegado(world) -> None:
    world.create()

    with pytest.raises(Forbidden):
        world.service.list_treatments(world.other_patient, world.cita_id)

    assert world.store.audit[-1].resultado == "DENEGADO"


# ---------- state changes (S4) ----------


def test_treating_doctor_finalizes_with_an_end_date(world) -> None:
    treatment = world.create()

    updated = world.service.update_treatment(
        world.doctor, treatment.id, estado="FINALIZADO", fecha_fin=date(2026, 11, 9)
    )

    assert updated.estado == "FINALIZADO"
    assert updated.fecha_fin == date(2026, 11, 9)


def test_finalizing_without_an_end_date_is_rejected_and_row_is_unchanged(world) -> None:
    treatment = world.create()

    with pytest.raises(ValidationFailed):
        world.service.update_treatment(world.doctor, treatment.id, estado="FINALIZADO")

    assert world.store.treatments[treatment.id].estado == "ACTIVO"


def test_finalized_treatment_cannot_be_reopened(world) -> None:
    treatment = world.create(estado="FINALIZADO", fecha_fin=date(2026, 11, 9))

    with pytest.raises(ValidationFailed):
        world.service.update_treatment(world.doctor, treatment.id, estado="ACTIVO")

    assert world.store.treatments[treatment.id].estado == "FINALIZADO"


def test_foreign_doctor_cannot_update_and_row_is_unchanged(world) -> None:
    treatment = world.create()

    with pytest.raises(Forbidden):
        world.service.update_treatment(world.other_doctor, treatment.id, descripcion="otra")

    assert world.store.treatments[treatment.id].descripcion == DESCRIPCION_SECRETA


def test_patient_cannot_update_their_treatment(world) -> None:
    treatment = world.create()

    with pytest.raises(Forbidden):
        world.service.update_treatment(world.patient, treatment.id, estado="SUSPENDIDO")


def test_missing_treatment_answers_forbidden(world) -> None:
    with pytest.raises(Forbidden):
        world.service.update_treatment(world.doctor, _id(), estado="SUSPENDIDO")


def test_empty_update_is_rejected(world) -> None:
    treatment = world.create()

    with pytest.raises(ValidationFailed):
        world.service.update_treatment(world.doctor, treatment.id)


def test_concurrent_state_change_answers_conflict(world) -> None:
    treatment = world.create()
    world.store.lose_update_race = True

    with pytest.raises(Conflict):
        world.service.update_treatment(world.doctor, treatment.id, estado="SUSPENDIDO")


def test_update_is_audited_without_the_description(world) -> None:
    treatment = world.create()

    world.service.update_treatment(world.doctor, treatment.id, estado="SUSPENDIDO")

    event = world.store.audit[-1]
    assert (event.entidad, event.accion) == ("tratamiento", "actualizar")
    assert "SUSPENDIDO" not in repr(event)
    assert DESCRIPCION_SECRETA not in repr(event)


# ---------- follow-ups (S5, append-only) ----------


def test_treating_doctor_appends_a_follow_up(world) -> None:
    treatment = world.create()

    followup = world.service.add_followup(world.doctor, treatment.id, NOTA_SECRETA)

    assert followup.tratamiento_id == treatment.id
    assert followup.nota == NOTA_SECRETA
    assert followup.registrado_en == NOW


@pytest.mark.parametrize("nota", ["", "   "])
def test_blank_follow_up_is_rejected_and_nothing_is_stored(world, nota) -> None:
    treatment = world.create()

    with pytest.raises(ValidationFailed):
        world.service.add_followup(world.doctor, treatment.id, nota)

    assert world.store.followups == []


def test_foreign_doctor_cannot_append_a_follow_up(world) -> None:
    treatment = world.create()

    with pytest.raises(Forbidden):
        world.service.add_followup(world.other_doctor, treatment.id, NOTA_SECRETA)

    assert world.store.followups == []


def test_owning_patient_reads_follow_ups_in_registration_order(world) -> None:
    treatment = world.create()
    first = world.service.add_followup(world.doctor, treatment.id, "uno")
    second = world.service.add_followup(world.doctor, treatment.id, "dos")

    items = world.service.list_followups(world.patient, treatment.id)

    assert [item.id for item in items] == [first.id, second.id]


def test_other_patient_cannot_read_follow_ups(world) -> None:
    treatment = world.create()
    world.service.add_followup(world.doctor, treatment.id, NOTA_SECRETA)

    with pytest.raises(Forbidden):
        world.service.list_followups(world.other_patient, treatment.id)


def test_follow_up_audit_never_contains_the_note(world) -> None:
    treatment = world.create()

    world.service.add_followup(world.doctor, treatment.id, NOTA_SECRETA)

    event = world.store.audit[-1]
    assert (event.entidad, event.accion) == ("seguimiento_tratamiento", "registrar")
    assert NOTA_SECRETA not in repr(event)
