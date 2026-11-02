"""Scheduling checks against real SQL Server: availability, booking, idempotency, concurrency.

Each test builds its own clinician, schedule slot and patients with unique identifiers,
so tests do not depend on order.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PASSWORD = "synthetic-pass-123"


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


def _token(app: Flask, user_id: str) -> dict[str, str]:
    with app.extensions["medicalbooking.engine"].connect() as conn:
        email = conn.execute(
            text("SELECT email FROM usuarios WHERE id = :u"), {"u": user_id}
        ).scalar_one()
    response = app.test_client().post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def _slot(engine: Engine, *, days_ahead: int = 2) -> str:
    """Create a clinician, a schedule and one free future slot; return the slot id."""
    doctor = _user(engine, "MEDICO")
    start = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=days_ahead)
    start = start.replace(tzinfo=None)
    with engine.begin() as conn:
        medico_id = conn.execute(
            text(
                "INSERT INTO medicos (id, usuario_id, registro_profesional) "
                "OUTPUT inserted.id VALUES (NEWID(), :u, :reg)"
            ),
            {"u": doctor, "reg": f"REG-{doctor}"},
        ).scalar_one()
        sede_id = conn.execute(
            text("INSERT INTO sedes (id, nombre) OUTPUT inserted.id VALUES (NEWID(), N'Sede test')")
        ).scalar_one()
        consultorio_id = conn.execute(
            text(
                "INSERT INTO consultorios (id, sede_id, nombre) OUTPUT inserted.id "
                "VALUES (NEWID(), :s, N'Consultorio test')"
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
    return str(uuid.UUID(str(slot_id)))  # the API answers in lower case


def _service(engine: Engine) -> str:
    with engine.begin() as conn:
        service_id = conn.execute(
            text(
                "INSERT INTO servicios_medicos (id, nombre, duracion_min, precio_base) "
                "OUTPUT inserted.id VALUES (NEWID(), N'Consulta test', 30, 25.00)"
            )
        ).scalar_one()
    return str(uuid.UUID(str(service_id)))


def _count(engine: Engine, sql: str, **params) -> int:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def _book(client, headers: dict[str, str], *, key: str, slot: str, servicio: str):
    return client.post(
        "/api/v1/appointments",
        json={"horario_id": slot, "servicio_id": servicio},
        headers={**headers, "Idempotency-Key": key},
    )


# ---------- availability ----------


def test_availability_lists_free_slots_in_the_window(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot = _slot(engine, days_ahead=3)
    start = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    end = (datetime.now(UTC) + timedelta(days=5)).isoformat()

    response = app.test_client().get(
        "/api/v1/availability",
        query_string={"desde": start, "hasta": end},
        headers=_token(app, patient),
    )

    assert response.status_code == 200
    assert slot in [item["horario_id"] for item in response.get_json()["items"]]


def test_availability_requires_authentication(app) -> None:
    response = app.test_client().get("/api/v1/availability")

    assert response.status_code == 401


# ---------- booking and idempotency ----------


def test_booking_blocks_the_slot_and_records_history_and_outbox(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    key = f"key-{uuid.uuid4()}"

    response = _book(app.test_client(), _token(app, patient), key=key, slot=slot, servicio=servicio)

    assert response.status_code == 201
    body = response.get_json()
    assert body["estado"] == "PENDIENTE_PAGO"
    cita = body["id"]
    assert (
        _count(engine, "SELECT COUNT(*) FROM historial_estados_cita WHERE cita_id = :c", c=cita)
        == 1
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM eventos_outbox "
            "WHERE agregado_id = :c AND tipo_evento = 'cita.reservada'",
            c=cita,
        )
        == 1
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM horarios_disponibles WHERE id = :h AND estado = 'RESERVADO'",
            h=slot,
        )
        == 1
    )


def test_repeated_key_with_same_request_returns_the_same_appointment(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    headers, client = _token(app, patient), app.test_client()
    key = f"key-{uuid.uuid4()}"

    first = _book(client, headers, key=key, slot=slot, servicio=servicio)
    second = _book(client, headers, key=key, slot=slot, servicio=servicio)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["id"] == first.get_json()["id"]
    assert _count(engine, "SELECT COUNT(*) FROM citas WHERE horario_id = :h", h=slot) == 1


def test_reused_key_with_a_different_request_is_rejected(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, other_slot, servicio = _slot(engine), _slot(engine), _service(engine)
    headers, client = _token(app, patient), app.test_client()
    key = f"key-{uuid.uuid4()}"
    _book(client, headers, key=key, slot=slot, servicio=servicio)

    response = _book(client, headers, key=key, slot=other_slot, servicio=servicio)

    assert response.status_code == 409
    assert _count(engine, "SELECT COUNT(*) FROM citas WHERE horario_id = :h", h=other_slot) == 0


def test_unknown_slot_returns_not_found(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = _book(
        app.test_client(),
        _token(app, patient),
        key=f"key-{uuid.uuid4()}",
        slot=str(uuid.uuid4()),
        servicio=_service(engine),
    )

    assert response.status_code == 404


def test_missing_idempotency_key_is_rejected(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = app.test_client().post(
        "/api/v1/appointments",
        json={"horario_id": str(uuid.uuid4()), "servicio_id": str(uuid.uuid4())},
        headers=_token(app, patient),
    )

    assert response.status_code == 422


# ---------- concurrency ----------


def test_twenty_parallel_bookings_leave_exactly_one_active_appointment(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    headers = _token(app, patient)

    def attempt(index: int) -> int:
        return _book(
            app.test_client(),
            headers,
            key=f"parallel-{index}-{uuid.uuid4()}",
            slot=slot,
            servicio=servicio,
        ).status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(attempt, range(20)))

    assert statuses.count(201) == 1, statuses
    assert statuses.count(409) == 19, statuses
    # Losing requests roll back completely: no history or outbox rows for them.
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM historial_estados_cita h JOIN citas c ON c.id = h.cita_id "
            "WHERE c.horario_id = :h",
            h=slot,
        )
        == 1
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM eventos_outbox e JOIN citas c ON c.id = e.agregado_id "
            "WHERE c.horario_id = :h",
            h=slot,
        )
        == 1
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM citas WHERE horario_id = :h "
            "AND estado IN ('PENDIENTE_PAGO','CONFIRMADA')",
            h=slot,
        )
        == 1
    )


# ---------- cancellation and ownership ----------


def test_cancelling_releases_the_slot_for_another_booking(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    headers, client = _token(app, patient), app.test_client()
    cita = _book(
        client, headers, key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio
    ).get_json()

    cancelled = client.post(f"/api/v1/appointments/{cita['id']}/cancel", headers=headers)
    rebooked = _book(client, headers, key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio)

    assert cancelled.status_code == 200
    assert cancelled.get_json()["estado"] == "CANCELADA"
    assert rebooked.status_code == 201


def test_another_patient_cannot_cancel_and_missing_looks_the_same(app, engine) -> None:
    owner, intruder = _user(engine, "PACIENTE"), _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    client = app.test_client()
    cita = _book(
        client, _token(app, owner), key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio
    ).get_json()

    forbidden = client.post(
        f"/api/v1/appointments/{cita['id']}/cancel", headers=_token(app, intruder)
    )
    missing = client.post(
        f"/api/v1/appointments/{uuid.uuid4()}/cancel", headers=_token(app, intruder)
    )

    assert forbidden.status_code == missing.status_code == 403
    assert forbidden.get_json()["detail"] == missing.get_json()["detail"]
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM citas WHERE id = :c AND estado = 'PENDIENTE_PAGO'",
            c=cita["id"],
        )
        == 1
    )


def test_cancelling_twice_conflicts(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    headers, client = _token(app, patient), app.test_client()
    cita = _book(
        client, headers, key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio
    ).get_json()
    client.post(f"/api/v1/appointments/{cita['id']}/cancel", headers=headers)

    response = client.post(f"/api/v1/appointments/{cita['id']}/cancel", headers=headers)

    assert response.status_code == 409
