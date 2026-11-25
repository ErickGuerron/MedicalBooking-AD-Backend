# ruff: noqa: F811  (pytest injects the fixtures imported below by parameter name)
"""Catalogs, date-range reads, the event log and the patient's own history (no database)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from medicalbooking.modules.history.domain.policies import (
    MAX_RANGO_DIAS,
    DateRange,
    validate_pagina,
    validate_rango,
)
from medicalbooking.shared.domain.errors import Forbidden, ValidationFailed
from tests.unit.test_history_service import (  # noqa: F401  (pytest fixtures)
    ADMIN,
    ALLERGY,
    DOCTOR,
    LAB,
    NOW,
    OTHER_DOCTOR,
    OTHER_PAT_ID,
    OTHER_PATIENT,
    PAT_ID,
    PATIENT,
    FakeStore,
    service,
    store,
)

# ---------- catalog validation ----------


def test_an_allergy_must_name_an_active_catalog_code(service):
    entry = service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    assert entry.data["sustancia"] == "Penicilina"
    assert entry.data["categoria"] == "MEDICAMENTO"
    assert entry.data["sustancia_codigo"] == "PENICILINA"


def test_an_allergy_with_an_unknown_code_is_refused(service, store):
    with pytest.raises(ValidationFailed):
        service.create_entry(
            DOCTOR, "alergia", PAT_ID, {**ALLERGY, "sustancia_codigo": "INVENTADA"}
        )
    assert store.entries == {}


def test_a_free_text_allergen_without_a_code_is_refused(service):
    body = {"sustancia": "Maní", "categoria": "ALIMENTO", "severidad": "LEVE"}
    with pytest.raises(ValidationFailed):
        service.create_entry(DOCTOR, "alergia", PAT_ID, body)


def test_a_vaccine_must_name_an_active_catalog_code(service):
    entry = service.create_entry(
        DOCTOR,
        "vacuna",
        PAT_ID,
        {"vacuna_codigo": "INFLUENZA", "dosis_numero": 1, "fecha": date(2026, 6, 1)},
    )
    assert entry.data["vacuna"] == "Influenza estacional"
    with pytest.raises(ValidationFailed):
        service.create_entry(
            DOCTOR,
            "vacuna",
            PAT_ID,
            {"vacuna_codigo": "RETIRED", "dosis_numero": 1, "fecha": date(2026, 6, 1)},
        )


def test_a_retired_cie11_code_cannot_be_used_on_an_antecedent(service, store):
    with pytest.raises(ValidationFailed):
        service.create_entry(
            DOCTOR, "antecedente", PAT_ID, {"tipo": "PERSONAL", "descripcion": "x", "cie11": "OLD1"}
        )
    assert store.entries == {}


def test_a_cie11_code_outside_the_catalog_is_refused_on_a_problem(service):
    with pytest.raises(ValidationFailed):
        service.create_entry(DOCTOR, "problema", PAT_ID, {"descripcion": "x", "cie11": "ZZ99"})


def test_an_active_cie11_code_is_accepted_on_a_problem(service):
    entry = service.create_entry(
        DOCTOR, "problema", PAT_ID, {"descripcion": "Hipertension", "cie11": "ba00"}
    )
    assert entry.data["cie11"] == "BA00"


def test_a_missing_catalog_code_is_a_validation_error_not_a_crash(service):
    with pytest.raises(ValidationFailed):
        service.create_entry(
            DOCTOR, "vacuna", PAT_ID, {"dosis_numero": 1, "fecha": date(2026, 6, 1)}
        )


# ---------- catalog search ----------


def test_catalog_search_returns_only_active_items_and_filters_by_text(service):
    items = service.search_catalog(DOCTOR, "sustancias_alergia", "peni", None)
    assert [item.codigo for item in items] == ["PENICILINA"]
    assert service.search_catalog(DOCTOR, "vacunas", None, None)[0].codigo == "INFLUENZA"


def test_catalog_search_refuses_an_unknown_catalog_a_long_query_and_a_bad_limit(service):
    with pytest.raises(ValidationFailed):
        service.search_catalog(DOCTOR, "tabla_secreta", None, None)
    with pytest.raises(ValidationFailed):
        service.search_catalog(DOCTOR, "vacunas", "x" * 101, None)
    with pytest.raises(ValidationFailed):
        service.search_catalog(DOCTOR, "vacunas", None, 0)


def test_any_signed_in_person_may_read_a_catalog(service):
    assert service.search_catalog(PATIENT, "cie11", None, None)
    assert service.search_catalog(LAB, "cie11", None, None)


# ---------- date ranges ----------


def test_a_range_must_not_end_before_it_starts_and_must_span_at_most_a_year():
    with pytest.raises(ValidationFailed):
        validate_rango(date(2026, 2, 1), date(2026, 1, 1))
    with pytest.raises(ValidationFailed):
        validate_rango(date(2025, 1, 1), date(2025, 1, 1) + timedelta(days=MAX_RANGO_DIAS + 1))
    assert validate_rango(date(2025, 1, 1), date(2025, 1, 1) + timedelta(days=MAX_RANGO_DIAS))


def test_an_open_range_is_accepted_on_either_side():
    assert validate_rango(date(2026, 1, 1), None).hasta is None
    assert validate_rango(None, date(2026, 1, 1)).desde is None


def test_the_history_read_keeps_only_entries_inside_the_range(service):
    service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)  # registered at NOW (2026-11-25 UTC)
    inside = service.read_history(DOCTOR, PAT_ID, DateRange(date(2026, 11, 25), date(2026, 11, 25)))
    later = service.read_history(DOCTOR, PAT_ID, DateRange(date(2026, 11, 26), None))
    assert len(inside.entries["alergia"]) == 1
    assert later.entries["alergia"] == []


def test_the_range_bounds_are_inclusive_days(service):
    service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    earlier = service.read_history(DOCTOR, PAT_ID, DateRange(None, date(2026, 11, 24)))
    assert earlier.entries["alergia"] == []


# ---------- the event log ----------


def test_the_doctor_sees_who_made_each_change_and_the_patient_does_not(service):
    entry = service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    service.change_estado(
        DOCTOR,
        "alergia",
        entry.id,
        paciente_id=PAT_ID,
        expected="ACTIVA",
        target="RESUELTA",
        motivo="Desensibilizada",
    )
    doctor_view = service.list_events(DOCTOR, PAT_ID)
    patient_view = service.list_events(PATIENT, PAT_ID)
    assert doctor_view.incluye_actor is True
    assert patient_view.incluye_actor is False
    assert all(row.actor_usuario_id == "user-doctor" for row in doctor_view.items)
    assert all(row.actor_usuario_id is None for row in patient_view.items)
    assert [row.accion for row in patient_view.items] == ["ESTADO", "CREAR"]


def test_events_are_newest_first_and_paged_with_a_total(service):
    entry = service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    service.change_estado(
        DOCTOR,
        "alergia",
        entry.id,
        paciente_id=PAT_ID,
        expected="ACTIVA",
        target="RESUELTA",
        motivo="a",
    )
    service.change_estado(
        DOCTOR,
        "alergia",
        entry.id,
        paciente_id=PAT_ID,
        expected="RESUELTA",
        target="ACTIVA",
        motivo="b",
    )
    first = service.list_events(DOCTOR, PAT_ID, limit=2, offset=0)
    second = service.list_events(DOCTOR, PAT_ID, limit=2, offset=2)
    assert first.total == 3 and len(first.items) == 2
    assert [row.motivo for row in first.items] == ["b", "a"]
    assert [row.accion for row in second.items] == ["CREAR"]


def test_events_can_be_filtered_by_entity_and_action(service):
    service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    service.create_entry(DOCTOR, "problema", PAT_ID, {"descripcion": "Asma", "cie11": "BA00"})
    only_problems = service.list_events(DOCTOR, PAT_ID, entidad="problema")
    assert only_problems.total == 1
    assert only_problems.items[0].entidad == "problema"
    only_creations = service.list_events(DOCTOR, PAT_ID, accion="CREAR")
    assert only_creations.total == 2


def test_an_unknown_entity_or_action_filter_is_refused(service):
    with pytest.raises(ValidationFailed):
        service.list_events(DOCTOR, PAT_ID, entidad="tabla_secreta")
    with pytest.raises(ValidationFailed):
        service.list_events(DOCTOR, PAT_ID, accion="BORRAR")


def test_another_patient_cannot_read_an_event_log_and_the_attempt_is_audited(service, store):
    with pytest.raises(Forbidden):
        service.list_events(OTHER_PATIENT, PAT_ID)
    with pytest.raises(Forbidden):
        service.list_events(OTHER_DOCTOR, PAT_ID)
    assert store.denied_audits()[-1].accion == "consultar_eventos"


def test_the_laboratory_cannot_read_the_event_log(service):
    with pytest.raises(Forbidden):
        service.list_events(LAB, PAT_ID)


def test_a_page_outside_the_limits_is_refused():
    with pytest.raises(ValidationFailed):
        validate_pagina(0, None)
    with pytest.raises(ValidationFailed):
        validate_pagina(201, None)
    with pytest.raises(ValidationFailed):
        validate_pagina(None, -1)
    assert validate_pagina(None, None) == (50, 0)


# ---------- the patient's own history ----------


def test_a_patient_resolves_their_own_history_and_a_doctor_does_not(service):
    assert service.own_patient_id(PATIENT) == PAT_ID
    with pytest.raises(Forbidden):
        service.own_patient_id(DOCTOR)
    with pytest.raises(Forbidden):
        service.own_patient_id(LAB)


def test_the_patient_reads_their_own_history_with_a_range(service):
    service.create_entry(DOCTOR, "alergia", PAT_ID, ALLERGY)
    view = service.read_history(PATIENT, PAT_ID, DateRange(date(2026, 11, 1), date(2026, 11, 30)))
    assert len(view.entries["alergia"]) == 1
