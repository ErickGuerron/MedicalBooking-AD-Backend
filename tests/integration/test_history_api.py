"""Clinical history through the API against SQL Server: access matrix, verification,
guarded changes, vital-sign corrections, append-only event log and the access audit."""

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
FORBIDDEN = "Access to this resource is not allowed"
ALLERGY = {
    "sustancia_codigo": "PENICILINA",
    "severidad": "GRAVE",
    "reaccion": "Urticaria",
}


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _patient_id(engine: Engine, user_id: str) -> str:
    with engine.connect() as conn:
        return str(
            uuid.UUID(
                str(
                    conn.execute(
                        text("SELECT id FROM pacientes WHERE usuario_id = :u"), {"u": user_id}
                    ).scalar_one()
                )
            )
        )


def _medico_id(engine: Engine, user_id: str) -> str:
    with engine.connect() as conn:
        return str(
            uuid.UUID(
                str(
                    conn.execute(
                        text("SELECT id FROM medicos WHERE usuario_id = :u"), {"u": user_id}
                    ).scalar_one()
                )
            )
        )


def _treated_patient(app: Flask, engine: Engine):
    """A doctor, a patient and one appointment between them. Returns headers and ids."""
    doctor = _user(engine, "MEDICO")
    patient = _user(engine, "PACIENTE")
    slot = _slot(engine, days_ahead=3, doctor=doctor)
    with engine.begin() as conn:
        cita = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, "
                "reservada_en, "
                "clave_idempotencia) VALUES (:id, :p, :h, :s, 'CONFIRMADA', SYSUTCDATETIME(), :k)"
            ),
            {
                "id": cita,
                "p": _patient_id(engine, patient),
                "h": slot,
                "s": _service(engine),
                "k": f"hist-{cita}",
            },
        )
    return {
        "doctor": _token(app, doctor),
        "patient": _token(app, patient),
        "paciente_id": _patient_id(engine, patient),
        "cita_id": cita,
        "doctor_user": doctor,
        "patient_user": patient,
    }


def _stranger_doctor(app: Flask, engine: Engine) -> dict[str, str]:
    return _token(app, _user(engine, "MEDICO"))


def _lab(app: Flask, engine: Engine) -> dict[str, str]:
    return _token(app, _user(engine, "LABORATORISTA"))


def _admin(app: Flask, engine: Engine) -> dict[str, str]:
    return _token(app, _user(engine, "ADMINISTRADOR"))


def _audit_rows(engine: Engine, paciente_id: str) -> list[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT accion, resultado FROM auditoria_accesos WHERE paciente_id = :p"),
            {"p": paciente_id},
        ).all()
    return [(row.accion, row.resultado) for row in rows]


# ---------- registering and verifying ----------


def test_a_treating_doctor_registers_a_verified_allergy_and_the_patient_declares_one(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    registered = client.post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias",
        json=ALLERGY,
        headers=ctx["doctor"],
    )
    assert registered.status_code == 201, registered.get_json()
    assert registered.get_json()["verificada"] is True
    assert registered.get_json()["origen"] == "REGISTRADA_MEDICO"

    declared = client.post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias",
        json={**ALLERGY, "sustancia_codigo": "CACAHUATE"},
        headers=ctx["patient"],
    )
    assert declared.status_code == 201, declared.get_json()
    assert declared.get_json()["verificada"] is False


