"""Consultations against real SQL Server.

The doctor's agenda names the patient and the doctor opens one consultation per appointment.
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
from tests.integration.test_lab_api import _doctor, _patient_id
from tests.integration.test_scheduling_api import KEY, _token, _user

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _confirmed_cita(
    engine: Engine, doctor_user: str, patient_id: str, estado: str = "CONFIRMADA"
) -> str:
    """An appointment of the doctor's own agenda for the patient profile, in ``estado``.

    A user without a doctor profile gets a random id: the request is refused before it matters.
    """
    cita_id = str(uuid.uuid4())
    with engine.begin() as conn:
        medico = conn.execute(
            text("SELECT id FROM medicos WHERE usuario_id = :u"), {"u": doctor_user}
        ).scalar()
        if medico is None:
            return cita_id
        sede, consultorio, agenda, slot, servicio = (str(uuid.uuid4()) for _ in range(5))
        conn.execute(text("INSERT INTO sedes (id, nombre) VALUES (:s, N'Sede')"), {"s": sede})
        conn.execute(
            text("INSERT INTO consultorios (id, sede_id, nombre) VALUES (:c, :s, N'C1')"),
            {"c": consultorio, "s": sede},
        )
        conn.execute(
            text(
                "INSERT INTO agendas (id, medico_id, consultorio_id, fecha_inicio) "
                "VALUES (:a, :m, :c, CAST(SYSUTCDATETIME() AS date))"
            ),
            {"a": agenda, "m": medico, "c": consultorio},
        )
        conn.execute(
            text(
                "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc, estado) "
                "VALUES (:h, :a, DATEADD(day, 3, SYSUTCDATETIME()), "
                "DATEADD(minute, 30, DATEADD(day, 3, SYSUTCDATETIME())), 'RESERVADO')"
            ),
            {"h": slot, "a": agenda},
        )
        conn.execute(
            text(
                "INSERT INTO servicios_medicos (id, nombre, duracion_min, precio_base) "
                "VALUES (:s, N'Consulta', 30, 25.00)"
            ),
            {"s": servicio},
        )
        conn.execute(
            text(
                "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, "
                "reservada_en, clave_idempotencia) "
                "VALUES (:c, :p, :h, :s, :estado, SYSUTCDATETIME(), :k)"
            ),
            {
                "c": cita_id,
                "p": patient_id,
                "h": slot,
                "s": servicio,
                "estado": estado,
                "k": f"k-{cita_id}",
            },
        )
    return cita_id


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
