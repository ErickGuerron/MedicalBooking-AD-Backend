"""Patient directory and lab test catalog (lab order form) against real SQL Server.

Each test creates its own users and test types, so counts do not depend on other tests.
Patients are listed only when they have a relation with the calling doctor (an appointment or a
lab order), and only when their account is ACTIVO. Patients share the same synthetic names, so
directory checks page through the whole list.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import _doctor, _patient_id, _relate, _test_type
from tests.integration.test_scheduling_api import _count, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PATIENTS_URL = "/api/v1/patients"
CATALOG_URL = "/api/v1/lab/test-types"
ORDERS_URL = "/api/v1/lab/orders"
PAGE = 100
UNIFORM_FORBIDDEN = "Access to this resource is not allowed"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Iterator[Flask]:
    documents = tmp_path_factory.mktemp("lab-catalog-documents")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _all_patients(app: Flask, headers: dict) -> list[dict]:
    """Page through the whole directory with the maximum page size."""
    items: list[dict] = []
    offset = 0
    while True:
        response = app.test_client().get(
            f"{PATIENTS_URL}?limit={PAGE}&offset={offset}", headers=headers
        )
        assert response.status_code == 200, response.get_json()
        page = response.get_json()["items"]
        items.extend(page)
        if len(page) < PAGE:
            return items
        offset += PAGE


def _listed_ids(app: Flask, doctor: str) -> set[str]:
    return {item["id"] for item in _all_patients(app, _token(app, doctor))}


def _doctor_profile(engine: Engine, user_id: str) -> str:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT id FROM medicos WHERE usuario_id = :u"), {"u": user_id}
        ).scalar_one()
    return str(uuid.UUID(str(value)))


def _insert_history_order(engine: Engine, patient_profile: str, doctor_user: str) -> None:
    """A lab order placed by the doctor with no appointment behind it (a relation by history)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio (id, paciente_id, medico_id, fecha_orden, "
                "prioridad, estado) VALUES (NEWID(), :p, :m, SYSUTCDATETIME(), 'NORMAL', "
                "'COMPLETADA')"
            ),
            {"p": patient_profile, "m": _doctor_profile(engine, doctor_user)},
        )


def _cancel_appointments(engine: Engine, patient_profile: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE citas SET estado = 'CANCELADA', cancelada_en = SYSUTCDATETIME() "
                "WHERE paciente_id = :p"
            ),
            {"p": patient_profile},
        )


def _set_account_status(engine: Engine, user_id: str, estado: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE usuarios SET estado = :e WHERE id = :u"), {"e": estado, "u": user_id}
        )


def _post_order(app: Flask, headers: dict, patient_profile: str, test_type: str):
    return app.test_client().post(
        ORDERS_URL,
        json={"paciente_id": patient_profile, "tipo_prueba_ids": [test_type]},
        headers=headers,
    )


def _orders_of(engine: Engine, patient_profile: str) -> int:
    return _count(
        engine,
        "SELECT COUNT(*) FROM ordenes_laboratorio WHERE paciente_id = :p",
        p=patient_profile,
    )


def _inactive_test_type(engine: Engine) -> str:
    test_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tipos_prueba_laboratorio (id, codigo, nombre, descripcion, "
                "unidad_predeterminada, activo) VALUES (:id, :codigo, :nombre, NULL, NULL, 0)"
            ),
            {"id": test_id, "codigo": f"INACT-{test_id[:8]}", "nombre": f"Inactiva {test_id}"},
        )
    return test_id


# ---------- patient directory: access and content ----------


