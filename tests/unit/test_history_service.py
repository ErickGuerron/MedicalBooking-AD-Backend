"""Clinical history use cases: access matrix, verification, guarded changes, vitals and audit."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

import pytest

from medicalbooking.modules.history.application.history_service import HistoryService
from medicalbooking.modules.history.application.ports import (
    CatalogItem,
    CitaOwnership,
    Entry,
    EventRow,
    VitalSigns,
)
from medicalbooking.modules.history.domain.policies import (
    ALERGIA,
    ANTECEDENTE,
    MEDICACION,
    PERM_DECLARAR,
    PERM_LEER,
    PERM_LEER_PROPIA,
    PERM_REGISTRAR,
    PROBLEMA,
    VACUNA,
)
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.shared.domain.errors import Conflict, Forbidden, ValidationFailed

NOW = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)
TODAY = date(2026, 11, 25)
FORBIDDEN_MESSAGE = "Access to this resource is not allowed"
CATALOGS = {
    "cie11": {
        "BA00": CatalogItem("BA00", "Hipertension esencial", True),
        "OLD1": CatalogItem("OLD1", "Codigo retirado", False),
    },
    "vacunas": {
        "INFLUENZA": CatalogItem("INFLUENZA", "Influenza estacional", True),
        "RETIRED": CatalogItem("RETIRED", "Vacuna retirada", False),
    },
    "sustancias_alergia": {
        "PENICILINA": CatalogItem("PENICILINA", "Penicilina", True, "MEDICAMENTO"),
    },
}
INITIAL = {
    "alergia": ALERGIA.initial,
    "antecedente": ANTECEDENTE.initial,
    "medicacion": MEDICACION.initial,
    "problema": PROBLEMA.initial,
    "vacuna": VACUNA.initial,
}


def _inside(moment, start, end):
    if start is not None and moment < start:
        return False
    if end is not None and moment >= end:
        return False
    return True


@dataclass
class FakeStore:
    patients_by_user: dict[str, str] = field(default_factory=dict)
    medicos_by_user: dict[str, str] = field(default_factory=dict)
    treats: set[tuple[str, str]] = field(default_factory=set)
    citas: dict[str, CitaOwnership] = field(default_factory=dict)
    entries: dict[tuple[str, str], Entry] = field(default_factory=dict)
    vitals: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    audits: list[Any] = field(default_factory=list)
    _seq: int = 0

    def resolve_patient_id(self, user_id):
        return self.patients_by_user.get(user_id)

    def resolve_medico_id(self, user_id):
        return self.medicos_by_user.get(user_id)

    def doctor_treats_patient(self, medico_id, paciente_id):
        return (medico_id, paciente_id) in self.treats

    def get_cita(self, cita_id):
        return self.citas.get(cita_id)

    def insert_entry(
        self,
        kind,
        entry_id,
        paciente_id,
        values,
        *,
        origen,
        verificada,
        verificada_por,
        registrada_por,
        now,
    ):
        self.entries[(kind, entry_id)] = Entry(
            id=entry_id,
            kind=kind,
            paciente_id=paciente_id,
            estado=INITIAL[kind],
            origen=origen,
            verificada=verificada,
            verificada_por=verificada_por,
            verificada_en=now if verificada else None,
            registrada_por=registrada_por,
            registrada_en=now,
            data=dict(values),
        )

    def get_entry(self, kind, entry_id):
        return self.entries.get((kind, entry_id))

    def list_entries(self, kind, paciente_id, start=None, end=None):
        return [
            e
            for (k, _), e in self.entries.items()
            if k == kind and e.paciente_id == paciente_id and _inside(e.registrada_en, start, end)
        ]

    def verify_entry(self, kind, entry_id, medico_user_id, now):
        entry = self.entries[(kind, entry_id)]
        if entry.verificada:
            return False
        self.entries[(kind, entry_id)] = replace(
            entry, verificada=True, verificada_por=medico_user_id, verificada_en=now
        )
        return True

    def change_estado(self, kind, entry_id, expected, target, now):
        entry = self.entries[(kind, entry_id)]
        if entry.estado != expected:
            return False
        self.entries[(kind, entry_id)] = replace(entry, estado=target)
        return True

    def insert_vitals(
        self,
        vitals_id,
        paciente_id,
        cita_id,
        values,
        *,
        registrada_por,
        now,
        corrige_a=None,
        motivo_correccion=None,
    ):
        self.vitals[vitals_id] = VitalSigns(
            id=vitals_id,
            paciente_id=paciente_id,
            cita_id=cita_id,
            corrige_a=corrige_a,
            motivo_correccion=motivo_correccion,
            registrada_por=registrada_por,
            registrada_en=now,
            values=dict(values),
        )

    def catalog_item(self, catalog, codigo):
        return CATALOGS.get(catalog, {}).get(codigo)

    def search_catalog(self, catalog, query, limit):
        items = [item for item in CATALOGS.get(catalog, {}).values() if item.activo]
        if query:
            items = [item for item in items if query.lower() in item.nombre.lower()]
        return sorted(items, key=lambda item: item.nombre)[:limit]

    def list_events(self, paciente_id, *, entidad, accion, start, end, limit, offset):
        rows = []
        for index, event in enumerate(self.events, start=1):
            if event["paciente_id"] != paciente_id:
                continue
            if entidad and event["entidad"] != entidad:
                continue
            if accion and event["accion"] != accion:
                continue
            if start and event["now"] < start or end and event["now"] >= end:
                continue
            rows.append(
                EventRow(
                    id=index,
                    entidad=event["entidad"],
                    entidad_id=event["entidad_id"],
                    accion=event["accion"],
                    actor_usuario_id=event["actor_user_id"],
                    motivo=event["motivo"],
                    antes=event["antes"],
                    despues=event["despues"],
                    ocurrido_en=event["now"],
                )
            )
        rows.sort(key=lambda row: row.id, reverse=True)
        return rows[offset : offset + limit], len(rows)

    def get_vitals(self, vitals_id):
        return self.vitals.get(vitals_id)

    def list_vitals(self, paciente_id, start=None, end=None):
        return [
            v
            for v in self.vitals.values()
            if v.paciente_id == paciente_id and _inside(v.registrada_en, start, end)
        ]

    def append_event(self, **event):
        self.events.append(event)

    def record_audit(self, event):
        self.audits.append(event)

    def denied_audits(self):
        return [a for a in self.audits if a.resultado == "DENEGADO"]


def _profile(user_id: str, *permissions: str) -> AccessProfile:
    return AccessProfile(
        status="ACTIVO", user_id=user_id, roles=frozenset(), permissions=frozenset(permissions)
    )


DOCTOR = _profile("user-doctor", PERM_LEER, PERM_REGISTRAR)
OTHER_DOCTOR = _profile("user-other", PERM_LEER, PERM_REGISTRAR)
PATIENT = _profile("user-pat", PERM_DECLARAR, PERM_LEER_PROPIA)
OTHER_PATIENT = _profile("user-pat-2", PERM_DECLARAR, PERM_LEER_PROPIA)
LAB = _profile("user-lab")
ADMIN = _profile("user-admin")
PAT_ID = "pat-1"
OTHER_PAT_ID = "pat-2"


@pytest.fixture
def store() -> FakeStore:
    s = FakeStore(
        patients_by_user={"user-pat": PAT_ID, "user-pat-2": OTHER_PAT_ID},
        medicos_by_user={"user-doctor": "med-1", "user-other": "med-2"},
        treats={("med-1", PAT_ID)},
    )
    s.citas["cita-1"] = CitaOwnership(cita_id="cita-1", paciente_id=PAT_ID, medico_id="med-1")
    return s


@pytest.fixture
def service(store: FakeStore) -> HistoryService:
    return HistoryService(store=store, clock=lambda: NOW)


ALLERGY = {
    "sustancia_codigo": "PENICILINA",
    "sustancia": "Penicilina",
    "categoria": "MEDICAMENTO",
    "severidad": "GRAVE",
    "reaccion": "Urticaria",
}


def _only_entry(store: FakeStore, kind: str) -> Entry:
    (entry,) = [e for (k, _), e in store.entries.items() if k == kind]
    return entry


# ---------- registering ----------


def test_a_treating_doctor_registers_a_verified_allergy(service, store):
    entry = service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    assert entry.origen == "REGISTRADA_MEDICO"
    assert entry.verificada is True
    assert entry.verificada_por == "user-doctor"
    assert entry.estado == "ACTIVA"
    assert [e["accion"] for e in store.events] == ["CREAR"]


def test_a_patient_declares_their_own_allergy_unverified(service):
    entry = service.create_entry(PATIENT, "alergia", PAT_ID, ALLERGY)
    assert entry.origen == "DECLARADA_PACIENTE"
    assert entry.verificada is False
    assert entry.verificada_por is None


def test_a_patient_cannot_declare_a_problem_because_that_is_a_doctors_judgement(service):
    with pytest.raises(Forbidden):
        service.create_entry(PATIENT, "problema", PAT_ID, {"descripcion": "Hipertension"})


def test_a_patient_cannot_write_into_another_patients_history(service, store):
    with pytest.raises(Forbidden):
        service.create_entry(OTHER_PATIENT, "alergia", PAT_ID, ALLERGY)
    assert store.entries == {}
    assert store.denied_audits()[0].paciente_id == PAT_ID


def test_a_doctor_without_a_relation_cannot_register(service, store):
    with pytest.raises(Forbidden) as refused:
        service.create_entry(OTHER_DOCTOR, "alergia", PAT_ID, ALLERGY)
    assert str(refused.value) == FORBIDDEN_MESSAGE
    assert store.entries == {}


def test_a_laboratorist_and_an_administrator_cannot_write(service):
    for actor in (LAB, ADMIN):
        with pytest.raises(Forbidden):
            service.create_entry(actor, "alergia", PAT_ID, ALLERGY)


def test_an_invalid_allergy_is_refused_before_anything_is_stored(service, store):
    with pytest.raises(ValidationFailed):
        service.create_entry(DOCTOR, "alergia", PAT_ID, {**ALLERGY, "severidad": "MUY_GRAVE"})
    assert store.entries == {}


def test_an_unknown_kind_is_a_validation_error(service):
    with pytest.raises(ValidationFailed):
        service.create_entry(DOCTOR, "tatuaje", PAT_ID, {})


# ---------- verification ----------


def test_a_treating_doctor_verifies_a_declared_allergy_once(service, store):
    declared = service.create_entry(PATIENT, "alergia", PAT_ID, ALLERGY)
    verified = service.verify_entry(DOCTOR, "alergia", declared.id, paciente_id=PAT_ID)
    assert verified.verificada is True
    assert verified.verificada_por == "user-doctor"
    with pytest.raises(Conflict):
        service.verify_entry(DOCTOR, "alergia", declared.id, paciente_id=PAT_ID)
    assert [e["accion"] for e in store.events] == ["CREAR", "VERIFICAR"]


def test_the_patient_cannot_verify_their_own_declaration(service):
    declared = service.create_entry(PATIENT, "alergia", PAT_ID, ALLERGY)
    with pytest.raises(Forbidden):
        service.verify_entry(PATIENT, "alergia", declared.id, paciente_id=PAT_ID)
    assert _only_entry(service._store, "alergia").verificada is False


def test_a_doctor_without_a_relation_cannot_verify(service):
    declared = service.create_entry(PATIENT, "alergia", PAT_ID, ALLERGY)
    with pytest.raises(Forbidden):
        service.verify_entry(OTHER_DOCTOR, "alergia", declared.id, paciente_id=PAT_ID)


def test_verifying_a_doctor_entry_is_a_conflict(service):
    entry = service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    with pytest.raises(Conflict):
        service.verify_entry(DOCTOR, "alergia", entry.id, paciente_id=PAT_ID)


def test_a_missing_entry_and_a_foreign_entry_give_the_same_403(service, store):
    service.create_entry(PATIENT, "alergia", PAT_ID, ALLERGY)
    foreign = service.create_entry(OTHER_PATIENT, "alergia", OTHER_PAT_ID, ALLERGY)
    with pytest.raises(Forbidden) as missing:
        service.verify_entry(DOCTOR, "alergia", str(uuid.uuid4()), paciente_id=PAT_ID)
    with pytest.raises(Forbidden) as other:
        service.verify_entry(DOCTOR, "alergia", foreign.id, paciente_id=PAT_ID)
    assert str(missing.value) == str(other.value) == FORBIDDEN_MESSAGE


# ---------- guarded state changes ----------


def test_a_state_change_needs_a_motivo_and_is_logged_with_before_and_after(service, store):
    entry = service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    with pytest.raises(ValidationFailed):
        service.change_estado(
            DOCTOR,
            "alergia",
            entry.id,
            expected="ACTIVA",
            target="RESUELTA",
            motivo=" ",
            paciente_id=PAT_ID,
        )
    changed = service.change_estado(
        DOCTOR,
        "alergia",
        entry.id,
        expected="ACTIVA",
        target="RESUELTA",
        motivo="Desensibilizada",
        paciente_id=PAT_ID,
    )
    assert changed.estado == "RESUELTA"
    event = store.events[-1]
    assert event["accion"] == "ESTADO"
    assert event["motivo"] == "Desensibilizada"
    assert '"estado": "ACTIVA"' in event["antes"]
    assert '"estado": "RESUELTA"' in event["despues"]


def test_a_stale_read_cannot_overwrite_a_newer_state(service):
    entry = service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    service.change_estado(
        DOCTOR,
        "alergia",
        entry.id,
        expected="ACTIVA",
        target="RESUELTA",
        motivo="x",
        paciente_id=PAT_ID,
    )
    with pytest.raises(Conflict):
        service.change_estado(
            DOCTOR,
            "alergia",
            entry.id,
            expected="ACTIVA",
            target="ERRONEA",
            motivo="y",
            paciente_id=PAT_ID,
        )


def test_an_erroneous_entry_cannot_change_again(service):
    entry = service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    service.change_estado(
        DOCTOR,
        "alergia",
        entry.id,
        expected="ACTIVA",
        target="ERRONEA",
        motivo="Dato equivocado",
        paciente_id=PAT_ID,
    )
    with pytest.raises(Conflict):
        service.change_estado(
            DOCTOR,
            "alergia",
            entry.id,
            expected="ERRONEA",
            target="ACTIVA",
            motivo="x",
            paciente_id=PAT_ID,
        )


def test_the_patient_cannot_change_a_state(service):
    entry = service.create_entry(PATIENT, "alergia", PAT_ID, ALLERGY)
    with pytest.raises(Forbidden):
        service.change_estado(
            PATIENT,
            "alergia",
            entry.id,
            expected="ACTIVA",
            target="RESUELTA",
            motivo="x",
            paciente_id=PAT_ID,
        )


# ---------- reading ----------


def test_the_treating_doctor_reads_the_history_with_active_allergies_first(service, store):
    resolved = service.create_entry(
        DOCTOR, "alergia", PAT_ID, {**ALLERGY, "sustancia": "Latex", "categoria": "LATEX"}
    )
    service.change_estado(
        DOCTOR,
        "alergia",
        resolved.id,
        expected="ACTIVA",
        target="RESUELTA",
        motivo="ya no",
        paciente_id=PAT_ID,
    )
    service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    view = service.read_history(DOCTOR, PAT_ID)
    states = [e.estado for e in view.entries["alergia"]]
    assert states == ["ACTIVA", "RESUELTA"]


def test_the_patient_reads_their_own_history(service):
    service.create_entry(PATIENT, "alergia", PAT_ID, ALLERGY)
    view = service.read_history(PATIENT, PAT_ID)
    assert len(view.entries["alergia"]) == 1


def test_another_patient_cannot_read_a_history_and_the_attempt_is_audited(service, store):
    with pytest.raises(Forbidden):
        service.read_history(OTHER_PATIENT, PAT_ID)
    assert store.denied_audits()[-1].accion == "consultar"


def test_a_doctor_without_a_relation_cannot_read(service):
    with pytest.raises(Forbidden):
        service.read_history(OTHER_DOCTOR, PAT_ID)


def test_the_laboratory_and_administration_cannot_read_clinical_history(service):
    for actor in (LAB, ADMIN):
        with pytest.raises(Forbidden):
            service.read_history(actor, PAT_ID)


def test_a_successful_read_is_audited_without_values(service, store):
    service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    service.read_history(DOCTOR, PAT_ID)
    read = store.audits[-1]
    assert read.accion == "consultar" and read.resultado == "OK"
    assert "Penicilina" not in repr(read) and "GRAVE" not in repr(read)


# ---------- vital signs ----------


def test_the_treating_doctor_records_vital_signs_for_their_appointment(service, store):
    vitals = service.record_vitals(
        DOCTOR, "cita-1", {"peso_kg": 72.5, "pa_sistolica": 120, "pa_diastolica": 80}
    )
    assert vitals.values["peso_kg"] == 72.5
    assert vitals.values["talla_cm"] is None
    assert store.vitals[vitals.id].paciente_id == PAT_ID


def test_another_doctor_cannot_record_vitals_for_an_appointment_that_is_not_theirs(service):
    with pytest.raises(Forbidden):
        service.record_vitals(OTHER_DOCTOR, "cita-1", {"peso_kg": 70})


def test_a_patient_cannot_record_their_own_vitals(service):
    with pytest.raises(Forbidden):
        service.record_vitals(PATIENT, "cita-1", {"peso_kg": 70})


def test_a_missing_appointment_and_a_foreign_one_give_the_same_403(service):
    with pytest.raises(Forbidden) as missing:
        service.record_vitals(DOCTOR, str(uuid.uuid4()), {"peso_kg": 70})
    with pytest.raises(Forbidden) as foreign:
        service.record_vitals(OTHER_DOCTOR, "cita-1", {"peso_kg": 70})
    assert str(missing.value) == str(foreign.value) == FORBIDDEN_MESSAGE


def test_an_out_of_range_vital_is_refused(service, store):
    with pytest.raises(ValidationFailed):
        service.record_vitals(DOCTOR, "cita-1", {"saturacion": 101})
    assert store.vitals == {}


# ---------- vaccines and the rest of the kinds ----------


def test_a_vaccine_is_registered_by_the_doctor_and_its_future_date_is_refused(service):
    with pytest.raises(ValidationFailed):
        service.create_entry(
            DOCTOR,
            "vacuna",
            PAT_ID,
            {"vacuna_codigo": "INFLUENZA", "dosis_numero": 1, "fecha": date(2030, 1, 1)},
        )
    entry = service.create_entry(
        DOCTOR,
        "vacuna",
        PAT_ID,
        {"vacuna_codigo": "INFLUENZA", "dosis_numero": 1, "fecha": date(2026, 6, 1)},
    )
    assert entry.verificada is True


def test_a_family_antecedent_needs_the_relative(service):
    with pytest.raises(ValidationFailed):
        service.create_entry(
            DOCTOR, "antecedente", PAT_ID, {"tipo": "FAMILIAR", "descripcion": "Diabetes"}
        )


def test_medication_declared_by_the_patient_is_unverified_until_a_doctor_confirms(service):
    entry = service.create_entry(
        PATIENT,
        "medicacion",
        PAT_ID,
        {"medicamento": "Losartan", "dosis": "50 mg", "frecuencia": "diaria"},
    )
    assert entry.verificada is False
    confirmed = service.verify_entry(DOCTOR, "medicacion", entry.id, paciente_id=PAT_ID)
    assert confirmed.verificada is True


# ---------- paths and corrections ----------


def test_an_entry_under_another_patients_path_is_a_403(service):
    entry = service.create_entry(PATIENT, "alergia", PAT_ID, ALLERGY)
    with pytest.raises(Forbidden):
        service.verify_entry(DOCTOR, "alergia", entry.id, paciente_id=OTHER_PAT_ID)


def test_a_vital_correction_is_a_new_entry_and_the_original_stays(service, store):
    original = service.record_vitals(DOCTOR, "cita-1", {"peso_kg": 72.5})
    corrected = service.correct_vitals(
        DOCTOR, original.id, {"peso_kg": 70.0}, "Bascula mal calibrada"
    )
    assert corrected.corrige_a == original.id
    assert corrected.motivo_correccion == "Bascula mal calibrada"
    assert store.vitals[original.id].values["peso_kg"] == 72.5
    assert store.vitals[corrected.id].values["peso_kg"] == 70.0


def test_a_vital_correction_needs_a_motivo(service):
    original = service.record_vitals(DOCTOR, "cita-1", {"peso_kg": 72.5})
    with pytest.raises(ValidationFailed):
        service.correct_vitals(DOCTOR, original.id, {"peso_kg": 70.0}, "  ")


def test_another_doctor_cannot_correct_vitals_of_an_appointment_that_is_not_theirs(service):
    original = service.record_vitals(DOCTOR, "cita-1", {"peso_kg": 72.5})
    with pytest.raises(Forbidden):
        service.correct_vitals(OTHER_DOCTOR, original.id, {"peso_kg": 70.0}, "motivo")
