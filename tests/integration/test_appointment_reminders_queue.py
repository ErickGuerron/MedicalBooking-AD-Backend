"""The day-of-appointment reminder is queued once per confirmed appointment of the Ecuador day.

Against real SQL Server: a confirmed appointment today gets one reminder event, however many
times the worker runs; pending or tomorrow's appointments get none.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.workers.appointment_reminders import queue_reminders
from tests.integration.test_scheduling_api import _service, _slot, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Flask:
    documents = tmp_path_factory.mktemp("reminders")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    return create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _ecuador_today_at(now: datetime, hour: int, days: int = 0) -> datetime:
    """A UTC instant at hour on the Ecuador calendar day of now (shifted by days)."""
    local = now.astimezone(UTC) - timedelta(hours=5)
    day = local.date() + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour + 5)


def _appointment(engine: Engine, when_utc: datetime, estado: str) -> str:
    """A slot moved to ``when_utc``, booked by a new patient with the given state."""
    slot = _slot(engine, days_ahead=3)
    paciente = _user(engine, "PACIENTE")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE horarios_disponibles SET inicio_utc = :i, fin_utc = :f WHERE id = :h"),
            {"i": when_utc, "f": when_utc + timedelta(minutes=30), "h": slot},
        )
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
                "h": slot,
                "s": _service(engine),
                "e": estado,
                "k": f"reminder-{cita}",
            },
        )
    return cita


def _reminders_for(engine: Engine, cita: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT COUNT(*) FROM eventos_outbox WHERE agregado_id = :c "
                "AND tipo_evento = 'cita.recordatorio'"
            ),
            {"c": cita},
        ).scalar_one()


def test_a_confirmed_appointment_today_gets_one_reminder_however_often_the_worker_runs(
    engine,
) -> None:
    now = datetime.now(UTC)
    cita = _appointment(engine, _ecuador_today_at(now, 14), "CONFIRMADA")

    first = queue_reminders(engine, now)
    second = queue_reminders(engine, now)

    assert first >= 1
    assert _reminders_for(engine, cita) == 1
    assert second == 0


def test_a_pending_payment_appointment_gets_no_reminder(engine) -> None:
    now = datetime.now(UTC)
    cita = _appointment(engine, _ecuador_today_at(now, 15), "PENDIENTE_PAGO")

    queue_reminders(engine, now)

    assert _reminders_for(engine, cita) == 0


def test_tomorrows_appointment_is_not_reminded_today(engine) -> None:
    now = datetime.now(UTC)
    cita = _appointment(engine, _ecuador_today_at(now, 10, days=1), "CONFIRMADA")

    queue_reminders(engine, now)

    assert _reminders_for(engine, cita) == 0
