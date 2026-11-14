"""Treatments API (S4-S6) against real SQL Server: create, list, update, follow-ups, access.

Each test books its own appointment through the API, so counts never depend on other tests.
The append-only trigger is asserted through SQL, not through the API.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_doctor_cancellation import _doctor_of
from tests.integration.test_lab_api import _doctor
from tests.integration.test_scheduling_api import _book, _count, _service, _slot, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
FORBIDDEN = "Access to this resource is not allowed"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _cita(app: Flask, engine: Engine) -> tuple[str, str, str]:
    """A booked appointment. Returns (patient user, treating doctor user, cita id)."""
    patient = _user(engine, "PACIENTE")
    response = _book(
        app.test_client(),
        _token(app, patient),
        key=f"treatments-{uuid.uuid4()}",
        slot=_slot(engine, days_ahead=4),
        servicio=_service(engine),
    )
    assert response.status_code == 201, response.get_json()
    cita = response.get_json()["id"]
    return patient, _doctor_of(engine, cita), cita


def _create(client, headers: dict, cita: str, **overrides):
    body = {
        "descripcion": "Fisioterapia lumbar (sintetico)",
        "fecha_inicio": "2026-11-01",
        **overrides,
    }
    return client.post(f"/api/v1/citas/{cita}/tratamientos", json=body, headers=headers)


def _note(client, headers: dict, tratamiento: str, nota: str):
    return client.post(
        f"/api/v1/tratamientos/{tratamiento}/seguimientos", json={"nota": nota}, headers=headers
    )


def _treatment_row(engine: Engine, tratamiento: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT descripcion, estado, fecha_fin FROM tratamientos WHERE id = :t"),
            {"t": tratamiento},
        ).one()


def _request(client, method: str, path: str, headers: dict, body: dict | None = None):
    return client.open(path, method=method, json=body, headers=headers)


def _without_correlation(response) -> dict:
    """Error body minus the per-request correlation id, which differs on every call."""
    return {key: value for key, value in response.get_json().items() if key != "correlation_id"}


# ---------- create and list (S4, S6) ----------


def test_doctor_creates_a_treatment_that_the_doctor_and_the_patient_can_list(app, engine) -> None:
    patient, doctor, cita = _cita(app, engine)
    client = app.test_client()

    created = _create(client, _token(app, doctor), cita)
    doctor_list = client.get(f"/api/v1/citas/{cita}/tratamientos", headers=_token(app, doctor))
    patient_list = client.get(f"/api/v1/citas/{cita}/tratamientos", headers=_token(app, patient))

    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    assert body["cita_id"] == cita
    assert body["estado"] == "ACTIVO"
    assert body["fecha_inicio"] == "2026-11-01"
    assert body["fecha_fin"] is None
    assert doctor_list.status_code == 200
    assert patient_list.status_code == 200
    assert [item["id"] for item in doctor_list.get_json()["items"]] == [body["id"]]
    assert [item["id"] for item in patient_list.get_json()["items"]] == [body["id"]]


# ---------- update and state rules (S4) ----------


def test_doctor_patches_description_and_state_and_finalizing_needs_an_end_date(app, engine) -> None:
    _, doctor, cita = _cita(app, engine)
    client = app.test_client()
    headers = _token(app, doctor)
    treatment = _create(client, headers, cita).get_json()["id"]
    url = f"/api/v1/tratamientos/{treatment}"

    described = client.patch(
        url, json={"descripcion": "Control a las dos semanas (sintetico)"}, headers=headers
    )
    no_end_date = client.patch(url, json={"estado": "FINALIZADO"}, headers=headers)
    finished = client.patch(
        url, json={"estado": "FINALIZADO", "fecha_fin": "2026-11-10"}, headers=headers
    )
    reopened = client.patch(url, json={"estado": "ACTIVO"}, headers=headers)

    assert described.status_code == 200
    assert described.get_json()["descripcion"] == "Control a las dos semanas (sintetico)"
    assert no_end_date.status_code == 422
    assert finished.status_code == 200
    assert finished.get_json()["estado"] == "FINALIZADO"
    assert finished.get_json()["fecha_fin"] == "2026-11-10"
    assert reopened.status_code == 422
    row = _treatment_row(engine, treatment)
    assert (row.descripcion, row.estado) == ("Control a las dos semanas (sintetico)", "FINALIZADO")
    assert row.fecha_fin is not None


def test_rejected_patch_leaves_the_stored_treatment_unchanged(app, engine) -> None:
    _, doctor, cita = _cita(app, engine)
    client = app.test_client()
    headers = _token(app, doctor)
    treatment = _create(client, headers, cita).get_json()["id"]

    response = client.patch(
        f"/api/v1/tratamientos/{treatment}", json={"estado": "FINALIZADO"}, headers=headers
    )

    assert response.status_code == 422
    row = _treatment_row(engine, treatment)
    assert (row.descripcion, row.estado, row.fecha_fin) == (
        "Fisioterapia lumbar (sintetico)",
        "ACTIVO",
        None,
    )


# ---------- follow-up notes (S5) ----------


def test_follow_up_notes_are_listed_in_insertion_order_for_doctor_and_patient(app, engine) -> None:
    patient, doctor, cita = _cita(app, engine)
    client = app.test_client()
    doctor_headers = _token(app, doctor)
    treatment = _create(client, doctor_headers, cita).get_json()["id"]
    notas = [
        "Primera evolucion (sintetica)",
        "Segunda evolucion (sintetica)",
        "Tercera evolucion (sintetica)",
    ]
    for nota in notas:
        assert _note(client, doctor_headers, treatment, nota).status_code == 201

    doctor_read = client.get(
        f"/api/v1/tratamientos/{treatment}/seguimientos", headers=doctor_headers
    )
    patient_read = client.get(
        f"/api/v1/tratamientos/{treatment}/seguimientos", headers=_token(app, patient)
    )

    assert doctor_read.status_code == 200
    assert [item["nota"] for item in doctor_read.get_json()["items"]] == notas
    assert [item["nota"] for item in patient_read.get_json()["items"]] == notas


# ---------- append-only trigger (S5), asserted through SQL ----------


def test_database_trigger_rejects_update_and_delete_of_a_follow_up_note(app, engine) -> None:
    _, doctor, cita = _cita(app, engine)
    client = app.test_client()
    headers = _token(app, doctor)
    treatment = _create(client, headers, cita).get_json()["id"]
    followup = _note(client, headers, treatment, "Nota original (sintetica)").get_json()["id"]

    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE tratamientos_seguimientos SET nota = N'alterada' WHERE id = :id"),
                {"id": followup},
            )
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tratamientos_seguimientos WHERE id = :id"), {"id": followup}
            )

    assert (
        _count(engine, "SELECT COUNT(*) FROM tratamientos_seguimientos WHERE id = :id", id=followup)
        == 1
    )
    with engine.connect() as conn:
        nota = conn.execute(
            text("SELECT nota FROM tratamientos_seguimientos WHERE id = :id"), {"id": followup}
        ).scalar_one()
    assert nota == "Nota original (sintetica)"


# ---------- access control (S6) ----------

# (method, path template, body). `{cita}` and `{tratamiento}` take the real or a missing id.
ROUTES = [
    ("GET", "/api/v1/citas/{cita}/tratamientos", None),
    (
        "POST",
        "/api/v1/citas/{cita}/tratamientos",
        {"descripcion": "Ajena", "fecha_inicio": "2026-11-01"},
    ),
    ("PATCH", "/api/v1/tratamientos/{tratamiento}", {"estado": "SUSPENDIDO"}),
    ("POST", "/api/v1/tratamientos/{tratamiento}/seguimientos", {"nota": "Ajena (sintetica)"}),
    ("GET", "/api/v1/tratamientos/{tratamiento}/seguimientos", None),
]


def _fill(path: str, cita: str, tratamiento: str) -> str:
    return path.format(cita=cita, tratamiento=tratamiento)


@pytest.mark.parametrize("actor", ["foreign_doctor", "foreign_patient"])
def test_foreign_actor_is_denied_and_missing_ids_answer_the_same_403(app, engine, actor) -> None:
    _, doctor, cita = _cita(app, engine)
    client = app.test_client()
    owner_headers = _token(app, doctor)
    treatment = _create(client, owner_headers, cita).get_json()["id"]
    _note(client, owner_headers, treatment, "Nota del medico (sintetica)")
    if actor == "foreign_doctor":
        headers = _token(app, _doctor(engine))
    else:
        headers = _token(app, _user(engine, "PACIENTE"))
    missing_cita, missing_treatment = str(uuid.uuid4()), str(uuid.uuid4())
    tratamientos_before = _count(
        engine, "SELECT COUNT(*) FROM tratamientos WHERE cita_id = :c", c=cita
    )
    notes_before = _count(
        engine,
        "SELECT COUNT(*) FROM tratamientos_seguimientos WHERE tratamiento_id = :t",
        t=treatment,
    )

    for method, template, body in ROUTES:
        foreign = _request(client, method, _fill(template, cita, treatment), headers, body)
        missing = _request(
            client, method, _fill(template, missing_cita, missing_treatment), headers, body
        )
        assert foreign.status_code == 403, (method, template, foreign.get_json())
        assert missing.status_code == 403, (method, template, missing.get_json())
        assert _without_correlation(foreign) == _without_correlation(missing)
        assert FORBIDDEN in str(foreign.get_json())

    assert (
        _count(engine, "SELECT COUNT(*) FROM tratamientos WHERE cita_id = :c", c=cita)
        == tratamientos_before
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM tratamientos_seguimientos WHERE tratamiento_id = :t",
            t=treatment,
        )
        == notes_before
    )


def test_owning_patient_reads_but_cannot_write(app, engine) -> None:
    patient, doctor, cita = _cita(app, engine)
    client = app.test_client()
    treatment = _create(client, _token(app, doctor), cita).get_json()["id"]
    headers = _token(app, patient)

    create = _request(
        client,
        "POST",
        f"/api/v1/citas/{cita}/tratamientos",
        headers,
        {"descripcion": "Intento del paciente", "fecha_inicio": "2026-11-01"},
    )
    patch = _request(
        client, "PATCH", f"/api/v1/tratamientos/{treatment}", headers, {"estado": "SUSPENDIDO"}
    )
    note = _note(client, headers, treatment, "Intento del paciente (sintetico)")
    list_treatments = client.get(f"/api/v1/citas/{cita}/tratamientos", headers=headers)
    list_notes = client.get(f"/api/v1/tratamientos/{treatment}/seguimientos", headers=headers)

    assert (create.status_code, patch.status_code, note.status_code) == (403, 403, 403)
    assert list_treatments.status_code == 200
    assert list_notes.status_code == 200
    assert _treatment_row(engine, treatment).estado == "ACTIVO"


# ---------- authentication ----------


def test_requests_without_a_bearer_token_are_rejected_with_401(app, engine) -> None:
    _, _, cita = _cita(app, engine)
    client = app.test_client()
    tratamientos_before = _count(
        engine, "SELECT COUNT(*) FROM tratamientos WHERE cita_id = :c", c=cita
    )

    unauthenticated_create = client.post(
        f"/api/v1/citas/{cita}/tratamientos",
        json={"descripcion": "Sin token", "fecha_inicio": "2026-11-01"},
    )
    unauthenticated_list = client.get(f"/api/v1/citas/{cita}/tratamientos")
    unauthenticated_notes = client.get(f"/api/v1/tratamientos/{uuid.uuid4()}/seguimientos")

    assert unauthenticated_create.status_code == 401
    assert unauthenticated_list.status_code == 401
    assert unauthenticated_notes.status_code == 401
    assert (
        _count(engine, "SELECT COUNT(*) FROM tratamientos WHERE cita_id = :c", c=cita)
        == tratamientos_before
    )


# Goal 7 (migration 0029 downgrade) is not covered here: the repo has no integration downgrade
# pattern. tests/unit/test_treatments_migration.py checks the downgrade script statically.
