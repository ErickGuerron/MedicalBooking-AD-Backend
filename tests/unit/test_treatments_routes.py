"""HTTP contract of the treatment endpoints (S4-S6) with in-memory fakes.

Covers bearer auth, strict request bodies (plan_tratamiento is never accepted, S8), response
shapes, status codes for foreign resources (403), and the append-only follow-up collection.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from flask import Flask, jsonify

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.treatments.application.ports import CitaContext
from medicalbooking.modules.treatments.application.use_cases import TreatmentService
from medicalbooking.modules.treatments.presentation.routes import treatments_bp
from medicalbooking.shared.domain.errors import AppError, Unauthenticated
from tests.unit.test_treatments_service import MEDICO, PACIENTE, FakeStore, _actor, _id

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class _Tokens:
    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self._profiles = profiles

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self._profiles:
            raise Unauthenticated("Authentication required")
        return self._profiles[token]


class World:
    def __init__(self) -> None:
        self.store = FakeStore()
        self.doctor = _actor(MEDICO)
        self.other_doctor = _actor(MEDICO)
        self.patient = _actor(PACIENTE)
        self.other_patient = _actor(PACIENTE)
        self.store.medico_by_user[self.doctor.user_id] = _id()
        self.store.medico_by_user[self.other_doctor.user_id] = _id()
        self.store.patient_by_user[self.patient.user_id] = _id()
        self.store.patient_by_user[self.other_patient.user_id] = _id()
        self.cita_id = _id()
        self.store.citas[self.cita_id] = CitaContext(
            cita_id=self.cita_id,
            paciente_id=self.store.patient_by_user[self.patient.user_id],
            medico_id=self.store.medico_by_user[self.doctor.user_id],
        )
        self.service = TreatmentService(store=self.store, clock=lambda: NOW)
        self.client = self._client()

    def _client(self):
        app = Flask("test-treatments-routes")
        app.register_blueprint(treatments_bp)
        app.extensions["medicalbooking.treatments"] = self.service
        app.extensions["medicalbooking.identity"] = _Tokens(
            {
                "tok-patient": self.patient,
                "tok-other-patient": self.other_patient,
                "tok-doctor": self.doctor,
                "tok-other-doctor": self.other_doctor,
            }
        )

        @app.errorhandler(AppError)
        def _domain_error(error: AppError):  # noqa: ANN202
            return jsonify({"status": error.status, "detail": error.detail}), error.status

        return app.test_client()

    def create_via_service(self) -> str:
        treatment = self.service.create_treatment(
            self.doctor,
            self.cita_id,
            descripcion="Reposo",
            fecha_inicio=datetime(2026, 11, 2).date(),
        )
        return treatment.id


@pytest.fixture
def world() -> World:
    return World()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _treatments_url(cita_id: str) -> str:
    return f"/api/v1/citas/{cita_id}/tratamientos"


def _body(**overrides) -> dict:
    body = {"descripcion": "Reposo y control", "fecha_inicio": "2026-11-02"}
    body.update(overrides)
    return body


# ---------- authentication and create ----------


def test_create_requires_a_bearer_token(world) -> None:
    response = world.client.post(_treatments_url(world.cita_id), json=_body())

    assert response.status_code == 401


def test_doctor_creates_a_treatment_with_the_documented_shape(world) -> None:
    response = world.client.post(
        _treatments_url(world.cita_id), json=_body(), headers=_bearer("tok-doctor")
    )

    body = response.get_json()
    assert response.status_code == 201
    assert set(body) == {
        "id",
        "cita_id",
        "descripcion",
        "estado",
        "fecha_inicio",
        "fecha_fin",
    }
    assert body["estado"] == "ACTIVO"
    assert body["fecha_inicio"] == "2026-11-02"
    assert body["fecha_fin"] is None


def test_plan_tratamiento_is_not_accepted_in_treatment_bodies(world) -> None:
    response = world.client.post(
        _treatments_url(world.cita_id),
        json=_body(plan_tratamiento="texto libre"),
        headers=_bearer("tok-doctor"),
    )

    assert response.status_code == 422
    assert world.store.treatments == {}


@pytest.mark.parametrize("estado", ["PAUSADO", "activo"])
def test_unknown_estado_is_422(world, estado) -> None:
    response = world.client.post(
        _treatments_url(world.cita_id),
        json=_body(estado=estado),
        headers=_bearer("tok-doctor"),
    )

    assert response.status_code == 422


def test_malformed_date_is_422(world) -> None:
    response = world.client.post(
        _treatments_url(world.cita_id),
        json=_body(fecha_inicio="02/11/2026"),
        headers=_bearer("tok-doctor"),
    )

    assert response.status_code == 422


def test_foreign_doctor_creation_answers_403(world) -> None:
    response = world.client.post(
        _treatments_url(world.cita_id), json=_body(), headers=_bearer("tok-other-doctor")
    )

    assert response.status_code == 403
    assert world.store.treatments == {}


# ---------- reads ----------


def test_owning_patient_lists_treatments_as_items(world) -> None:
    world.create_via_service()

    response = world.client.get(_treatments_url(world.cita_id), headers=_bearer("tok-patient"))

    assert response.status_code == 200
    assert set(response.get_json()) == {"items"}
    assert len(response.get_json()["items"]) == 1


def test_other_patient_listing_answers_403(world) -> None:
    world.create_via_service()

    response = world.client.get(
        _treatments_url(world.cita_id), headers=_bearer("tok-other-patient")
    )

    assert response.status_code == 403


# ---------- updates ----------


def test_doctor_patches_state_and_end_date(world) -> None:
    treatment_id = world.create_via_service()

    response = world.client.patch(
        f"/api/v1/tratamientos/{treatment_id}",
        json={"estado": "FINALIZADO", "fecha_fin": "2026-11-09"},
        headers=_bearer("tok-doctor"),
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["estado"] == "FINALIZADO"
    assert body["fecha_fin"] == "2026-11-09"


def test_finalizing_without_end_date_answers_422(world) -> None:
    treatment_id = world.create_via_service()

    response = world.client.patch(
        f"/api/v1/tratamientos/{treatment_id}",
        json={"estado": "FINALIZADO"},
        headers=_bearer("tok-doctor"),
    )

    assert response.status_code == 422


def test_patch_with_no_fields_answers_422(world) -> None:
    treatment_id = world.create_via_service()

    response = world.client.patch(
        f"/api/v1/tratamientos/{treatment_id}", json={}, headers=_bearer("tok-doctor")
    )

    assert response.status_code == 422


def test_patch_rejects_fields_outside_the_contract(world) -> None:
    treatment_id = world.create_via_service()

    response = world.client.patch(
        f"/api/v1/tratamientos/{treatment_id}",
        json={"cita_id": _id()},
        headers=_bearer("tok-doctor"),
    )

    assert response.status_code == 422


def test_foreign_doctor_patch_answers_403(world) -> None:
    treatment_id = world.create_via_service()

    response = world.client.patch(
        f"/api/v1/tratamientos/{treatment_id}",
        json={"estado": "SUSPENDIDO"},
        headers=_bearer("tok-other-doctor"),
    )

    assert response.status_code == 403


# ---------- follow-ups (append-only) ----------


def test_doctor_appends_a_follow_up_with_the_documented_shape(world) -> None:
    treatment_id = world.create_via_service()

    response = world.client.post(
        f"/api/v1/tratamientos/{treatment_id}/seguimientos",
        json={"nota": "Evolucion favorable"},
        headers=_bearer("tok-doctor"),
    )

    body = response.get_json()
    assert response.status_code == 201
    assert set(body) == {"id", "tratamiento_id", "nota", "registrado_en"}
    assert body["registrado_en"] == "2026-11-02T09:00:00Z"


def test_follow_up_rejects_unknown_fields(world) -> None:
    treatment_id = world.create_via_service()

    response = world.client.post(
        f"/api/v1/tratamientos/{treatment_id}/seguimientos",
        json={"nota": "x", "estado": "FINALIZADO"},
        headers=_bearer("tok-doctor"),
    )

    assert response.status_code == 422


def test_owning_patient_reads_follow_ups(world) -> None:
    treatment_id = world.create_via_service()
    world.client.post(
        f"/api/v1/tratamientos/{treatment_id}/seguimientos",
        json={"nota": "Evolucion favorable"},
        headers=_bearer("tok-doctor"),
    )

    response = world.client.get(
        f"/api/v1/tratamientos/{treatment_id}/seguimientos", headers=_bearer("tok-patient")
    )

    assert response.status_code == 200
    assert [item["nota"] for item in response.get_json()["items"]] == ["Evolucion favorable"]


@pytest.mark.parametrize("method", ["patch", "put", "delete"])
def test_follow_ups_cannot_be_edited_or_deleted(world, method) -> None:
    treatment_id = world.create_via_service()
    send = getattr(world.client, method)

    response = send(
        f"/api/v1/tratamientos/{treatment_id}/seguimientos",
        json={"nota": "cambio"},
        headers=_bearer("tok-doctor"),
    )

    assert response.status_code == 405


def test_foreign_patient_follow_up_read_answers_403(world) -> None:
    treatment_id = world.create_via_service()

    response = world.client.get(
        f"/api/v1/tratamientos/{treatment_id}/seguimientos",
        headers=_bearer("tok-other-patient"),
    )

    assert response.status_code == 403