def test_only_the_treating_doctor_verifies_a_declared_entry_and_only_once(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    base = f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias"
    declared = client.post(base, json=ALLERGY, headers=ctx["patient"]).get_json()
    url = f"{base}/{declared['id']}/verificar"

    assert client.post(url, headers=ctx["patient"]).status_code == 403
    assert client.post(url, headers=_stranger_doctor(app, engine)).status_code == 403
    verified = client.post(url, headers=ctx["doctor"])
    assert verified.status_code == 200
    assert verified.get_json()["verificada"] is True
    assert client.post(url, headers=ctx["doctor"]).status_code == 409


# ---------- access matrix ----------


def test_the_access_matrix_for_reading_the_history(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    url = f"/api/v1/patients/{ctx['paciente_id']}/historia"
    assert client.get(url, headers=ctx["doctor"]).status_code == 200
    assert client.get(url, headers=ctx["patient"]).status_code == 200
    assert client.get(url, headers=_stranger_doctor(app, engine)).status_code == 403
    assert client.get(url, headers=_lab(app, engine)).status_code == 403
    assert client.get(url, headers=_admin(app, engine)).status_code == 403


def test_a_patient_cannot_read_another_patients_history(app, engine):
    ctx = _treated_patient(app, engine)
    other = _treated_patient(app, engine)
    response = app.test_client().get(
        f"/api/v1/patients/{other['paciente_id']}/historia", headers=ctx["patient"]
    )
    assert response.status_code == 403
    assert response.get_json()["detail"] == FORBIDDEN


def test_a_missing_entry_and_a_foreign_entry_give_the_same_403(app, engine):
    ctx = _treated_patient(app, engine)
    other = _treated_patient(app, engine)
    client = app.test_client()
    foreign = client.post(
        f"/api/v1/patients/{other['paciente_id']}/historia/alergias",
        json=ALLERGY,
        headers=other["doctor"],
    ).get_json()
    missing_response = client.post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias/{uuid.uuid4()}/verificar",
        headers=ctx["doctor"],
    )
    foreign_response = client.post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias/{foreign['id']}/verificar",
        headers=ctx["doctor"],
    )
    assert missing_response.status_code == foreign_response.status_code == 403
    assert (
        missing_response.get_json()["detail"] == foreign_response.get_json()["detail"] == FORBIDDEN
    )


def test_the_history_read_answers_the_active_allergy_first(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    base = f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias"
    first = client.post(base, json=ALLERGY, headers=ctx["doctor"]).get_json()
    client.post(base, json={**ALLERGY, "sustancia_codigo": "LATEX"}, headers=ctx["doctor"])
    client.patch(
        f"{base}/{first['id']}/estado",
        json={"expected_estado": "ACTIVA", "estado": "RESUELTA", "motivo": "Ya no reacciona"},
        headers=ctx["doctor"],
    )
    history = client.get(
        f"/api/v1/patients/{ctx['paciente_id']}/historia", headers=ctx["doctor"]
    ).get_json()
    assert [entry["estado"] for entry in history["alergias"]] == ["ACTIVA", "RESUELTA"]


# ---------- guarded state changes ----------


def test_a_stale_state_change_is_refused_and_a_motivo_is_required(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    base = f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias"
    entry = client.post(base, json=ALLERGY, headers=ctx["doctor"]).get_json()
    url = f"{base}/{entry['id']}/estado"

    no_motivo = client.patch(
        url, json={"expected_estado": "ACTIVA", "estado": "RESUELTA"}, headers=ctx["doctor"]
    )
    assert no_motivo.status_code == 422

    changed = client.patch(
        url,
        json={"expected_estado": "ACTIVA", "estado": "RESUELTA", "motivo": "ok"},
        headers=ctx["doctor"],
    )
    assert changed.status_code == 200
    stale = client.patch(
        url,
        json={"expected_estado": "ACTIVA", "estado": "ERRONEA", "motivo": "otra vez"},
        headers=ctx["doctor"],
    )
    assert stale.status_code == 409


def test_the_event_log_refuses_updates_and_deletes_at_the_database(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    client.post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias",
        json=ALLERGY,
        headers=ctx["doctor"],
    )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE historia_eventos SET motivo = 'x' WHERE paciente_id = :p"),
                {"p": ctx["paciente_id"]},
            )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM historia_eventos WHERE paciente_id = :p"),
                {"p": ctx["paciente_id"]},
            )


# ---------- vital signs ----------


