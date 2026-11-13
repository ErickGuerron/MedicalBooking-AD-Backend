"""A doctor cancels an appointment of an own agenda, with a reason (scheduling + refunds).

Real SQL Server. Stripe is the ProviderDouble over httpx.MockTransport. Each test builds its own
clinician, agenda, patients and payments with unique identifiers.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from urllib.parse import parse_qs

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.payments.application.refund_service import refund_request_id
from tests.integration.test_refund_request_api import (
    ProviderDouble,
    Seed,
    _install,
    _refunds_for,
    _seed,
)
from tests.integration.test_scheduling_api import (
    _book,
    _service,
    _slot,
    _token,
    _user,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
OUTSIDER_MESSAGE = "Access to this resource is not allowed"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _doctor_of(engine: Engine, cita_id: str) -> str:
    """The user id of the doctor whose agenda holds the appointment."""
    with engine.connect() as conn:
        return str(
            conn.execute(
                text(
                    "SELECT m.usuario_id FROM citas c "
                    "JOIN horarios_disponibles h ON h.id = c.horario_id "
                    "JOIN agendas a ON a.id = h.agenda_id "
                    "JOIN medicos m ON m.id = a.medico_id WHERE c.id = :c"
                ),
                {"c": cita_id},
            ).scalar_one()
        ).lower()


def _booked_by_patient(app: Flask, engine: Engine):
    """An unpaid appointment booked through the API. Returns (patient user, doctor user, cita)."""
    patient = _user(engine, "PACIENTE")
    slot = _slot(engine, days_ahead=3)
    response = _book(
        app.test_client(),
        _token(app, patient),
        key=f"doctor-cancel-{uuid.uuid4()}",
        slot=slot,
        servicio=_service(engine),
    )
    assert response.status_code == 201, response.get_json()
    cita = response.get_json()["id"]
    return patient, _doctor_of(engine, cita), cita


def _cancel(app: Flask, headers: dict[str, str], cita: str, body=None):
    if body is None:
        return app.test_client().post(f"/api/v1/appointments/{cita}/cancel", headers=headers)
    return app.test_client().post(f"/api/v1/appointments/{cita}/cancel", json=body, headers=headers)


def _row(engine: Engine, cita: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT estado, motivo_cancelacion_codigo, motivo_cancelacion_texto "
                "FROM citas WHERE id = :c"
            ),
            {"c": cita},
        ).one()


def _unchanged(engine: Engine, cita: str) -> None:
    row = _row(engine, cita)
    assert row.estado == "PENDIENTE_PAGO"
    assert row.motivo_cancelacion_codigo is None
    assert row.motivo_cancelacion_texto is None


def _valid_reason(codigo: str = "IMPREVISTO_MEDICO", texto: str = "Urgencia en el hospital"):
    return {"motivo_codigo": codigo, "motivo_texto": texto}


# ---------- a doctor cancels with a reason ----------


def test_doctor_cancels_own_patients_appointment_with_a_reason(app, engine) -> None:
    _, doctor, cita = _booked_by_patient(app, engine)

    response = _cancel(app, _token(app, doctor), cita, _valid_reason())

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["estado"] == "CANCELADA"
    row = _row(engine, cita)
    assert row.estado == "CANCELADA"
    assert row.motivo_cancelacion_codigo == "IMPREVISTO_MEDICO"
    assert row.motivo_cancelacion_texto == "Urgencia en el hospital"


def test_doctor_cancellation_without_a_reason_is_422_and_changes_nothing(app, engine) -> None:
    _, doctor, cita = _booked_by_patient(app, engine)
    headers = _token(app, doctor)

    no_body = _cancel(app, headers, cita)
    empty_body = _cancel(app, headers, cita, {})

    for response in (no_body, empty_body):
        assert response.status_code == 422, response.get_json()
        assert response.get_json()["detail"] == "MOTIVO_CANCELACION_REQUERIDO"
    _unchanged(engine, cita)


def test_doctor_cancellation_with_an_invalid_reason_code_is_422(app, engine) -> None:
    _, doctor, cita = _booked_by_patient(app, engine)

    response = _cancel(app, _token(app, doctor), cita, _valid_reason(codigo="CANCELACION_PACIENTE"))

    assert response.status_code == 422
    assert response.get_json()["detail"] == "MOTIVO_CANCELACION_INVALIDO"
    _unchanged(engine, cita)


def test_doctor_cancellation_with_a_text_over_1500_characters_is_422(app, engine) -> None:
    _, doctor, cita = _booked_by_patient(app, engine)

    response = _cancel(app, _token(app, doctor), cita, _valid_reason(texto="x" * 1501))

    assert response.status_code == 422
    assert response.get_json()["detail"] == "MOTIVO_CANCELACION_INVALIDO"
    _unchanged(engine, cita)


def test_unknown_fields_in_the_cancel_body_are_422(app, engine) -> None:
    _, doctor, cita = _booked_by_patient(app, engine)

    response = _cancel(app, _token(app, doctor), cita, {**_valid_reason(), "monto": 1})

    assert response.status_code == 422
    _unchanged(engine, cita)


def test_another_doctor_gets_the_same_403_as_a_missing_appointment(app, engine) -> None:
    _, _, cita = _booked_by_patient(app, engine)
    # A second doctor with an agenda and a patient of their own, not the owner of `cita`.
    other_slot = _slot(engine, days_ahead=4)
    other_cita = _booked_by_patient_slot(app, engine, other_slot)
    other_doctor = _doctor_of(engine, other_cita)

    foreign = _cancel(app, _token(app, other_doctor), cita, _valid_reason())
    missing = _cancel(app, _token(app, other_doctor), str(uuid.uuid4()), _valid_reason())

    assert foreign.status_code == missing.status_code == 403
    assert foreign.get_json()["detail"] == missing.get_json()["detail"] == OUTSIDER_MESSAGE
    _unchanged(engine, cita)


def _booked_by_patient_slot(app: Flask, engine: Engine, slot: str) -> str:
    """Books the given slot for a new patient and returns the appointment id."""
    response = _book(
        app.test_client(),
        _token(app, _user(engine, "PACIENTE")),
        key=f"doctor-cancel-{uuid.uuid4()}",
        slot=slot,
        servicio=_service(engine),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


# ---------- patient cancellation keeps working and stores no reason ----------


def test_patient_cancellation_still_works_and_stores_no_reason(app, engine) -> None:
    patient, _, cita = _booked_by_patient(app, engine)

    response = _cancel(app, _token(app, patient), cita)

    assert response.status_code == 200, response.get_json()
    row = _row(engine, cita)
    assert row.estado == "CANCELADA"
    assert row.motivo_cancelacion_codigo is None
    assert row.motivo_cancelacion_texto is None


def test_reason_fields_sent_by_a_patient_are_ignored(app, engine) -> None:
    patient, _, cita = _booked_by_patient(app, engine)

    response = _cancel(app, _token(app, patient), cita, _valid_reason())

    assert response.status_code == 200, response.get_json()
    row = _row(engine, cita)
    assert row.estado == "CANCELADA"
    assert row.motivo_cancelacion_codigo is None
    assert row.motivo_cancelacion_texto is None


def test_a_patient_body_that_is_not_a_json_object_is_ignored_as_before(app, engine) -> None:
    patient, _, cita = _booked_by_patient(app, engine)

    response = app.test_client().post(
        f"/api/v1/appointments/{cita}/cancel",
        data="[]",
        headers={**_token(app, patient), "Content-Type": "application/json"},
    )

    assert response.status_code == 200, response.get_json()
    assert _row(engine, cita).estado == "CANCELADA"


# ---------- refunds of a doctor cancellation ----------


def _paid_seed(engine: Engine) -> tuple[Seed, str]:
    """A paid, still confirmed appointment and its doctor."""
    seed = _seed(engine, estado_cita="CONFIRMADA", sin_cancelacion=True)
    return seed, _doctor_of(engine, seed.cita_id)


def _owner_of_refunds(engine: Engine, pago_id: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT pa.usuario_id FROM reembolsos r "
                "JOIN pagos p ON p.id = r.pago_id JOIN facturas f ON f.id = p.factura_id "
                "JOIN pacientes pa ON pa.id = f.paciente_id WHERE r.pago_id = :p"
            ),
            {"p": pago_id},
        ).all()
    return [str(row[0]).lower() for row in rows]


def test_paid_appointment_cancelled_by_the_doctor_requests_one_refund_for_the_patient(
    app, engine
) -> None:
    seed, doctor = _paid_seed(engine)
    double = ProviderDouble(refund_status="pending")
    _install(app, engine, double)

    response = _cancel(app, _token(app, doctor), seed.cita_id, _valid_reason())

    assert response.status_code == 200, response.get_json()
    [(reembolso_id, estado, stripe_id)] = _refunds_for(engine, seed.pago_id)
    assert estado == "PENDIENTE"
    assert stripe_id is not None
    # The owner of the refund is the patient who paid, never the doctor who cancelled.
    assert _owner_of_refunds(engine, seed.pago_id) == [seed.paciente_user.lower()]
    assert doctor != seed.paciente_user.lower()
    with engine.connect() as conn:
        motivo = conn.execute(
            text("SELECT motivo FROM reembolsos WHERE id = :r"), {"r": reembolso_id}
        ).scalar_one()
    assert motivo == "CANCELACION_MEDICO"
    # The request key is the same as the patient path: auto-cancel-{cita}.
    assert reembolso_id == refund_request_id(seed.pago_id, f"auto-cancel-{seed.cita_id}")
    assert len(double.calls) == 1
    sent = parse_qs(double.calls[0].content.decode())
    assert sent["payment_intent"] == [seed.payment_intent]
    assert double.calls[0].headers["Idempotency-Key"] == f"refund-{reembolso_id}"


def test_patient_cancellation_of_a_paid_appointment_still_uses_the_patient_reason(
    app, engine
) -> None:
    seed, _ = _paid_seed(engine)
    double = ProviderDouble(refund_status="pending")
    _install(app, engine, double)

    response = _cancel(app, _token(app, seed.paciente_user), seed.cita_id)

    assert response.status_code == 200, response.get_json()
    [(reembolso_id, _, _)] = _refunds_for(engine, seed.pago_id)
    with engine.connect() as conn:
        motivo = conn.execute(
            text("SELECT motivo FROM reembolsos WHERE id = :r"), {"r": reembolso_id}
        ).scalar_one()
    assert motivo == "CANCELACION_PACIENTE"
    assert _owner_of_refunds(engine, seed.pago_id) == [seed.paciente_user.lower()]
    assert len(double.calls) == 1


def test_unpaid_appointment_cancelled_by_the_doctor_has_no_refund(app, engine) -> None:
    _, doctor, cita = _booked_by_patient(app, engine)
    double = ProviderDouble()
    _install(app, engine, double)

    response = _cancel(app, _token(app, doctor), cita, _valid_reason())

    assert response.status_code == 200, response.get_json()
    assert _row(engine, cita).estado == "CANCELADA"
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM reembolsos r JOIN pagos p ON p.id = r.pago_id "
                "JOIN facturas f ON f.id = p.factura_id WHERE f.cita_id = :c"
            ),
            {"c": cita},
        ).scalar_one()
    assert count == 0
    assert double.calls == []


def test_the_doctor_cancellation_never_refunds_another_patients_payment(app, engine) -> None:
    mine, doctor = _paid_seed(engine)
    theirs = _seed(engine)  # another patient's paid, already cancelled appointment
    double = ProviderDouble(refund_status="pending")
    _install(app, engine, double)

    response = _cancel(app, _token(app, doctor), mine.cita_id, _valid_reason())

    assert response.status_code == 200, response.get_json()
    assert _refunds_for(engine, theirs.pago_id) == []
    assert len(double.calls) == 1
    sent = parse_qs(double.calls[0].content.decode())
    assert sent["payment_intent"] == [mine.payment_intent]
    assert theirs.payment_intent not in [
        parse_qs(call.content.decode())["payment_intent"][0] for call in double.calls
    ]
