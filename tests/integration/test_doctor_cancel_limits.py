"""Doctor cancellation limits through the API: quota, 24-hour notice, admin approval (S1-S4).

Real SQL Server. Each test builds its own doctor, agenda, slots and patients.
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
from tests.integration.test_scheduling_api import _book, _service, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
REASON = {"motivo_codigo": "IMPREVISTO_MEDICO", "motivo_texto": "Urgencia en el hospital"}


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _doctor_with_slots(engine: Engine, offsets_hours: list[int]) -> tuple[str, list[str]]:
    """One doctor with one agenda holding a free slot per offset (hours from now)."""
    doctor = _user(engine, "MEDICO")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
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
            {"m": medico_id, "c": consultorio_id, "d": now.date()},
        ).scalar_one()
        slots = []
        for hours in offsets_hours:
            start = (now + timedelta(hours=hours)).replace(tzinfo=None)
            slot_id = conn.execute(
                text(
                    "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc) "
                    "OUTPUT inserted.id VALUES (NEWID(), :a, :i, DATEADD(minute, 30, :i))"
                ),
                {"a": agenda_id, "i": start},
            ).scalar_one()
            slots.append(str(uuid.UUID(str(slot_id))))
    return doctor, slots


def _headers(app: Flask, user_id: str) -> dict[str, str]:
    return _token(app, user_id)


def _book_slot(app: Flask, engine: Engine, slot: str) -> str:
    patient = _user(engine, "PACIENTE")
    response = _book(
        app.test_client(),
        _headers(app, patient),
        key=f"limits-{uuid.uuid4()}",
        slot=slot,
        servicio=_service(engine),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def _doctor_cancel(app: Flask, doctor: str, cita: str):
    return app.test_client().post(
        f"/api/v1/appointments/{cita}/cancel",
        json=REASON,
        headers=_headers(app, doctor),
    )


def _approve(app: Flask, admin: str, cita: str, motivo: str | None = "Caso justificado"):
    body = {} if motivo is None else {"motivo": motivo}
    return app.test_client().post(
        f"/api/v1/appointments/{cita}/cancellation-approval",
        json=body,
        headers=_headers(app, admin),
    )


def _estado(engine: Engine, cita: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT estado FROM citas WHERE id = :c"), {"c": cita}
        ).scalar_one()


SLOT_HOURS = [72, 80, 88, 96]


def _doctor_cancelled_three_times(app: Flask, engine: Engine):
    """A doctor with a fourth booked appointment after three cancellations this month."""
    doctor, slots = _doctor_with_slots(engine, SLOT_HOURS)
    for slot in slots[:3]:
        cita = _book_slot(app, engine, slot)
        assert _doctor_cancel(app, doctor, cita).status_code == 200
    fourth = _book_slot(app, engine, slots[3])
    return doctor, fourth


# ---------- S1: monthly quota ----------


def test_the_fourth_doctor_cancellation_in_a_month_is_refused_until_approved(app, engine) -> None:
    doctor, fourth = _doctor_cancelled_three_times(app, engine)

    response = _doctor_cancel(app, doctor, fourth)

    assert response.status_code == 409, response.get_json()
    assert response.get_json()["detail"] == "LIMITE_CANCELACION_MEDICO"
    assert _estado(engine, fourth) == "PENDIENTE_PAGO"


def test_an_admin_approval_lets_the_doctor_cancel_over_the_quota(app, engine) -> None:
    doctor, fourth = _doctor_cancelled_three_times(app, engine)
    admin = _user(engine, "ADMINISTRADOR")

    approval = _approve(app, admin, fourth)
    cancel = _doctor_cancel(app, doctor, fourth)

    assert approval.status_code == 201, approval.get_json()
    assert cancel.status_code == 200, cancel.get_json()
    assert _estado(engine, fourth) == "CANCELADA"


def test_a_patient_cancellation_does_not_count_against_the_quota(app, engine) -> None:
    doctor, slots = _doctor_with_slots(engine, SLOT_HOURS)
    for slot in slots[:3]:
        cita = _book_slot(app, engine, slot)
        assert _doctor_cancel(app, doctor, cita).status_code == 200
    # Patient cancels a fourth appointment of the same doctor: the doctor's quota is untouched.
    patient = _user(engine, "PACIENTE")
    cita = _book(
        app.test_client(),
        _headers(app, patient),
        key=f"limits-{uuid.uuid4()}",
        slot=slots[3],
        servicio=_service(engine),
    ).get_json()["id"]
    response = app.test_client().post(
        f"/api/v1/appointments/{cita}/cancel", headers=_headers(app, patient)
    )
    assert response.status_code == 200, response.get_json()


# ---------- S2: 24-hour notice ----------


def test_less_than_24_hours_notice_is_refused_until_approved(app, engine) -> None:
    doctor, slots = _doctor_with_slots(engine, [5])
    cita = _book_slot(app, engine, slots[0])

    blocked = _doctor_cancel(app, doctor, cita)
    admin = _user(engine, "ADMINISTRADOR")
    _approve(app, admin, cita)
    allowed = _doctor_cancel(app, doctor, cita)

    assert blocked.status_code == 409
    assert blocked.get_json()["detail"] == "CANCELACION_MEDICO_FUERA_DE_PLAZO"
    assert allowed.status_code == 200, allowed.get_json()


# ---------- S3: admin approval rules ----------


def test_a_second_approval_for_the_same_appointment_is_409(app, engine) -> None:
    _, fourth = _doctor_cancelled_three_times(app, engine)
    admin = _user(engine, "ADMINISTRADOR")
    assert _approve(app, admin, fourth).status_code == 201

    response = _approve(app, admin, fourth)

    assert response.status_code == 409


def test_an_approval_needs_a_non_blank_reason(app, engine) -> None:
    _, fourth = _doctor_cancelled_three_times(app, engine)
    admin = _user(engine, "ADMINISTRADOR")

    blank = _approve(app, admin, fourth, motivo="   ")
    missing = _approve(app, admin, fourth, motivo=None)

    for response in (blank, missing):
        assert response.status_code == 422
        assert response.get_json()["detail"] == "MOTIVO_APROBACION_REQUERIDO"


def test_only_an_administrator_can_approve(app, engine) -> None:
    doctor, fourth = _doctor_cancelled_three_times(app, engine)
    patient = _user(engine, "PACIENTE")

    by_doctor = _approve(app, doctor, fourth)
    by_patient = _approve(app, patient, fourth)

    assert by_doctor.status_code == 403
    assert by_patient.status_code == 403


def test_approving_an_unknown_appointment_is_404(app, engine) -> None:
    admin = _user(engine, "ADMINISTRADOR")

    response = _approve(app, admin, str(uuid.uuid4()))

    assert response.status_code == 404