def test_vital_signs_are_recorded_and_a_correction_leaves_the_original(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    recorded = client.post(
        f"/api/v1/citas/{ctx['cita_id']}/signos-vitales",
        json={"peso_kg": 72.5, "pa_sistolica": 120, "pa_diastolica": 80},
        headers=ctx["doctor"],
    )
    assert recorded.status_code == 201, recorded.get_json()
    corrected = client.post(
        f"/api/v1/signos-vitales/{recorded.get_json()['id']}/correccion",
        json={"peso_kg": 70.0, "motivo": "Bascula mal calibrada"},
        headers=ctx["doctor"],
    )
    assert corrected.status_code == 201, corrected.get_json()
    assert corrected.get_json()["corrige_a"] == recorded.get_json()["id"]
    history = client.get(
        f"/api/v1/patients/{ctx['paciente_id']}/historia", headers=ctx["doctor"]
    ).get_json()
    weights = {v["id"]: v["peso_kg"] for v in history["signos_vitales"]}
    assert weights[recorded.get_json()["id"]] == 72.5


def test_vital_signs_out_of_range_or_as_text_are_refused(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    url = f"/api/v1/citas/{ctx['cita_id']}/signos-vitales"
    assert client.post(url, json={"saturacion": 101}, headers=ctx["doctor"]).status_code == 422
    assert client.post(url, json={"pa_sistolica": "120"}, headers=ctx["doctor"]).status_code == 422
    assert client.post(url, json={"saturacion": True}, headers=ctx["doctor"]).status_code == 422
    assert client.post(url, json={}, headers=ctx["doctor"]).status_code == 422


def test_a_patient_cannot_record_vital_signs_for_their_own_appointment(app, engine):
    ctx = _treated_patient(app, engine)
    response = app.test_client().post(
        f"/api/v1/citas/{ctx['cita_id']}/signos-vitales",
        json={"peso_kg": 70},
        headers=ctx["patient"],
    )
    assert response.status_code == 403


# ---------- request shape ----------


def test_unknown_fields_and_bad_dates_are_refused(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    base = f"/api/v1/patients/{ctx['paciente_id']}/historia/vacunas"
    future = (datetime.now(UTC) + timedelta(days=30)).date().isoformat()
    assert (
        client.post(
            base,
            json={
                "vacuna_codigo": "INFLUENZA",
                "dosis_numero": 1,
                "fecha": "2026-06-01",
                "estado": "ERRONEA",
            },
            headers=ctx["doctor"],
        ).status_code
        == 422
    )
    assert (
        client.post(
            base,
            json={"vacuna_codigo": "INFLUENZA", "dosis_numero": True, "fecha": "2026-06-01"},
            headers=ctx["doctor"],
        ).status_code
        == 422
    )
    assert (
        client.post(
            base,
            json={"vacuna_codigo": "INFLUENZA", "dosis_numero": 1, "fecha": future},
            headers=ctx["doctor"],
        ).status_code
        == 422
    )


def test_a_malformed_body_is_a_422_not_a_crash(app, engine):
    ctx = _treated_patient(app, engine)
    response = app.test_client().post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias",
        data="{no es json",
        headers={**ctx["doctor"], "Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_an_unknown_kind_of_entry_is_a_422(app, engine):
    ctx = _treated_patient(app, engine)
    response = app.test_client().post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/tatuajes",
        json={},
        headers=ctx["doctor"],
    )
    assert response.status_code == 422


# ---------- audit ----------


def test_denials_and_reads_are_in_the_access_audit_without_values(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    client.post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias",
        json=ALLERGY,
        headers=ctx["doctor"],
    )
    client.get(f"/api/v1/patients/{ctx['paciente_id']}/historia", headers=ctx["doctor"])
    client.get(f"/api/v1/patients/{ctx['paciente_id']}/historia", headers=_lab(app, engine))
    rows = _audit_rows(engine, ctx["paciente_id"])
    assert ("consultar", "OK") in rows
    assert ("consultar", "DENEGADO") in rows
    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT * FROM auditoria_accesos WHERE paciente_id = :p"),
            {"p": ctx["paciente_id"]},
        ).all()
    assert all("Penicilina" not in str(row) for row in stored)
