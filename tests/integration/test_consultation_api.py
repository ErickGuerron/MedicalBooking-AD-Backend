"""Consultation and order link against real SQL Server.

The doctor's agenda names the patient, the doctor opens one consultation per appointment, and a
lab order needs an own, matching, confirmed appointment and stores its consultation.
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
from tests.integration.test_lab_api import (
    _confirmed_cita,
    _create_order,
    _doctor,
    _patient_id,
    _test_type,
)
from tests.integration.test_scheduling_api import KEY, _token, _user

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _consultations(engine: Engine, cita_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM consultas_medicas WHERE cita_id = :c"), {"c": cita_id}
        ).scalar_one()


def _setup(app, engine, estado: str = "CONFIRMADA"):
    patient = _user(engine, "PACIENTE")
    patient_id = _patient_id(engine, patient)
    doctor = _doctor(engine)
    cita = _confirmed_cita(engine, doctor, patient_id, estado)
    return patient, patient_id, doctor, _token(app, doctor), cita


def _open(client, headers, cita: str):
    return client.post(f"/api/v1/appointments/{cita}/consultation", headers=headers)


def test_agenda_names_the_patient_but_the_patient_listing_does_not(app, engine) -> None:
    patient, patient_id, _, doctor_headers, cita = _setup(app, engine)
    client = app.test_client()

    agenda = client.get("/api/v1/appointments", headers=doctor_headers).get_json()["items"]
    own = client.get("/api/v1/appointments", headers=_token(app, patient)).get_json()["items"]

    row = next(item for item in agenda if item["id"] == cita)
    assert row["paciente"]["id"] == patient_id
    assert row["paciente"]["nombre"]
    assert all("paciente" not in item for item in own)


def test_doctor_opens_one_consultation_per_appointment(app, engine) -> None:
    _, _, _, headers, cita = _setup(app, engine)
    client = app.test_client()

    first = _open(client, headers, cita)
    again = _open(client, headers, cita)

    assert (first.status_code, again.status_code) == (201, 200)
    assert first.get_json() == again.get_json()
    assert _consultations(engine, cita) == 1


def test_consultation_refusals(app, engine) -> None:
    patient, _, _, headers, cita = _setup(app, engine)
    other_headers = _setup(app, engine)[3]
    _, _, _, pending_headers, pending = _setup(app, engine, "PENDIENTE_PAGO")
    client = app.test_client()

    assert _open(client, _token(app, patient), cita).status_code == 403
    assert _open(client, other_headers, cita).status_code == 403
    assert _open(client, headers, str(uuid.uuid4())).status_code == 403
    assert _open(client, pending_headers, pending).status_code == 409
    assert _consultations(engine, cita) == 0
    assert _consultations(engine, pending) == 0


def test_order_requires_an_appointment_and_stores_its_consultation(app, engine) -> None:
    _, patient_id, _, headers, cita = _setup(app, engine)
    client = app.test_client()
    tests = [_test_type(engine)]

    missing = client.post(
        "/api/v1/lab/orders",
        json={"paciente_id": patient_id, "tipo_prueba_ids": tests},
        headers=headers,
    )
    created = _create_order(client, headers, patient_id, tests, cita_id=cita)
    second = _create_order(client, headers, patient_id, tests, cita_id=cita)

    assert missing.status_code == 422
    assert created.status_code == 201, created.get_json()
    opened = _open(client, headers, cita).get_json()
    assert created.get_json()["consulta_id"] == opened["id"]
    assert second.get_json()["consulta_id"] == opened["id"]
    assert _consultations(engine, cita) == 1


def test_order_refusals_by_appointment(app, engine) -> None:
    _, patient_id, _, headers, cita = _setup(app, engine)
    other_patient = _patient_id(engine, _user(engine, "PACIENTE"))
    other_cita = _setup(app, engine)[4]
    _, pending_patient_id, _, pending_headers, pending = _setup(app, engine, "PENDIENTE_PAGO")
    client = app.test_client()
    tests = [_test_type(engine)]

    foreign = _create_order(client, headers, patient_id, tests, cita_id=other_cita)
    unknown = _create_order(client, headers, patient_id, tests, cita_id=str(uuid.uuid4()))
    wrong_patient = _create_order(client, headers, other_patient, tests, cita_id=cita)
    not_confirmed = _create_order(
        client, pending_headers, pending_patient_id, tests, cita_id=pending
    )

    assert foreign.status_code == 403
    assert unknown.status_code == 403
    assert wrong_patient.status_code == 422
    assert not_confirmed.status_code == 409
    assert _consultations(engine, pending) == 0
