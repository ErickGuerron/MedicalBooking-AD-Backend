"""Cancellation window through the API: no cancel more than one hour after the start.

Real SQL Server.
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
from tests.integration.test_doctor_cancel_limits import _doctor_with_slots, _estado
from tests.integration.test_scheduling_api import _book, _service, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _booked_at(app: Flask, engine: Engine, start_offset_hours: float) -> tuple[str, str, str]:
    """A patient's appointment whose slot starts at now + offset hours.

    Returns (patient, cita, slot).
    """
    _doctor, slots = _doctor_with_slots(engine, [2])
    patient = _user(engine, "PACIENTE")
    response = _book(
        app.test_client(),
        _token(app, patient),
        key=f"window-{uuid.uuid4()}",
        slot=slots[0],
        servicio=_service(engine),
    )
    assert response.status_code == 201, response.get_json()
    start = datetime.now(UTC) + timedelta(hours=start_offset_hours)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE horarios_disponibles "
                "SET inicio_utc = :i, fin_utc = DATEADD(minute, 30, :i) WHERE id = :h"
            ),
            {"i": start.replace(tzinfo=None, second=0, microsecond=0), "h": slots[0]},
        )
    return patient, response.get_json()["id"], slots[0]


def test_a_patient_can_cancel_within_the_hour_after_the_start(app, engine) -> None:
    patient, cita, _slot = _booked_at(app, engine, start_offset_hours=-0.5)

    response = app.test_client().post(
        f"/api/v1/appointments/{cita}/cancel",
        json={"motivo_texto": "Llegaré tarde"},
        headers=_token(app, patient),
    )

    assert response.status_code == 200, response.get_json()
    assert _estado(engine, cita) == "CANCELADA"


def test_a_patient_cannot_cancel_more_than_an_hour_after_the_start(app, engine) -> None:
    patient, cita, _slot = _booked_at(app, engine, start_offset_hours=-2)

    response = app.test_client().post(
        f"/api/v1/appointments/{cita}/cancel",
        json={"motivo_texto": "Ya no puedo"},
        headers=_token(app, patient),
    )

    assert response.status_code == 409, response.get_json()
    assert _estado(engine, cita) == "PENDIENTE_PAGO"


def test_an_admin_approval_cannot_reopen_the_window(app, engine) -> None:
    _patient, cita, _slot = _booked_at(app, engine, start_offset_hours=-2)
    admin = _user(engine, "ADMINISTRADOR")

    response = app.test_client().post(
        f"/api/v1/appointments/{cita}/cancellation-approval",
        json={"motivo": "Caso justificado"},
        headers=_token(app, admin),
    )

    assert response.status_code == 409, response.get_json()
