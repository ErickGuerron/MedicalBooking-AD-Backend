"""POST /appointments/{id}/consultation: permission, ownership, idempotence and states."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from flask import Flask, jsonify
from flask.testing import FlaskClient

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.ports import (
    Consultation,
    ConsultationOutcome,
    ConsultationStatus,
    OpenConsultationCommand,
)
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.presentation.routes import scheduling_bp
from medicalbooking.shared.domain.errors import AppError, Unauthenticated
from tests.unit.test_scheduling_service import FakeStore

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
CITA = "44444444-4444-4444-8444-444444444444"
PATH = f"/api/v1/appointments/{CITA}/consultation"


@dataclass
class ConsultStore(FakeStore):
    status: ConsultationStatus = ConsultationStatus.CREATED
    commands: list[OpenConsultationCommand] = field(default_factory=list)

    def open_consultation(self, command: OpenConsultationCommand) -> ConsultationOutcome:
        self.commands.append(command)
        consultation = Consultation("c1", command.appointment_id, "ABIERTA", NOW, "dolor")
        has_value = self.status in (ConsultationStatus.CREATED, ConsultationStatus.EXISTING)
        return ConsultationOutcome(self.status, consultation if has_value else None)


class Tokens:
    PROFILES = {
        "doctor": AccessProfile(
            "d", "ACTIVO", frozenset({"MEDICO"}), frozenset({"consultas.abrir"})
        ),
        "patient": AccessProfile(
            "p", "ACTIVO", frozenset({"PACIENTE"}), frozenset({"citas.reservar"})
        ),
    }

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self.PROFILES:
            raise Unauthenticated("Authentication required")
        return self.PROFILES[token]


def _client(store: ConsultStore) -> FlaskClient:
    app = Flask("test-consultation")
    app.register_blueprint(scheduling_bp)
    app.extensions["medicalbooking.identity"] = Tokens()
    app.extensions["medicalbooking.scheduling"] = SchedulingService(
        store=store, clock=lambda: NOW, hold_minutes=15
    )

    @app.errorhandler(AppError)
    def _error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status}), error.status

    return app.test_client()


def _post(client: FlaskClient, token: str):
    return client.post(PATH, headers={"Authorization": f"Bearer {token}"})


def test_first_open_is_201_and_a_repeat_is_200() -> None:
    store = ConsultStore()
    client = _client(store)

    created = _post(client, "doctor")
    store.status = ConsultationStatus.EXISTING
    repeated = _post(client, "doctor")

    assert (created.status_code, repeated.status_code) == (201, 200)
    assert created.get_json() == {
        "id": "c1",
        "cita_id": CITA,
        "estado": "ABIERTA",
        "inicio_atencion": NOW.isoformat(),
        "motivo_consulta": "dolor",
    }


def test_requires_authentication_and_the_permission() -> None:
    store = ConsultStore()
    client = _client(store)

    assert client.post(PATH).status_code == 401
    assert _post(client, "patient").status_code == 403
    assert store.commands == []


def test_missing_or_foreign_appointment_is_403_and_a_wrong_state_is_409() -> None:
    store = ConsultStore(status=ConsultationStatus.NOT_FOUND_OR_FORBIDDEN)
    client = _client(store)
    assert _post(client, "doctor").status_code == 403

    store.status = ConsultationStatus.STATE_NOT_ALLOWED
    assert _post(client, "doctor").status_code == 409


@pytest.mark.parametrize("state", ["CONFIRMADA", "COMPLETADA"])
def test_the_store_receives_the_one_allowed_states_constant(state: str) -> None:
    store = ConsultStore()

    _post(_client(store), "doctor")

    assert state in store.commands[0].allowed_states
    assert store.commands[0].allowed_states == {"CONFIRMADA", "COMPLETADA"}
    assert store.commands[0].actor_user_id == "d"
