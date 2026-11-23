"""Doctor work hours, slot generation and the service window, through the API (real SQL Server)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
BRANCH_OFFSET = timedelta(hours=-5)
MORNING_BLOCK = {"inicio": "08:00", "fin": "10:00"}
AFTERNOON_BLOCK = {"inicio": "12:00", "fin": "17:00"}


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _today_branch() -> date:
    return (datetime.now(UTC) + BRANCH_OFFSET).date()


def _doctor_with_office(engine: Engine) -> tuple[str, str, str]:
    """A doctor profile and a consultorio. Returns (usuario_id, medico_id, consultorio_id)."""
    user = _user(engine, "MEDICO")
    medico_id = str(uuid.uuid4())
    sede_id = str(uuid.uuid4())
    consultorio_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO medicos (id, usuario_id, registro_profesional, duracion_cita_min) "
                "VALUES (:m, :u, :r, 30)"
            ),
            {"m": medico_id, "u": user, "r": f"WH-{medico_id}"},
        )
        conn.execute(
            text("INSERT INTO sedes (id, nombre) VALUES (:s, :n)"),
            {"s": sede_id, "n": f"Sede WH {sede_id}"},
        )
        conn.execute(
            text("INSERT INTO consultorios (id, sede_id, nombre) VALUES (:c, :s, :n)"),
            {"c": consultorio_id, "s": sede_id, "n": f"Consultorio WH {consultorio_id}"},
        )
    return user, medico_id, consultorio_id


def _service_with_franja(engine: Engine, franja: str) -> str:
    service_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO servicios_medicos (id, nombre, duracion_min, precio_base, franja) "
                "VALUES (:s, :n, 30, 10, :f)"
            ),
            {"s": service_id, "n": f"Servicio {franja} {service_id}", "f": franja},
        )
    return service_id


def _next_monday() -> str:
    today = _today_branch()
    return (today + timedelta(days=(7 - today.weekday()) % 7 or 7)).isoformat()


def _every_day(blocks: list[dict]) -> dict:
    return {"bloques": [{"dia_semana": day, **block} for day in range(7) for block in blocks]}


def test_a_doctor_declares_the_hours_and_the_clinic_refuses_hours_outside_it(app, engine) -> None:
    doctor_user, _medico, _office = _doctor_with_office(engine)
    client = app.test_client()
    headers = _token(app, doctor_user)

    refused = client.put(
        "/api/v1/me/horario-trabajo",
        json={"bloques": [{"dia_semana": 0, "inicio": "07:00", "fin": "09:00"}]},
        headers=headers,
    )
    saved = client.put(
        "/api/v1/me/horario-trabajo", json=_every_day([MORNING_BLOCK]), headers=headers
    )

    assert refused.status_code == 422, refused.get_json()
    assert saved.status_code == 200, saved.get_json()
    assert client.get("/api/v1/me/horario-trabajo", headers=headers).get_json()["proximo"][0] == {
        "dia_semana": 0,
        "inicio": "08:00",
        "fin": "10:00",
    }


def test_the_administrator_proposes_and_generates_slots_once(app, engine) -> None:
    doctor_user, medico, office = _doctor_with_office(engine)
    client = app.test_client()
    client.put(
        "/api/v1/me/horario-trabajo",
        json=_every_day([MORNING_BLOCK, AFTERNOON_BLOCK]),
        headers=_token(app, doctor_user),
    )
    admin = _token(app, _user(engine, "ADMINISTRADOR"))
    semana = _next_monday()

    proposal = client.get(
        f"/api/v1/admin/horarios/propuesta?medico_id={medico}&semana={semana}",
        headers=admin,
    )
    body = {"medico_id": medico, "consultorio_id": office, "semana": semana}
    first = client.post("/api/v1/admin/horarios/generar", json=body, headers=admin)
    second = client.post("/api/v1/admin/horarios/generar", json=body, headers=admin)

    assert proposal.status_code == 200, proposal.get_json()
    assert {item["franja"] for item in proposal.get_json()["items"]} == {"MANANA", "TARDE"}
    assert first.status_code == 201, first.get_json()
    assert first.get_json()["creados"] == proposal.get_json()["total"]
    assert second.get_json() == {"creados": 0, "existentes": first.get_json()["creados"]}


def test_the_doctor_cannot_generate_slots_and_the_range_is_checked(app, engine) -> None:
    doctor_user, medico, office = _doctor_with_office(engine)
    client = app.test_client()
    doctor = _token(app, doctor_user)
    semana = _next_monday()

    denied = client.post(
        "/api/v1/admin/horarios/generar",
        json={"medico_id": medico, "consultorio_id": office, "semana": semana},
        headers=doctor,
    )
    invalid = client.get(
        f"/api/v1/admin/horarios/propuesta?medico_id={medico}&semana=2026-02-30",
        headers=_token(app, _user(engine, "ADMINISTRADOR")),
    )

    assert denied.status_code == 403, denied.get_json()
    assert invalid.status_code == 422, invalid.get_json()


def test_a_service_books_only_into_its_own_window(app, engine) -> None:
    doctor_user, medico, office = _doctor_with_office(engine)
    client = app.test_client()
    client.put(
        "/api/v1/me/horario-trabajo",
        json=_every_day([MORNING_BLOCK, AFTERNOON_BLOCK]),
        headers=_token(app, doctor_user),
    )
    admin = _token(app, _user(engine, "ADMINISTRADOR"))
    semana = _next_monday()
    client.post(
        "/api/v1/admin/horarios/generar",
        json={"medico_id": medico, "consultorio_id": office, "semana": semana},
        headers=admin,
    )
    window = f"desde={semana}T00:00:00-05:00&hasta={semana}T23:59:00-05:00&medico_id={medico}"
    patient = _token(app, _user(engine, "PACIENTE"))
    samples = _service_with_franja(engine, "MANANA")
    consultation = _service_with_franja(engine, "TARDE")

    morning = client.get(
        f"/api/v1/availability?{window}&servicio_id={samples}", headers=patient
    ).get_json()["items"]
    afternoon = client.get(
        f"/api/v1/availability?{window}&servicio_id={consultation}", headers=patient
    ).get_json()["items"]

    assert morning and afternoon
    assert all(datetime.fromisoformat(i["inicio_utc"]).astimezone(UTC).hour < 15 for i in morning)
    assert all(
        datetime.fromisoformat(i["inicio_utc"]).astimezone(UTC).hour >= 17 for i in afternoon
    )

    wrong = client.post(
        "/api/v1/appointments",
        json={"horario_id": afternoon[0]["horario_id"], "servicio_id": samples},
        headers={**patient, "Idempotency-Key": f"franja-{uuid.uuid4()}"},
    )
    right = client.post(
        "/api/v1/appointments",
        json={"horario_id": afternoon[0]["horario_id"], "servicio_id": consultation},
        headers={**patient, "Idempotency-Key": f"franja-{uuid.uuid4()}"},
    )

    assert wrong.status_code == 422, wrong.get_json()
    assert right.status_code == 201, right.get_json()
