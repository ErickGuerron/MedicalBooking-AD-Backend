# ruff: noqa: F811  (pytest injects the fixtures imported below by parameter name; not a redefinition)
"""Hostile and edge cases for sedes administration, through the API (real SQL Server).

Not run yet in this environment: SQL Server must be reachable on localhost,1433.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask
from sqlalchemy import Engine, text

from tests.integration.test_office_admin_api import (  # noqa: F401  (pytest fixtures)
    _book,
    _new_sede,
    _sede_of_slot,
    admin,
    app,
    engine,
)
from tests.integration.test_scheduling_api import _slot, _token, _user

pytestmark = pytest.mark.integration

SQL_INJECTION = "Sede'); DROP TABLE sedes; --"


def _sede_row(engine: Engine, sede_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT nombre, direccion, activo, zona_horaria FROM sedes WHERE id = :s"),
            {"s": sede_id},
        ).first()


# ---------- permission evasion ----------


def test_a_patient_cannot_escalate_by_sending_roles_or_permissions(
    app: Flask, engine: Engine
) -> None:
    client = app.test_client()
    paciente = _user(engine, "PACIENTE")
    headers = _token(app, paciente)
    response = client.post(
        "/api/v1/admin/sedes",
        json={
            "nombre": "Intruso",
            "roles": ["ADMINISTRADOR"],
            "permissions": ["sedes.administrar"],
        },
        headers=headers,
    )
    assert response.status_code in (403, 422), response.get_json()  # refused, never granted
    with engine.connect() as conn:
        granted = conn.execute(
            text(
                "SELECT COUNT(*) FROM usuarios_roles ur JOIN roles r ON r.id = ur.rol_id "
                "WHERE ur.usuario_id = :u AND r.nombre = 'ADMINISTRADOR'"
            ),
            {"u": paciente},
        ).scalar()
    assert granted == 0


def test_a_doctor_cannot_create_or_deactivate_offices(app: Flask, engine: Engine, admin) -> None:
    client = app.test_client()
    sede_id = _new_sede(client, admin)
    doctor = _token(app, _user(engine, "MEDICO"))
    assert (
        client.post("/api/v1/admin/sedes", json={"nombre": "x"}, headers=doctor).status_code == 403
    )
    assert (
        client.patch(
            f"/api/v1/admin/sedes/{sede_id}", json={"activo": False}, headers=doctor
        ).status_code
        == 403
    )
    assert _sede_row(engine, sede_id).activo == 1


def test_a_refused_write_changes_nothing_in_the_database(app: Flask, engine: Engine, admin) -> None:
    client = app.test_client()
    sede_id = _new_sede(client, admin)
    before = _sede_row(engine, sede_id)
    denied = client.patch(
        f"/api/v1/admin/sedes/{sede_id}",
        json={"nombre": "Cambiado", "activo": "false"},
        headers=admin,
    )
    assert denied.status_code == 422
    assert _sede_row(engine, sede_id) == before


# ---------- mass assignment and type coercion ----------


def test_zona_horaria_cannot_be_chosen_by_the_client(app: Flask, engine: Engine, admin) -> None:
    client = app.test_client()
    response = client.post(
        "/api/v1/admin/sedes", json={"nombre": "Sede Z", "zona_horaria": "UTC"}, headers=admin
    )
    assert response.status_code == 422
    assert response.get_json()  # a problem document, not a crash


def test_client_cannot_set_the_id_of_a_new_sede(app: Flask, engine: Engine, admin) -> None:
    client = app.test_client()
    chosen = str(uuid.uuid4())
    response = client.post(
        "/api/v1/admin/sedes", json={"nombre": "Sede ID", "id": chosen}, headers=admin
    )
    assert response.status_code == 422


def test_string_booleans_are_refused_and_not_stored(app: Flask, engine: Engine, admin) -> None:
    client = app.test_client()
    sede_id = _new_sede(client, admin)
    for value in ["false", 0, "yes"]:
        response = client.patch(
            f"/api/v1/admin/sedes/{sede_id}", json={"activo": value}, headers=admin
        )
        assert response.status_code == 422, value
    assert _sede_row(engine, sede_id).activo == 1


def test_malformed_and_non_object_bodies_are_problems_not_crashes(app: Flask, admin) -> None:
    client = app.test_client()
    bad_bodies = [b"{not json", b"[]", b'"text"', b"null", b""]
    for body in bad_bodies:
        response = client.post(
            "/api/v1/admin/sedes", data=body, headers={**admin, "Content-Type": "application/json"}
        )
        assert response.status_code == 422, body


def test_oversized_body_is_rejected_before_it_is_processed(app: Flask, admin) -> None:
    client = app.test_client()
    huge = b'{"nombre": "' + b"a" * (13 * 1024 * 1024) + b'"}'
    response = client.post(
        "/api/v1/admin/sedes", data=huge, headers={**admin, "Content-Type": "application/json"}
    )
    assert response.status_code == 413


# ---------- injection and hostile text, stored as data ----------


def test_sql_injection_text_is_stored_literally_and_the_table_survives(
    app: Flask, engine: Engine, admin
) -> None:
    client = app.test_client()
    created = client.post("/api/v1/admin/sedes", json={"nombre": SQL_INJECTION}, headers=admin)
    assert created.status_code == 201
    assert created.get_json()["nombre"] == SQL_INJECTION
    assert client.get("/api/v1/admin/sedes", headers=admin).status_code == 200


def test_control_characters_never_reach_the_database(app: Flask, engine: Engine, admin) -> None:
    client = app.test_client()
    for nombre in ["Sede\x00", "Sede\nX", "Sede\x1b[2J"]:
        response = client.post("/api/v1/admin/sedes", json={"nombre": nombre}, headers=admin)
        assert response.status_code == 422, nombre


def test_forty_emoji_consultorio_name_fits_and_forty_one_is_refused(
    app: Flask, engine: Engine, admin
) -> None:
    client = app.test_client()
    sede_id = _new_sede(client, admin)
    fits = client.post(
        f"/api/v1/admin/sedes/{sede_id}/consultorios",
        json={"nombre": "\U0001f600" * 40},
        headers=admin,
    )
    assert fits.status_code == 201, fits.get_json()
    too_big = client.post(
        f"/api/v1/admin/sedes/{sede_id}/consultorios",
        json={"nombre": "\U0001f600" * 41},
        headers=admin,
    )
    assert too_big.status_code == 422


# ---------- missing and malformed targets ----------


def test_unknown_or_malformed_ids_are_not_found_not_server_errors(app: Flask, admin) -> None:
    client = app.test_client()
    missing = str(uuid.uuid4())
    assert (
        client.patch(
            f"/api/v1/admin/sedes/{missing}", json={"activo": False}, headers=admin
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/admin/sedes/{missing}/consultorios", json={"nombre": "x"}, headers=admin
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/admin/consultorios/{missing}", json={"activo": False}, headers=admin
        ).status_code
        == 404
    )
    assert (
        client.patch(
            "/api/v1/admin/sedes/not-a-uuid", json={"activo": False}, headers=admin
        ).status_code
        == 404
    )


# ---------- overload ----------


def test_one_hundred_and_fifty_consultorios_list_and_guard_without_error(
    app: Flask, engine: Engine, admin
) -> None:
    client = app.test_client()
    sede_id = _new_sede(client, admin)
    for index in range(150):
        response = client.post(
            f"/api/v1/admin/sedes/{sede_id}/consultorios",
            json={"nombre": f"C{index:03d}"},
            headers=admin,
        )
        assert response.status_code == 201, response.get_json()
    listed = client.get("/api/v1/admin/sedes", headers=admin).get_json()["items"]
    mine = next(s for s in listed if s["id"] == sede_id)
    assert len(mine["consultorios"]) == 150
    deactivated = client.patch(
        f"/api/v1/admin/sedes/{sede_id}", json={"activo": False}, headers=admin
    )
    assert deactivated.status_code == 200


# ---------- races ----------


def test_concurrent_duplicate_consultorio_names_create_exactly_one(
    app: Flask, engine: Engine, admin
) -> None:
    sede_id = _new_sede(app.test_client(), admin)

    def attempt(_: int) -> int:
        return (
            app.test_client()
            .post(
                f"/api/v1/admin/sedes/{sede_id}/consultorios",
                json={"nombre": "Carrera"},
                headers=admin,
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(16)))
    assert statuses.count(201) == 1, statuses
    assert set(statuses) <= {201, 409}, statuses


# ---------- future bookings: the guard at the HTTP boundary ----------


def test_a_booking_on_a_different_sede_does_not_block_this_one(
    app: Flask, engine: Engine, admin
) -> None:
    client = app.test_client()
    _book(engine, _slot(engine, days_ahead=4), "CONFIRMADA")
    free_sede = _new_sede(client, admin)
    response = client.patch(
        f"/api/v1/admin/sedes/{free_sede}", json={"activo": False}, headers=admin
    )
    assert response.status_code == 200, response.get_json()


def test_a_pending_payment_booking_also_blocks_deactivation(
    app: Flask, engine: Engine, admin
) -> None:
    client = app.test_client()
    slot_id = _slot(engine, days_ahead=5)
    _book(engine, slot_id, "PENDIENTE_PAGO")
    response = client.patch(
        f"/api/v1/admin/sedes/{_sede_of_slot(engine, slot_id)}",
        json={"activo": False},
        headers=admin,
    )
    assert response.status_code == 409
