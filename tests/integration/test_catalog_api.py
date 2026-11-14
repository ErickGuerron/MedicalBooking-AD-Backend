"""Catalog against real SQL Server: admin maintenance, listings, public doctor data, filters.

Each test creates its own users, specialty and doctor with unique names.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_scheduling_api import KEY, _user
from tests.integration.test_scheduling_api import _token as _headers

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _doctor(engine: Engine) -> str:
    """A doctor with an open-ended agenda and one slot two days ahead; return the doctor id."""
    user = _user(engine, "MEDICO")
    start = (datetime.now(UTC) + timedelta(days=2)).replace(second=0, microsecond=0, tzinfo=None)
    with engine.begin() as conn:
        medico_id = conn.execute(
            text(
                "INSERT INTO medicos (id, usuario_id, registro_profesional) "
                "OUTPUT inserted.id VALUES (NEWID(), :u, :reg)"
            ),
            {"u": user, "reg": f"REG-{user}"},
        ).scalar_one()
        sede = conn.execute(
            text("INSERT INTO sedes (id, nombre) OUTPUT inserted.id VALUES (NEWID(), N'Sede')")
        ).scalar_one()
        consultorio = conn.execute(
            text(
                "INSERT INTO consultorios (id, sede_id, nombre) OUTPUT inserted.id "
                "VALUES (NEWID(), :s, N'C1')"
            ),
            {"s": sede},
        ).scalar_one()
        agenda = conn.execute(
            text(
                "INSERT INTO agendas (id, medico_id, consultorio_id, fecha_inicio) "
                "OUTPUT inserted.id VALUES (NEWID(), :m, :c, CAST(:d AS date))"
            ),
            {"m": medico_id, "c": consultorio, "d": start.date()},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc) "
                "VALUES (NEWID(), :a, :i, DATEADD(minute, 30, :i))"
            ),
            {"a": agenda, "i": start},
        )
    return str(uuid.UUID(str(medico_id)))


def _create_specialty(app: Flask, admin: dict[str, str]) -> dict:
    response = app.test_client().post(
        "/api/v1/specialties", json={"nombre": f"Esp-{uuid.uuid4()}"}, headers=admin
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_admin_seeds_specialties_and_patient_lists_them(app, engine) -> None:
    admin = _headers(app, _user(engine, "ADMINISTRADOR"))
    patient = _headers(app, _user(engine, "PACIENTE"))
    client = app.test_client()
    specialty = _create_specialty(app, admin)

    duplicate = client.post(
        "/api/v1/specialties", json={"nombre": specialty["nombre"]}, headers=admin
    )
    forbidden = client.post("/api/v1/specialties", json={"nombre": "X"}, headers=patient)
    listing = client.get("/api/v1/specialties", headers=patient)

    assert duplicate.status_code == 409
    assert forbidden.status_code == 403
    assert specialty in listing.get_json()["items"]
    deactivated = client.patch(
        f"/api/v1/specialties/{specialty['id']}", json={"activo": False}, headers=admin
    )
    assert deactivated.status_code == 200
    hidden = client.get("/api/v1/specialties", headers=patient).get_json()["items"]
    assert specialty["id"] not in [item["id"] for item in hidden]
    missing = client.patch("/api/v1/specialties/2147483647", json={"activo": True}, headers=admin)
    assert missing.status_code == 404


def test_doctor_listing_filters_by_specialty_and_hides_private_data(app, engine) -> None:
    admin = _headers(app, _user(engine, "ADMINISTRADOR"))
    patient = _headers(app, _user(engine, "PACIENTE"))
    client = app.test_client()
    specialty = _create_specialty(app, admin)
    doctor_id = _doctor(engine)
    other_id = _doctor(engine)

    assigned = client.put(
        f"/api/v1/doctors/{doctor_id}/specialties",
        json={"especialidad_ids": [specialty["id"]]},
        headers=admin,
    )
    items = client.get(
        f"/api/v1/doctors?especialidad_id={specialty['id']}", headers=patient
    ).get_json()["items"]

    assert assigned.status_code == 200
    assert [item["id"] for item in items] == [doctor_id]
    doctor = items[0]
    assert set(doctor) == {
        "id",
        "nombre",
        "especialidades",
        "duracion_cita_min",
        "horario_atencion",
    }
    assert doctor["especialidades"] == [{"id": specialty["id"], "nombre": specialty["nombre"]}]
    assert len(doctor["horario_atencion"]["dias"]) == 1
    assert doctor["horario_atencion"]["vigente_hasta"] is None
    listing = client.get("/api/v1/doctors", headers=patient).get_json()["items"]
    assert {doctor_id, other_id} <= {item["id"] for item in listing}


def test_doctor_specialties_reject_unknown_ids_and_unknown_doctors(app, engine) -> None:
    admin = _headers(app, _user(engine, "ADMINISTRADOR"))
    client = app.test_client()
    doctor_id = _doctor(engine)

    unknown_specialty = client.put(
        f"/api/v1/doctors/{doctor_id}/specialties",
        json={"especialidad_ids": [2147483647]},
        headers=admin,
    )
    unknown_doctor = client.put(
        f"/api/v1/doctors/{uuid.uuid4()}/specialties", json={"especialidad_ids": []}, headers=admin
    )

    assert unknown_specialty.status_code == 422
    assert unknown_doctor.status_code == 404


def test_services_catalog_and_specialty_link(app, engine) -> None:
    admin = _headers(app, _user(engine, "ADMINISTRADOR"))
    patient = _headers(app, _user(engine, "PACIENTE"))
    client = app.test_client()
    specialty = _create_specialty(app, admin)

    created = client.post(
        "/api/v1/services",
        json={"nombre": f"Servicio-{uuid.uuid4()}", "duracion_min": 45, "precio_base": "30.5"},
        headers=admin,
    )
    service = created.get_json()
    linked = client.patch(
        f"/api/v1/services/{service['id']}",
        json={"especialidad_id": specialty["id"]},
        headers=admin,
    )
    listing = client.get("/api/v1/services", headers=patient).get_json()["items"]

    assert created.status_code == 201
    assert (service["precio_base"], service["moneda"], service["especialidad_id"]) == (
        "30.50",
        "USD",
        None,
    )
    assert linked.get_json()["especialidad_id"] == specialty["id"]
    assert linked.get_json() in listing


def test_availability_adds_the_doctor_and_filters_by_specialty(app, engine) -> None:
    admin = _headers(app, _user(engine, "ADMINISTRADOR"))
    patient = _headers(app, _user(engine, "PACIENTE"))
    client = app.test_client()
    specialty = _create_specialty(app, admin)
    doctor_id = _doctor(engine)
    _doctor(engine)
    client.put(
        f"/api/v1/doctors/{doctor_id}/specialties",
        json={"especialidad_ids": [specialty["id"]]},
        headers=admin,
    )
    window = (
        f"desde={(datetime.now(UTC) + timedelta(days=1)).isoformat()}"
        f"&hasta={(datetime.now(UTC) + timedelta(days=5)).isoformat()}"
    ).replace("+", "%2B")

    items = client.get(
        f"/api/v1/availability?{window}&especialidad_id={specialty['id']}", headers=patient
    ).get_json()["items"]

    assert [item["medico_id"] for item in items] == [doctor_id]
    assert items[0]["medico"]["id"] == doctor_id
    assert items[0]["medico"]["nombre"]