def test_doctor_lists_patients_and_the_profile_id_places_an_order(app, engine) -> None:
    patient_user = _user(engine, "PACIENTE")
    profile_id = _patient_id(engine, patient_user)
    doctor = _doctor(engine)
    _relate(app, engine, patient_user, doctor)
    headers = _token(app, doctor)

    listed = {item["id"]: item for item in _all_patients(app, headers)}

    assert profile_id in listed
    response = app.test_client().post(
        ORDERS_URL,
        json={"paciente_id": listed[profile_id]["id"], "tipo_prueba_ids": [_test_type(engine)]},
        headers=headers,
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["paciente_id"] == profile_id


def test_patient_rows_carry_only_id_and_names(app, engine) -> None:
    doctor = _doctor(engine)
    _relate(app, engine, _user(engine, "PACIENTE"), doctor)
    items = _all_patients(app, _token(app, doctor))

    assert items
    assert all(set(item) == {"id", "nombres", "apellidos"} for item in items)


def test_patient_directory_is_ordered_by_apellido_nombre_and_id(app, engine) -> None:
    doctor = _doctor(engine)
    for _ in range(3):
        _relate(app, engine, _user(engine, "PACIENTE"), doctor)
    items = _all_patients(app, _token(app, doctor))

    with engine.connect() as conn:
        expected = [
            str(uuid.UUID(str(row.id)))
            for row in conn.execute(
                text(
                    "SELECT p.id FROM pacientes p JOIN usuarios u ON u.id = p.usuario_id "
                    "WHERE u.estado = 'ACTIVO' AND ("
                    "EXISTS (SELECT 1 FROM citas c "
                    "JOIN horarios_disponibles h ON h.id = c.horario_id "
                    "JOIN agendas a ON a.id = h.agenda_id "
                    "WHERE c.paciente_id = p.id AND a.medico_id = :m) "
                    "OR EXISTS (SELECT 1 FROM ordenes_laboratorio o "
                    "WHERE o.paciente_id = p.id AND o.medico_id = :m)) "
                    "ORDER BY u.apellido, u.nombre, p.id"
                ),
                {"m": _doctor_profile(engine, doctor)},
            ).all()
        ]
    assert [item["id"] for item in items] == expected
    assert len(expected) == 3


def test_patient_pages_do_not_overlap(app, engine) -> None:
    doctor = _doctor(engine)
    for _ in range(3):
        _relate(app, engine, _user(engine, "PACIENTE"), doctor)
    headers = _token(app, doctor)

    first = app.test_client().get(f"{PATIENTS_URL}?limit=2&offset=0", headers=headers)
    second = app.test_client().get(f"{PATIENTS_URL}?limit=2&offset=2", headers=headers)

    first_ids = {item["id"] for item in first.get_json()["items"]}
    second_ids = {item["id"] for item in second.get_json()["items"]}
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)


def test_patient_listing_writes_one_audit_row_without_values(app, engine) -> None:
    headers = _token(app, _doctor(engine))
    before = _count(
        engine,
        "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad = 'paciente' AND accion = 'listar'",
    )

    response = app.test_client().get(PATIENTS_URL, headers=headers)

    assert response.status_code == 200
    after = _count(
        engine,
        "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad = 'paciente' AND accion = 'listar'",
    )
    assert after == before + 1
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT TOP 1 resultado, paciente_id, entidad_id FROM auditoria_accesos "
                "WHERE entidad = 'paciente' AND accion = 'listar' ORDER BY fecha_evento DESC"
            )
        ).one()
    assert row.resultado == "OK"
    assert row.paciente_id is None
    assert row.entidad_id is None


# ---------- patient directory: relation and account rules ----------


def test_directory_lists_only_patients_related_to_the_doctor(app, engine) -> None:
    doctor, other_doctor = _doctor(engine), _doctor(engine)
    mine = _user(engine, "PACIENTE")
    theirs = _user(engine, "PACIENTE")
    stranger = _user(engine, "PACIENTE")
    _relate(app, engine, mine, doctor)
    _relate(app, engine, theirs, other_doctor)

    listed = _listed_ids(app, doctor)

    assert _patient_id(engine, mine) in listed
    assert _patient_id(engine, theirs) not in listed
    assert _patient_id(engine, stranger) not in listed


