"""Appointments reach the replica as one snapshot per change: cita.actualizada.

Every reservation, cancellation (patient or doctor) and expiration writes the full snapshot of
the appointment in the same SQL Server transaction as the change. The payload carries the
replica's columns and the slot window only. The booking's free text, the idempotency key and
the row version never leave SQL Server. The existing cita.reservada, cita.cancelada and
cita.expirada events keep their payloads.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.infrastructure.repositories import SqlSchedulingStore
from tests.integration.test_doctor_cancellation import (
    _booked_by_patient,
    _cancel,
    _doctor_of,
    _valid_reason,
)
from tests.integration.test_scheduling_api import _service, _slot, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
FREE_TEXT = "Dolor persistente en el pecho desde la semana pasada"
FORBIDDEN_KEYS = {"motivo", "clave_idempotencia", "version_fila", "horario_id"}
REPLICA_KEYS = {
    "id",
    "paciente_id",
    "servicio_id",
    "estado",
    "reservada_en",
    "confirmada_en",
    "cancelada_en",
    "motivo_cancelacion_codigo",
    "motivo_cancelacion_texto",
    "inicio",
    "fin",
}


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _snapshots(engine: Engine, cita_id: str) -> list[dict]:
    """The cita.actualizada payloads of one appointment, oldest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT payload FROM eventos_outbox WHERE agregado_tipo = 'cita' "
                "AND agregado_id = :c AND tipo_evento = 'cita.actualizada' "
                "ORDER BY version_agregado"
            ),
            {"c": cita_id},
        ).all()
    return [json.loads(row.payload)["cita"] for row in rows]


def _events(engine: Engine, cita_id: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT payload FROM eventos_outbox WHERE agregado_tipo = 'cita' "
                "AND agregado_id = :c ORDER BY version_agregado"
            ),
            {"c": cita_id},
        ).all()
    return [row.payload for row in rows]


def _slot_window(engine: Engine, horario_id: str) -> tuple[datetime, datetime]:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT inicio_utc, fin_utc FROM horarios_disponibles WHERE id = :h"),
            {"h": horario_id},
        ).one()
    return row.inicio_utc, row.fin_utc


def _book_with_free_text(app: Flask, engine: Engine, patient: str):
    slot, servicio = _slot(engine, days_ahead=3), _service(engine)
    response = app.test_client().post(
        "/api/v1/appointments",
        json={"horario_id": slot, "servicio_id": servicio, "motivo": FREE_TEXT},
        headers={**_token(app, patient), "Idempotency-Key": f"snap-{uuid.uuid4()}"},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"], slot


def _assert_no_private_fields(snapshot: dict) -> None:
    assert set(snapshot) == REPLICA_KEYS
    assert not (FORBIDDEN_KEYS & set(snapshot))


def test_a_reservation_emits_the_snapshot_in_the_stored_state(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    cita_id, slot = _book_with_free_text(app, engine, patient)

    snapshots = _snapshots(engine, cita_id)

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    _assert_no_private_fields(snapshot)
    assert snapshot["id"] == cita_id
    assert snapshot["estado"] == "PENDIENTE_PAGO"
    assert snapshot["cancelada_en"] is None
    assert snapshot["motivo_cancelacion_codigo"] is None
    assert snapshot["motivo_cancelacion_texto"] is None
    inicio, fin = _slot_window(engine, slot)
    assert datetime.fromisoformat(snapshot["inicio"]) == inicio
    assert datetime.fromisoformat(snapshot["fin"]) == fin


def test_the_booking_free_text_and_key_never_reach_any_outbox_payload(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    cita_id, _ = _book_with_free_text(app, engine, patient)
    _cancel(app, _token(app, _doctor_of(engine, cita_id)), cita_id, _valid_reason())

    for payload in _events(engine, cita_id):
        assert FREE_TEXT not in payload
        assert "clave_idempotencia" not in payload
        assert "version_fila" not in payload
    assert all(not (FORBIDDEN_KEYS & set(snapshot)) for snapshot in _snapshots(engine, cita_id))


def test_a_doctor_cancellation_emits_the_reason_fields(app, engine) -> None:
    _patient, doctor, cita_id = _booked_by_patient(app, engine)

    response = _cancel(app, _token(app, doctor), cita_id, _valid_reason())

    assert response.status_code == 200, response.get_json()
    snapshot = _snapshots(engine, cita_id)[-1]
    _assert_no_private_fields(snapshot)
    assert snapshot["estado"] == "CANCELADA"
    assert snapshot["cancelada_en"] is not None
    assert snapshot["motivo_cancelacion_codigo"] == "IMPREVISTO_MEDICO"
    assert snapshot["motivo_cancelacion_texto"] == "Urgencia en el hospital"


def test_a_full_length_doctor_reason_reaches_the_snapshot_exactly(app, engine) -> None:
    _patient, doctor, cita_id = _booked_by_patient(app, engine)
    texto = ("Urgencia médica en el hospital. " * 60)[:1500]
    assert len(texto) == 1500

    response = _cancel(
        app,
        _token(app, doctor),
        cita_id,
        {"motivo_codigo": "OTRO_MEDICO", "motivo_texto": texto},
    )

    assert response.status_code == 200, response.get_json()
    assert _snapshots(engine, cita_id)[-1]["motivo_cancelacion_texto"] == texto


def test_a_patient_cancellation_emits_null_reason_fields(app, engine) -> None:
    patient, _doctor, cita_id = _booked_by_patient(app, engine)

    response = _cancel(app, _token(app, patient), cita_id)

    assert response.status_code == 200, response.get_json()
    snapshot = _snapshots(engine, cita_id)[-1]
    _assert_no_private_fields(snapshot)
    assert snapshot["estado"] == "CANCELADA"
    assert snapshot["motivo_cancelacion_codigo"] is None
    assert snapshot["motivo_cancelacion_texto"] is None


def test_an_expiration_emits_the_expired_state_after_the_reservation(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    cita_id, _ = _book_with_free_text(app, engine, patient)
    sweeper = SchedulingService(
        store=SqlSchedulingStore(engine),
        clock=lambda: datetime.now(UTC) + timedelta(hours=1),
        hold_minutes=15,
        expiry_batch_size=100,
    )

    sweeper.expire_overdue_holds()

    snapshots = _snapshots(engine, cita_id)
    assert [snapshot["estado"] for snapshot in snapshots] == ["PENDIENTE_PAGO", "EXPIRADA"]
    _assert_no_private_fields(snapshots[-1])
    assert FREE_TEXT not in json.dumps(snapshots)


def test_each_change_gets_its_own_version_for_the_appointment(app, engine) -> None:
    _patient, doctor, cita_id = _booked_by_patient(app, engine)
    _cancel(app, _token(app, doctor), cita_id, _valid_reason())

    with engine.connect() as conn:
        versions = conn.execute(
            text(
                "SELECT version_agregado, tipo_evento FROM eventos_outbox "
                "WHERE agregado_tipo = 'cita' AND agregado_id = :c ORDER BY version_agregado"
            ),
            {"c": cita_id},
        ).all()
    numbers = [row.version_agregado for row in versions]
    assert numbers == list(range(1, len(numbers) + 1))
    assert [row.tipo_evento for row in versions] == [
        "cita.reservada",
        "cita.actualizada",
        "cita.cancelada",
        "cita.actualizada",
    ]
