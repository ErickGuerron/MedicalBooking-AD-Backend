"""Patient clinical profile for the WHO 2024 adjustments (feature doc S12): self-reported, audited.

Only the patient writes their own altitude of residence and smoking status. Every write and every
denial is audited. Values are validated before they reach the database.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_scheduling_api import _count, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
URL = "/api/v1/me/perfil-clinico"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _put(app: Flask, headers: dict, body: dict):
    return app.test_client().put(URL, json=body, headers=headers)


def _stored(engine: Engine, user_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT p.altitud_residencia_m, p.fumador, p.cigarrillos_dia FROM pacientes p "
                "JOIN usuarios u ON u.id = p.usuario_id WHERE u.id = :u"
            ),
            {"u": user_id},
        ).one()


def test_the_patient_saves_their_own_altitude_and_smoking(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = _put(
        app,
        _token(app, patient),
        {"altitud_residencia_m": 2400, "fumador": True, "cigarrillos_dia": 12},
    )

    assert response.status_code == 200, response.get_json()
    row = _stored(engine, patient)
    assert (row.altitud_residencia_m, row.fumador, row.cigarrillos_dia) == (2400, True, 12)


def test_a_smoker_must_declare_the_cigarettes_per_day(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = _put(app, _token(app, patient), {"altitud_residencia_m": 0, "fumador": True})

    assert response.status_code == 422
    assert _stored(engine, patient).fumador is None


@pytest.mark.parametrize(
    "body",
    [
        {"fumador": False},  # altitude missing
        {"altitud_residencia_m": 100},  # smoking missing
        {"altitud_residencia_m": None, "fumador": False},  # explicit unknown altitude
        {"altitud_residencia_m": 100, "fumador": None},  # explicit unknown smoking
    ],
)
def test_altitude_and_smoking_are_both_mandatory(app, engine, body: dict) -> None:
    patient = _user(engine, "PACIENTE")

    response = _put(app, _token(app, patient), body)

    assert response.status_code == 422
    assert _stored(engine, patient).altitud_residencia_m is None


def test_a_complete_non_smoker_profile_is_accepted(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = _put(app, _token(app, patient), {"altitud_residencia_m": 0, "fumador": False})

    assert response.status_code == 200, response.get_json()
    row = _stored(engine, patient)
    assert (row.altitud_residencia_m, row.fumador, row.cigarrillos_dia) == (0, False, None)


def test_a_non_smoker_cannot_declare_cigarettes(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = _put(
        app,
        _token(app, patient),
        {"altitud_residencia_m": 100, "fumador": False, "cigarrillos_dia": 5},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("altitude", [-1, 9000])
def test_altitude_must_be_a_plausible_residence(app, engine, altitude: int) -> None:
    patient = _user(engine, "PACIENTE")

    response = _put(app, _token(app, patient), {"altitud_residencia_m": altitude, "fumador": False})

    assert response.status_code == 422


def test_a_doctor_cannot_write_the_patient_profile_and_the_denial_is_audited(app, engine) -> None:
    doctor = _user(engine, "MEDICO")

    response = _put(app, _token(app, doctor), {"altitud_residencia_m": 100, "fumador": False})

    assert response.status_code == 403
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad = 'perfil_clinico' "
            "AND resultado = 'DENEGADO'",
        )
        >= 1
    )


def _get(app: Flask, headers: dict):
    return app.test_client().get(URL, headers=headers)


def test_a_new_patient_reads_an_empty_profile_so_the_client_knows_to_ask(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = _get(app, _token(app, patient))

    assert response.status_code == 200, response.get_json()
    assert response.get_json() == {
        "altitud_residencia_m": None,
        "fumador": None,
        "cigarrillos_dia": None,
        "sexo": None,
        "fecha_nacimiento": None,
    }


def test_the_patient_reads_back_what_they_declared(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    headers = _token(app, patient)
    body = {"altitud_residencia_m": 2850, "fumador": True, "cigarrillos_dia": 5}
    assert _put(app, headers, body).status_code == 200

    response = _get(app, headers)

    assert response.status_code == 200
    assert response.get_json() == {**body, "sexo": None, "fecha_nacimiento": None}


def _demographics(engine: Engine, user_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT sexo_registrado, fecha_nacimiento FROM pacientes WHERE usuario_id = :u"),
            {"u": user_id},
        ).one()
    return row.sexo_registrado, None if row.fecha_nacimiento is None else str(row.fecha_nacimiento)


BASE = {"altitud_residencia_m": 100, "fumador": False}


def test_sex_and_birth_date_are_saved_and_read_back(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    headers = _token(app, patient)

    put = _put(app, headers, {**BASE, "sexo": "F", "fecha_nacimiento": "1990-05-17"})

    expected = {**BASE, "cigarrillos_dia": None, "sexo": "F", "fecha_nacimiento": "1990-05-17"}
    assert put.status_code == 200, put.get_json()
    assert put.get_json() == expected
    assert _get(app, headers).get_json() == expected
    assert _demographics(engine, patient) == ("F", "1990-05-17")


def test_omitting_demographics_leaves_them_and_null_clears_them(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    headers = _token(app, patient)
    _put(app, headers, {**BASE, "sexo": "M", "fecha_nacimiento": "1985-01-02"})

    omitted = _put(app, headers, {"altitud_residencia_m": 3000, "fumador": False})
    sex_cleared = _put(app, headers, {**BASE, "sexo": None})
    both_cleared = _put(app, headers, {**BASE, "sexo": None, "fecha_nacimiento": None})

    assert omitted.status_code == 200
    assert omitted.get_json()["sexo"] == "M"
    assert omitted.get_json()["fecha_nacimiento"] == "1985-01-02"
    assert sex_cleared.get_json()["sexo"] is None
    assert sex_cleared.get_json()["fecha_nacimiento"] == "1985-01-02"
    assert both_cleared.get_json()["fecha_nacimiento"] is None
    assert _demographics(engine, patient) == (None, None)


@pytest.mark.parametrize(
    "extra",
    [
        {"sexo": "X"},
        {"sexo": "m"},
        {"sexo": 1},
        {"fecha_nacimiento": "2999-01-01"},
        {"fecha_nacimiento": "1800-01-01"},
        {"fecha_nacimiento": "1990-02-30"},
        {"fecha_nacimiento": "17/05/1990"},
        {"fecha_nacimiento": 19900517},
        {"genero": "F"},
    ],
)
def test_invalid_demographics_are_rejected_and_nothing_is_written(app, engine, extra) -> None:
    patient = _user(engine, "PACIENTE")

    response = _put(app, _token(app, patient), {**BASE, **extra})

    assert response.status_code == 422
    assert _stored(engine, patient).altitud_residencia_m is None
    assert _demographics(engine, patient) == (None, None)


def test_demographics_are_own_data_and_the_write_is_audited(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    other = _user(engine, "PACIENTE")

    _put(app, _token(app, patient), {**BASE, "sexo": "F", "fecha_nacimiento": "1990-05-17"})

    assert _demographics(engine, other) == (None, None)
    assert _get(app, _token(app, other)).get_json()["sexo"] is None
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos a JOIN pacientes p ON p.id = a.paciente_id "
            "WHERE a.entidad = 'perfil_clinico' AND a.resultado = 'OK' "
            f"AND p.usuario_id = '{patient}'",
        )
        == 1
    )


def test_a_doctor_cannot_read_the_patient_profile_endpoint(app, engine) -> None:
    doctor = _user(engine, "MEDICO")

    assert _get(app, _token(app, doctor)).status_code == 403


def test_reading_the_profile_needs_a_session(app) -> None:
    assert app.test_client().get(URL).status_code == 401
