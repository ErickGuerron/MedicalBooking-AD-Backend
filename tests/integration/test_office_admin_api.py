"""Sedes and consultorios administration, through the API (real SQL Server)."""

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
from tests.integration.test_scheduling_api import _service, _slot, _token, _user

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


@pytest.fixture
def admin(app: Flask, engine: Engine) -> dict[str, str]:
    return _token(app, _user(engine, "ADMINISTRADOR"))


def _sede_of_slot(engine: Engine, slot_id: str) -> str:
    """The sede holding this slot's consultorio (each _slot call creates a fresh sede)."""
    with engine.connect() as conn:
        return str(
            conn.execute(
                text(
                    "SELECT s.id FROM horarios_disponibles h "
                    "JOIN agendas a ON a.id = h.agenda_id "
                    "JOIN consultorios c ON c.id = a.consultorio_id "
                    "JOIN sedes s ON s.id = c.sede_id WHERE h.id = :h"
                ),
                {"h": slot_id},
            ).scalar_one()
        ).lower()


def _new_sede(client, headers: dict[str, str]) -> str:
    created = client.post(
        "/api/v1/admin/sedes", json={"nombre": f"Sede {uuid.uuid4().hex[:8]}"}, headers=headers
    )
    assert created.status_code == 201, created.get_json()
    return created.get_json()["id"]


def _book(engine: Engine, slot_id: str, estado: str) -> str:
    """A patient booking on the slot, in the given state. Returns the cita id."""
    paciente = _user(engine, "PACIENTE")
    with engine.begin() as conn:
        # _user already creates the pacientes row for a PACIENTE; read it back.
        patient_id = conn.execute(
            text("SELECT id FROM pacientes WHERE usuario_id = :u"), {"u": paciente}
        ).scalar_one()
        cita = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, "
                "reservada_en, clave_idempotencia) VALUES (:id, :p, :h, :s, :e, "
                "SYSUTCDATETIME(), :k)"
            ),
            {
                "id": cita,
                "p": patient_id,
                "h": slot_id,
                "s": _service(engine),
                "e": estado,
                "k": f"sedes-{cita}",
            },
        )
    return cita


def test_administrator_creates_lists_and_edits_sedes_and_consultorios(
    app: Flask, engine: Engine, admin: dict[str, str]
) -> None:
    client = app.test_client()
    nombre = f"Sede {uuid.uuid4().hex[:8]}"

    created = client.post(
        "/api/v1/admin/sedes", json={"nombre": nombre, "direccion": "Av. Test 1"}, headers=admin
    )
    assert created.status_code == 201, created.get_json()
    sede_id = created.get_json()["id"]
    assert created.get_json()["activo"] is True

    office = client.post(
        f"/api/v1/admin/sedes/{sede_id}/consultorios",
        json={"nombre": "Consultorio A"},
        headers=admin,
    )
    assert office.status_code == 201, office.get_json()
    consultorio_id = office.get_json()["consultorios"][0]["id"]

    renamed = client.patch(
        f"/api/v1/admin/sedes/{sede_id}", json={"direccion": None}, headers=admin
    )
    assert renamed.status_code == 200
    assert renamed.get_json()["direccion"] is None
    assert renamed.get_json()["nombre"] == nombre

    office_off = client.patch(
        f"/api/v1/admin/consultorios/{consultorio_id}", json={"activo": False}, headers=admin
    )
    assert office_off.status_code == 200
    assert office_off.get_json()["activo"] is False

    listed = client.get("/api/v1/admin/sedes", headers=admin)
    assert listed.status_code == 200
    mine = next(s for s in listed.get_json()["items"] if s["id"] == sede_id)
    assert mine["consultorios"] == [
        {"id": consultorio_id, "nombre": "Consultorio A", "activo": False}
    ]


def test_doctors_and_patients_cannot_manage_sedes(app: Flask, engine: Engine) -> None:
    client = app.test_client()
    for role in ("MEDICO", "PACIENTE"):
        headers = _token(app, _user(engine, role))
        denied = client.post("/api/v1/admin/sedes", json={"nombre": "Sede"}, headers=headers)
        assert denied.status_code == 403, denied.get_json()
        assert client.get("/api/v1/admin/sedes", headers=headers).status_code == 403


def test_deactivating_a_sede_is_refused_while_a_future_booking_holds_it(
    app: Flask, engine: Engine, admin: dict[str, str]
) -> None:
    client = app.test_client()
    slot_id = _slot(engine, days_ahead=3)
    sede_id = _sede_of_slot(engine, slot_id)
    cita = _book(engine, slot_id, "CONFIRMADA")

    refused = client.patch(f"/api/v1/admin/sedes/{sede_id}", json={"activo": False}, headers=admin)
    assert refused.status_code == 409, refused.get_json()

    with engine.begin() as conn:
        conn.execute(text("UPDATE citas SET estado = 'CANCELADA' WHERE id = :c"), {"c": cita})
    allowed = client.patch(f"/api/v1/admin/sedes/{sede_id}", json={"activo": False}, headers=admin)
    assert allowed.status_code == 200, allowed.get_json()
    assert allowed.get_json()["activo"] is False


def test_a_past_booking_does_not_block_deactivation(
    app: Flask, engine: Engine, admin: dict[str, str]
) -> None:
    client = app.test_client()
    slot_id = _slot(engine, days_ahead=2)
    past = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE horarios_disponibles SET inicio_utc = :p, "
                "fin_utc = DATEADD(minute, 30, :p) WHERE id = :h"
            ),
            {"p": past, "h": slot_id},
        )
    _book(engine, slot_id, "CONFIRMADA")
    response = client.patch(
        f"/api/v1/admin/sedes/{_sede_of_slot(engine, slot_id)}",
        json={"activo": False},
        headers=admin,
    )
    assert response.status_code == 200, response.get_json()


def test_a_duplicate_consultorio_name_in_the_same_sede_is_a_conflict(
    app: Flask, engine: Engine, admin: dict[str, str]
) -> None:
    client = app.test_client()
    sede_id = _new_sede(client, admin)
    first = client.post(
        f"/api/v1/admin/sedes/{sede_id}/consultorios", json={"nombre": "Duplicado"}, headers=admin
    )
    assert first.status_code == 201, first.get_json()
    second = client.post(
        f"/api/v1/admin/sedes/{sede_id}/consultorios", json={"nombre": "Duplicado"}, headers=admin
    )
    assert second.status_code == 409, second.get_json()


def test_work_hours_listing_still_shows_only_active_consultorios(
    app: Flask, engine: Engine, admin: dict[str, str]
) -> None:
    """S10: GET /admin/consultorios keeps its shape and skips inactive offices."""
    client = app.test_client()
    sede_id = _new_sede(client, admin)
    created = client.post(
        f"/api/v1/admin/sedes/{sede_id}/consultorios", json={"nombre": "Oculto"}, headers=admin
    ).get_json()
    hidden = next(c["id"] for c in created["consultorios"] if c["nombre"] == "Oculto")
    client.patch(f"/api/v1/admin/consultorios/{hidden}", json={"activo": False}, headers=admin)

    listed = client.get("/api/v1/admin/consultorios", headers=admin)
    assert listed.status_code == 200
    items = listed.get_json()["items"]
    assert all(set(item) == {"id", "nombre", "sede"} for item in items)
    assert hidden not in {item["id"] for item in items}
