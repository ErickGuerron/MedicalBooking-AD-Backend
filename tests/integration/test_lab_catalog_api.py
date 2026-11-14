"""Patient directory and lab test catalog (lab order form) against real SQL Server.

Each test creates its own users and test types, so counts do not depend on other tests.
Patients share the same synthetic names, so directory checks page through the whole list.
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
from tests.integration.test_lab_api import _doctor, _patient_id, _test_type
from tests.integration.test_scheduling_api import _count, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PATIENTS_URL = "/api/v1/patients"
CATALOG_URL = "/api/v1/lab/test-types"
ORDERS_URL = "/api/v1/lab/orders"
PAGE = 100


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
    headers = _token(app, _doctor(engine))

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
    _user(engine, "PACIENTE")
    items = _all_patients(app, _token(app, _doctor(engine)))

    assert items
    assert all(set(item) == {"id", "nombres", "apellidos"} for item in items)


def test_patient_directory_is_ordered_by_apellido_nombre_and_id(app, engine) -> None:
    _user(engine, "PACIENTE")
    items = _all_patients(app, _token(app, _doctor(engine)))

    with engine.connect() as conn:
        expected = [
            str(uuid.UUID(str(row.id)))
            for row in conn.execute(
                text(
                    "SELECT p.id FROM pacientes p JOIN usuarios u ON u.id = p.usuario_id "
                    "ORDER BY u.apellido, u.nombre, p.id"
                )
            ).all()
        ]
    assert [item["id"] for item in items] == expected


def test_patient_pages_do_not_overlap(app, engine) -> None:
    _user(engine, "PACIENTE")
    headers = _token(app, _doctor(engine))

    first = app.test_client().get(f"{PATIENTS_URL}?limit=2&offset=0", headers=headers)
    second = app.test_client().get(f"{PATIENTS_URL}?limit=2&offset=2", headers=headers)

    first_ids = {item["id"] for item in first.get_json()["items"]}
    second_ids = {item["id"] for item in second.get_json()["items"]}
    assert len(first_ids) == 2
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
