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
