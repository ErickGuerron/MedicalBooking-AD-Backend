"""GET /availability keeps its fields and adds the optional doctor name and specialty filter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask, jsonify
from flask.testing import FlaskClient

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.ports import AvailableSlot
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.presentation.routes import scheduling_bp
from medicalbooking.shared.domain.errors import AppError
from tests.unit.test_scheduling_service import FakeStore

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
MEDICO = "33333333-3333-4333-8333-333333333333"
PATH = "/api/v1/availability?desde=2026-11-11T00:00:00%2B00:00&hasta=2026-11-12T00:00:00%2B00:00"


@dataclass
class NamedStore(FakeStore):
    calls: list[dict] = field(default_factory=list)
    named: bool = True

    def list_available(self, start, end, medico_id, limit, especialidad_id=None):
        self.calls.append({"medico_id": medico_id, "especialidad_id": especialidad_id})
        begin = NOW + timedelta(days=1)
        return [
            AvailableSlot(
                horario_id="h1",
                medico_id=MEDICO,
                inicio_utc=begin,
                fin_utc=begin + timedelta(minutes=30),
                medico_nombre="Ana Perez" if self.named else None,
            )
        ]


class Tokens:
    def authenticate_token(self, token: str) -> AccessProfile:
        return AccessProfile(
            user_id="p",
            status="ACTIVO",
            roles=frozenset(),
            permissions=frozenset({"citas.reservar"}),
        )


def _client(store: NamedStore) -> FlaskClient:
    app = Flask("test-availability")
    app.register_blueprint(scheduling_bp)
    app.extensions["medicalbooking.identity"] = Tokens()
    app.extensions["medicalbooking.scheduling"] = SchedulingService(
        store=store, clock=lambda: NOW, hold_minutes=15
    )

    @app.errorhandler(AppError)
    def _error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status}), error.status

    return app.test_client()


HEADERS = {"Authorization": "Bearer t"}


def test_slot_keeps_every_existing_field_and_adds_the_doctor() -> None:
    item = _client(NamedStore()).get(PATH, headers=HEADERS).get_json()["items"][0]

    assert set(item) == {"horario_id", "medico_id", "inicio_utc", "fin_utc", "medico"}
    assert item["medico"] == {"id": MEDICO, "nombre": "Ana Perez"}
    assert item["medico_id"] == MEDICO


def test_doctor_is_omitted_when_the_adapter_has_no_name() -> None:
    item = _client(NamedStore(named=False)).get(PATH, headers=HEADERS).get_json()["items"][0]

    assert "medico" not in item


def test_filters_reach_the_store_and_default_to_none() -> None:
    store = NamedStore()
    client = _client(store)

    client.get(PATH, headers=HEADERS)
    client.get(PATH + f"&medico_id={MEDICO}&especialidad_id=4", headers=HEADERS)

    assert store.calls == [
        {"medico_id": None, "especialidad_id": None},
        {"medico_id": MEDICO, "especialidad_id": 4},
    ]


@pytest.mark.parametrize("value", ["0", "-2", "x", "99999999999"])
def test_invalid_specialty_filter_is_422(value: str) -> None:
    assert (
        _client(NamedStore()).get(PATH + f"&especialidad_id={value}", headers=HEADERS).status_code
        == 422
    )
