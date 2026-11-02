"""Hold expiration (E4) against real SQL Server.

The sweep runs with a clock one hour ahead, so every hold created by the suite is overdue.
Sweeps are shared across tests in the same database, so each test asserts only on the
appointments it created and never on global counts that other tests could change.
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
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.infrastructure.repositories import SqlSchedulingStore
from tests.integration.test_scheduling_api import (
    _book,
    _count,
    _service,
    _slot,
    _token,
    _user,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


def _later() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _sweeper(engine: Engine, *, batch_size: int = 100) -> SchedulingService:
    return SchedulingService(
        store=SqlSchedulingStore(engine),
        clock=_later,
        hold_minutes=15,
        expiry_batch_size=batch_size,
    )


def _estado(engine: Engine, cita: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT estado FROM citas WHERE id = :c"), {"c": cita}
        ).scalar_one()


def test_expired_hold_is_released_and_recorded(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    headers = _token(app, patient)
    cita = _book(
        app.test_client(), headers, key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio
    )
    cita_id = cita.get_json()["id"]

    _sweeper(engine).expire_overdue_holds()

    assert _estado(engine, cita_id) == "EXPIRADA"
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM horarios_disponibles WHERE id = :h AND estado = 'DISPONIBLE'",
            h=slot,
        )
        == 1
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM historial_estados_cita WHERE cita_id = :c "
            "AND estado_anterior = 'PENDIENTE_PAGO' AND estado_nuevo = 'EXPIRADA' "
            "AND usuario_id IS NULL",
            c=cita_id,
        )
        == 1
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM eventos_outbox WHERE agregado_id = :c "
            "AND tipo_evento = 'cita.expirada' AND version_agregado = 2",
            c=cita_id,
        )
        == 1
    )


def test_confirmed_appointments_are_never_expired(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    cita_id = _book(
        app.test_client(),
        _token(app, patient),
        key=f"key-{uuid.uuid4()}",
        slot=slot,
        servicio=servicio,
    ).get_json()["id"]
    with engine.begin() as conn:
        conn.execute(text("UPDATE citas SET estado = 'CONFIRMADA' WHERE id = :c"), {"c": cita_id})

    _sweeper(engine).expire_overdue_holds()

    assert _estado(engine, cita_id) == "CONFIRMADA"


def test_concurrent_sweeps_expire_each_hold_exactly_once(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    headers, client = _token(app, patient), app.test_client()
    servicio = _service(engine)
    citas = []
    for _ in range(6):
        slot = _slot(engine)
        citas.append(
            _book(
                client, headers, key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio
            ).get_json()["id"]
        )

    # Four workers race with small batches; READPAST must keep them from claiming the same hold.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: _sweeper(engine, batch_size=2).expire_overdue_holds(), range(4)))

    for cita_id in citas:
        assert _estado(engine, cita_id) == "EXPIRADA"
        assert (
            _count(
                engine,
                "SELECT COUNT(*) FROM eventos_outbox "
                "WHERE agregado_id = :c AND tipo_evento = 'cita.expirada'",
                c=cita_id,
            )
            == 1
        )


def test_repeated_key_replays_the_expired_appointment(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    headers, client = _token(app, patient), app.test_client()
    key = f"key-{uuid.uuid4()}"
    first = _book(client, headers, key=key, slot=slot, servicio=servicio)
    _sweeper(engine).expire_overdue_holds()

    replay = _book(client, headers, key=key, slot=slot, servicio=servicio)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.get_json()["id"] == first.get_json()["id"]
    assert replay.get_json()["estado"] == "EXPIRADA"
    assert _count(engine, "SELECT COUNT(*) FROM citas WHERE horario_id = :h", h=slot) == 1


def test_expired_appointment_cannot_be_cancelled(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    headers, client = _token(app, patient), app.test_client()
    cita_id = _book(
        client, headers, key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio
    ).get_json()["id"]
    _sweeper(engine).expire_overdue_holds()

    response = client.post(f"/api/v1/appointments/{cita_id}/cancel", headers=headers)

    assert response.status_code == 409
    assert _estado(engine, cita_id) == "EXPIRADA"


def test_sweep_skips_a_hold_locked_by_another_transaction(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    cita_id = _book(
        app.test_client(),
        _token(app, patient),
        key=f"key-{uuid.uuid4()}",
        slot=slot,
        servicio=servicio,
    ).get_json()["id"]
    sweeper = _sweeper(engine)

    with engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT id FROM citas WITH (UPDLOCK, ROWLOCK) WHERE id = :c"), {"c": cita_id}
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            # READPAST: the sweep must finish instead of waiting on the locked hold.
            pool.submit(sweeper.expire_overdue_holds).result(timeout=15)
        transaction.rollback()

    assert _estado(engine, cita_id) == "PENDIENTE_PAGO"
    sweeper.expire_overdue_holds()
    assert _estado(engine, cita_id) == "EXPIRADA"


def test_slot_of_an_expired_hold_can_be_booked_by_someone_else(app, engine) -> None:
    first_patient, second_patient = _user(engine, "PACIENTE"), _user(engine, "PACIENTE")
    slot, servicio = _slot(engine), _service(engine)
    client = app.test_client()
    _book(
        client, _token(app, first_patient), key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio
    )
    _sweeper(engine).expire_overdue_holds()

    rebooked = _book(
        client, _token(app, second_patient), key=f"key-{uuid.uuid4()}", slot=slot, servicio=servicio
    )

    assert rebooked.status_code == 201