def test_a_patient_whose_only_relation_is_a_lab_order_is_listed(app, engine) -> None:
    doctor = _doctor(engine)
    patient = _user(engine, "PACIENTE")
    _insert_history_order(engine, _patient_id(engine, patient), doctor)

    assert _patient_id(engine, patient) in _listed_ids(app, doctor)


def test_a_cancelled_appointment_still_counts_as_history(app, engine) -> None:
    doctor = _doctor(engine)
    patient = _user(engine, "PACIENTE")
    _relate(app, engine, patient, doctor)
    _cancel_appointments(engine, _patient_id(engine, patient))

    assert _patient_id(engine, patient) in _listed_ids(app, doctor)


def test_inactive_and_blocked_accounts_are_not_listed(app, engine) -> None:
    doctor = _doctor(engine)
    active = _user(engine, "PACIENTE")
    inactive = _user(engine, "PACIENTE")
    blocked = _user(engine, "PACIENTE")
    for user in (active, inactive, blocked):
        _relate(app, engine, user, doctor)
    _set_account_status(engine, inactive, "INACTIVO")
    _set_account_status(engine, blocked, "BLOQUEADO")

    listed = _listed_ids(app, doctor)

    assert _patient_id(engine, active) in listed
    assert _patient_id(engine, inactive) not in listed
    assert _patient_id(engine, blocked) not in listed


# ---------- patient directory: HTTP codes ----------


@pytest.mark.parametrize("role", ["PACIENTE", "LABORATORISTA"])
def test_non_doctor_gets_403_on_the_patient_directory(app, engine, role) -> None:
    response = app.test_client().get(PATIENTS_URL, headers=_token(app, _user(engine, role)))

    assert response.status_code == 403


@pytest.mark.parametrize("query", ["?limit=0", "?limit=101", "?offset=-1", "?limit=abc"])
def test_invalid_patient_page_is_422(app, engine, query) -> None:
    response = app.test_client().get(f"{PATIENTS_URL}{query}", headers=_token(app, _doctor(engine)))

    assert response.status_code == 422


def test_patient_directory_requires_authentication(app) -> None:
    assert app.test_client().get(PATIENTS_URL).status_code == 401


# ---------- order creation: the patient must be the doctor's patient ----------


def test_order_for_an_unrelated_patient_is_403_with_the_uniform_message(app, engine) -> None:
    doctor = _doctor(engine)
    headers = _token(app, doctor)
    stranger = _patient_id(engine, _user(engine, "PACIENTE"))

    response = _post_order(app, headers, stranger, _test_type(engine))
    missing = _post_order(app, headers, str(uuid.uuid4()), _test_type(engine))

    assert response.status_code == 403
    assert response.get_json()["detail"] == UNIFORM_FORBIDDEN
    assert missing.status_code == 403
    assert missing.get_json()["detail"] == UNIFORM_FORBIDDEN
    assert _orders_of(engine, stranger) == 0


def test_order_for_a_patient_of_another_doctor_is_403_for_a_foreign_doctor(app, engine) -> None:
    owner, foreign = _doctor(engine), _doctor(engine)
    patient = _user(engine, "PACIENTE")
    _relate(app, engine, patient, owner)
    profile = _patient_id(engine, patient)
    before = _orders_of(engine, profile)

    response = _post_order(app, _token(app, foreign), profile, _test_type(engine))

    assert response.status_code == 403
    assert response.get_json()["detail"] == UNIFORM_FORBIDDEN
    assert _orders_of(engine, profile) == before


def test_order_for_a_related_patient_still_answers_201(app, engine) -> None:
    doctor = _doctor(engine)
    patient = _user(engine, "PACIENTE")
    _relate(app, engine, patient, doctor)
    profile = _patient_id(engine, patient)

    response = _post_order(app, _token(app, doctor), profile, _test_type(engine))

    assert response.status_code == 201, response.get_json()
    assert response.get_json()["paciente_id"] == profile


def test_order_for_a_patient_with_only_an_order_history_still_answers_201(app, engine) -> None:
    doctor = _doctor(engine)
    patient = _user(engine, "PACIENTE")
    profile = _patient_id(engine, patient)
    _insert_history_order(engine, profile, doctor)

    response = _post_order(app, _token(app, doctor), profile, _test_type(engine))

    assert response.status_code == 201, response.get_json()


