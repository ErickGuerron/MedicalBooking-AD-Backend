"""Appointment listing checks against real SQL Server: ownership, estado filter, paging.

Each test builds its own clinician, schedule, patients and appointments with unique
identifiers, so tests do not depend on order.
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
from medicalbooking.modules.identity.application.ports import NewUser
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.modules.identity.infrastructure.security import Argon2PasswordHasher
from tests.integration.clinic_time import afternoon

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PASSWORD = "synthetic-pass-123"
PATH = "/api/v1/appointments"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _user(engine: Engine, role: str) -> str:
    user_id = str(uuid.uuid4())
    SqlUserDirectory(engine).create_user(
        NewUser(
            user_id=user_id,
            email=f"{role.lower()}-{user_id}@synthetic.test",
            first_name="Sintetico",
            last_name=role.title(),
            password_hash=Argon2PasswordHasher().hash(PASSWORD),
            roles=(role,),
        )
    )
    return user_id


def _headers(app: Flask, user_id: str) -> dict[str, str]:
    engine = app.extensions["medicalbooking.engine"]
    with engine.connect() as conn:
        email = conn.execute(
            text("SELECT email FROM usuarios WHERE id = :u"), {"u": user_id}
        ).scalar_one()
    response = app.test_client().post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def _agenda(engine: Engine) -> tuple[str, str]:
    """Create a clinician with one agenda and one free slot; return (doctor user, slot id)."""
    doctor = _user(engine, "MEDICO")
    start = afternoon(datetime.now(UTC) + timedelta(days=4)).replace(
        second=0, microsecond=0, tzinfo=None
    )
    with engine.begin() as conn:
        medico_id = conn.execute(
            text(
                "INSERT INTO medicos (id, usuario_id, registro_profesional) "
                "OUTPUT inserted.id VALUES (NEWID(), :u, :reg)"
            ),
            {"u": doctor, "reg": f"REG-{doctor}"},
        ).scalar_one()
        sede_id = conn.execute(
            text(
                "INSERT INTO sedes (id, nombre) OUTPUT inserted.id VALUES (NEWID(), N'Sede lista')"
            )
        ).scalar_one()
        consultorio_id = conn.execute(
            text(
                "INSERT INTO consultorios (id, sede_id, nombre) OUTPUT inserted.id "
                "VALUES (NEWID(), :s, N'Consultorio lista')"
            ),
            {"s": sede_id},
        ).scalar_one()
        agenda_id = conn.execute(
            text(
                "INSERT INTO agendas (id, medico_id, consultorio_id, fecha_inicio) "
                "OUTPUT inserted.id VALUES (NEWID(), :m, :c, CAST(:d AS date))"
            ),
            {"m": medico_id, "c": consultorio_id, "d": start.date()},
        ).scalar_one()
        slot_id = conn.execute(
            text(
                "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc) "
                "OUTPUT inserted.id VALUES (NEWID(), :a, :i, DATEADD(minute, 30, :i))"
            ),
            {"a": agenda_id, "i": start},
        ).scalar_one()
    return doctor, str(uuid.UUID(str(slot_id)))


def _service(engine: Engine) -> str:
    with engine.begin() as conn:
        service_id = conn.execute(
            text(
                "INSERT INTO servicios_medicos (id, nombre, duracion_min, precio_base) "
                "OUTPUT inserted.id VALUES (NEWID(), N'Consulta lista', 30, 25.00)"
            )
        ).scalar_one()
    return str(uuid.UUID(str(service_id)))


def _book(app: Flask, headers: dict[str, str], slot: str, servicio: str) -> str:
    response = app.test_client().post(
        PATH,
        json={"horario_id": slot, "servicio_id": servicio},
        headers={**headers, "Idempotency-Key": f"key-{uuid.uuid4()}"},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def test_patient_sees_own_appointments_and_never_another_patients(app, engine) -> None:
    patient_a, patient_b = _user(engine, "PACIENTE"), _user(engine, "PACIENTE")
    _, slot_a = _agenda(engine)
    _, slot_b = _agenda(engine)
    servicio = _service(engine)
    mine = _book(app, _headers(app, patient_a), slot_a, servicio)
    theirs = _book(app, _headers(app, patient_b), slot_b, servicio)

    response = app.test_client().get(PATH, headers=_headers(app, patient_a))

    assert response.status_code == 200
    ids = [item["id"] for item in response.get_json()["items"]]
    assert mine in ids
    assert theirs not in ids


def test_patient_rows_carry_id_estado_utc_times_and_the_doctor(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    doctor, slot = _agenda(engine)
    cita = _book(app, _headers(app, patient), slot, _service(engine))

    items = app.test_client().get(PATH, headers=_headers(app, patient)).get_json()["items"]
    item = next(entry for entry in items if entry["id"] == cita)

    assert set(item) == {"id", "estado", "inicio", "fin", "medico"}
    assert set(item["medico"]) <= {"id", "nombre", "registro_profesional", "telefono"}
    assert item["medico"]["registro_profesional"] == f"REG-{doctor}"
    assert "paciente" not in item
    assert item["estado"] == "PENDIENTE_PAGO"
    assert item["inicio"].endswith("+00:00")
    assert item["fin"].endswith("+00:00")


def test_doctor_sees_appointments_of_his_or_her_agenda_only(app, engine) -> None:
    owner, slot = _agenda(engine)
    other_doctor, _ = _agenda(engine)
    patient = _user(engine, "PACIENTE")
    cita = _book(app, _headers(app, patient), slot, _service(engine))

    owner_response = app.test_client().get(PATH, headers=_headers(app, owner))
    other_response = app.test_client().get(PATH, headers=_headers(app, other_doctor))

    assert owner_response.status_code == 200
    assert cita in [item["id"] for item in owner_response.get_json()["items"]]
    assert other_response.status_code == 200
    assert cita not in [item["id"] for item in other_response.get_json()["items"]]


def test_doctor_listing_never_exposes_patient_contact_data(app, engine) -> None:
    doctor, slot = _agenda(engine)
    patient = _user(engine, "PACIENTE")
    _book(app, _headers(app, patient), slot, _service(engine))

    response = app.test_client().get(PATH, headers=_headers(app, doctor))

    assert response.status_code == 200
    assert "@synthetic.test" not in response.get_data(as_text=True)


def test_estado_filter_returns_only_that_state(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    headers = _headers(app, patient)
    servicio = _service(engine)
    _, slot_kept = _agenda(engine)
    _, slot_cancelled = _agenda(engine)
    kept = _book(app, headers, slot_kept, servicio)
    cancelled = _book(app, headers, slot_cancelled, servicio)
    app.test_client().post(f"{PATH}/{cancelled}/cancel", headers=headers)

    response = app.test_client().get(PATH, query_string={"estado": "CANCELADA"}, headers=headers)

    ids = [item["id"] for item in response.get_json()["items"]]
    assert cancelled in ids
    assert kept not in ids
    assert all(item["estado"] == "CANCELADA" for item in response.get_json()["items"])


def test_laboratory_user_gets_403(app, engine) -> None:
    lab = _user(engine, "LABORATORISTA")

    response = app.test_client().get(PATH, headers=_headers(app, lab))

    assert response.status_code == 403


@pytest.mark.parametrize(
    "query",
    [{"estado": "BORRADOR"}, {"limit": 0}, {"limit": 201}, {"offset": -1}, {"offset": "x"}],
)
def test_invalid_filters_and_paging_answer_422(app, engine, query) -> None:
    patient = _user(engine, "PACIENTE")

    response = app.test_client().get(PATH, query_string=query, headers=_headers(app, patient))

    assert response.status_code == 422


def test_listing_requires_authentication(app) -> None:
    assert app.test_client().get(PATH).status_code == 401


def _set_phone(engine: Engine, user_id: str, phone: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE usuarios SET telefono = :t WHERE id = :u"), {"t": phone, "u": user_id}
        )


def _audit_rows(engine: Engine, user_id: str) -> list[tuple]:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT entidad, accion, resultado, entidad_id, paciente_id "
                "FROM auditoria_accesos WHERE usuario_id = :u "
                "AND accion = 'listar_revision_cancelacion'"
            ),
            {"u": user_id},
        ).all()


def test_patient_sees_the_doctor_phone_and_registration_of_each_row(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    doctor, slot = _agenda(engine)
    _set_phone(engine, doctor, "0999000111")
    cita = _book(app, _headers(app, patient), slot, _service(engine))

    items = app.test_client().get(PATH, headers=_headers(app, patient)).get_json()["items"]
    item = next(entry for entry in items if entry["id"] == cita)

    assert item["medico"]["telefono"] == "0999000111"
    assert item["medico"]["registro_profesional"] == f"REG-{doctor}"


def test_admin_candidates_carry_doctor_identity_and_no_patient_or_phone(app, engine) -> None:
    admin = _user(engine, "ADMINISTRADOR")
    patient = _user(engine, "PACIENTE")
    doctor, slot = _agenda(engine)
    _set_phone(engine, doctor, "0988777666")
    cita = _book(app, _headers(app, patient), slot, _service(engine))

    response = app.test_client().get(
        "/api/v1/appointments/cancellation-approvals",
        query_string={"medico_registro": f"REG-{doctor}"},
        headers=_headers(app, admin),
    )

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert [item["id"] for item in items] == [cita]
    assert set(items[0]) == {"id", "estado", "inicio", "fin", "tiene_aprobacion", "medico"}
    assert set(items[0]["medico"]) == {"id", "nombre", "registro_profesional"}
    body = response.get_data(as_text=True)
    assert "0988777666" not in body
    assert "@synthetic.test" not in body


def test_admin_general_listing_is_refused(app, engine) -> None:
    admin = _user(engine, "ADMINISTRADOR")

    assert app.test_client().get(PATH, headers=_headers(app, admin)).status_code == 403


def test_candidates_reads_are_audited_ok_and_denials_as_denied(app, engine) -> None:
    admin = _user(engine, "ADMINISTRADOR")
    patient = _user(engine, "PACIENTE")

    ok = app.test_client().get(
        "/api/v1/appointments/cancellation-approvals", headers=_headers(app, admin)
    )
    denied = app.test_client().get(
        "/api/v1/appointments/cancellation-approvals", headers=_headers(app, patient)
    )

    assert ok.status_code == 200
    assert denied.status_code == 403
    assert _audit_rows(engine, admin) == [("cita", "listar_revision_cancelacion", "OK", None, None)]
    assert _audit_rows(engine, patient) == [
        ("cita", "listar_revision_cancelacion", "DENEGADO", None, None)
    ]