def test_order_for_a_related_patient_with_an_inactive_account_gets_its_own_403(app, engine) -> None:
    doctor = _doctor(engine)
    patient = _user(engine, "PACIENTE")
    _relate(app, engine, patient, doctor)
    profile = _patient_id(engine, patient)
    _set_account_status(engine, patient, "INACTIVO")
    before = _orders_of(engine, profile)

    response = _post_order(app, _token(app, doctor), profile, _test_type(engine))

    assert response.status_code == 403
    assert response.get_json()["detail"] == "The patient account is not active"
    assert _orders_of(engine, profile) == before


def test_doctor_permission_without_a_doctor_profile_gets_the_profile_message(app, engine) -> None:
    # A MEDICO user with no medicos row: it holds ordenes.crear but has no profile to act with.
    headers = _token(app, _user(engine, "MEDICO"))
    profile = _patient_id(engine, _user(engine, "PACIENTE"))

    listing = app.test_client().get(PATIENTS_URL, headers=headers)
    ordering = _post_order(app, headers, profile, _test_type(engine))

    assert listing.status_code == 403
    assert listing.get_json()["detail"] == "A doctor profile is required for this action"
    assert ordering.status_code == 403
    assert ordering.get_json()["detail"] == "A doctor profile is required for this action"
    assert _orders_of(engine, profile) == 0


def test_actor_without_the_permission_keeps_the_uniform_message(app, engine) -> None:
    headers = _token(app, _user(engine, "LABORATORISTA"))
    profile = _patient_id(engine, _user(engine, "PACIENTE"))

    listing = app.test_client().get(PATIENTS_URL, headers=headers)
    ordering = _post_order(app, headers, profile, _test_type(engine))

    assert listing.status_code == 403
    assert listing.get_json()["detail"] == UNIFORM_FORBIDDEN
    assert ordering.status_code == 403
    assert ordering.get_json()["detail"] == UNIFORM_FORBIDDEN


def test_order_creation_without_a_token_is_401(app, engine) -> None:
    response = app.test_client().post(
        ORDERS_URL,
        json={"paciente_id": str(uuid.uuid4()), "tipo_prueba_ids": [_test_type(engine)]},
    )

    assert response.status_code == 401


# ---------- test-type catalog ----------


def test_catalog_returns_active_tests_and_not_inactive_ones(app, engine) -> None:
    inactive = _inactive_test_type(engine)
    active = _test_type(engine)

    response = app.test_client().get(CATALOG_URL, headers=_token(app, _user(engine, "PACIENTE")))

    body = response.get_json()
    assert response.status_code == 200
    ids = [item["id"] for item in body["items"]]
    assert active in ids
    assert inactive not in ids
    assert all(
        set(item) == {"id", "codigo", "nombre", "descripcion", "unidad_predeterminada"}
        for item in body["items"]
    )


def test_catalog_is_ordered_by_nombre_then_id(app, engine) -> None:
    response = app.test_client().get(CATALOG_URL, headers=_token(app, _user(engine, "PACIENTE")))

    with engine.connect() as conn:
        expected = [
            str(uuid.UUID(str(row.id)))
            for row in conn.execute(
                text("SELECT id FROM tipos_prueba_laboratorio WHERE activo = 1 ORDER BY nombre, id")
            ).all()
        ]
    assert [item["id"] for item in response.get_json()["items"]] == expected


@pytest.mark.parametrize("role", ["PACIENTE", "MEDICO", "LABORATORISTA"])
def test_catalog_is_available_to_any_authenticated_actor(app, engine, role) -> None:
    response = app.test_client().get(CATALOG_URL, headers=_token(app, _user(engine, role)))

    assert response.status_code == 200


def test_catalog_requires_authentication(app) -> None:
    assert app.test_client().get(CATALOG_URL).status_code == 401
