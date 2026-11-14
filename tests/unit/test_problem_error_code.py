"""Stable machine-readable error code in the problem envelope.

An AppError with a code adds one `code` key to the problem body. An AppError without a code
keeps the exact body every other endpoint already returns.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from flask import Flask

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.use_cases import LabService
from medicalbooking.shared.domain.errors import Forbidden
from tests.unit.test_lab_service import HEMO, MEDICO, PACIENTE, FakeStorage, FakeStore, _actor

ORDERS_URL = "/api/v1/lab/orders"
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class _Tokens:
    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self._profiles = profiles

    def authenticate_token(self, token: str) -> AccessProfile:
        return self._profiles[token]


@pytest.fixture
def app() -> Flask:
    # Port 1 is closed; the routes below never reach the database.
    settings = Settings(_env_file=None, sqlserver_port=1, sqlserver_database="unused")
    flask_app = create_app(settings)
    flask_app.config["TESTING"] = True
    return flask_app


def _add_raising_route(app: Flask, path: str, error: Forbidden) -> None:
    def view():
        raise error

    app.add_url_rule(path, endpoint=path, view_func=view)


def test_problem_body_carries_the_code_when_the_error_has_one(app) -> None:
    _add_raising_route(app, "/_coded", Forbidden("coded refusal", code="SAMPLE_CODE"))
    correlation_id = str(uuid.uuid4())

    response = app.test_client().get("/_coded", headers={"X-Correlation-ID": correlation_id})

    assert response.status_code == 403
    assert response.mimetype == "application/problem+json"
    assert response.get_json() == {
        "type": "about:blank",
        "title": "Forbidden",
        "status": 403,
        "detail": "coded refusal",
        "correlation_id": correlation_id,
        "code": "SAMPLE_CODE",
    }


def test_problem_body_without_a_code_is_exactly_the_old_shape(app) -> None:
    _add_raising_route(app, "/_plain", Forbidden("plain refusal"))
    correlation_id = str(uuid.uuid4())

    response = app.test_client().get("/_plain", headers={"X-Correlation-ID": correlation_id})

    assert response.status_code == 403
    assert response.get_json() == {
        "type": "about:blank",
        "title": "Forbidden",
        "status": 403,
        "detail": "plain refusal",
        "correlation_id": correlation_id,
    }
    assert "code" not in response.get_json()


def test_a_plain_app_error_has_no_code_attribute_value(app) -> None:
    assert Forbidden("plain").code is None


# ---------- (b) unknown and unrelated patients are indistinguishable ----------


def _lab_app(app: Flask) -> FakeStore:
    store = FakeStore()
    doctor = _actor(*MEDICO)
    store.medico_by_user[doctor.user_id] = str(uuid.uuid4())
    app.extensions["medicalbooking.laboratory"] = LabService(
        store=store, storage=FakeStorage(), clock=lambda: NOW
    )
    app.extensions["medicalbooking.identity"] = _Tokens({"tok-doctor": doctor})
    return store


def _post_order(app: Flask, patient_id: str):
    return app.test_client().post(
        ORDERS_URL,
        json={"paciente_id": patient_id, "tipo_prueba_ids": [HEMO]},
        headers={"Authorization": "Bearer tok-doctor"},
    )


def test_unknown_and_unrelated_patients_render_identical_problem_bodies(app) -> None:
    store = _lab_app(app)
    other = _actor(*PACIENTE)
    unrelated_id = str(uuid.uuid4())
    store.patient_by_user[other.user_id] = unrelated_id
    unknown_id = str(uuid.uuid4())

    unknown = _post_order(app, unknown_id)
    unrelated = _post_order(app, unrelated_id)

    assert unknown.status_code == unrelated.status_code == 403
    unknown_body = json.loads(unknown.get_data(as_text=True))
    unrelated_body = json.loads(unrelated.get_data(as_text=True))
    assert unknown_body.pop("correlation_id") != ""
    assert unrelated_body.pop("correlation_id") != ""
    assert unknown_body == unrelated_body
    assert unknown_body == {
        "type": "about:blank",
        "title": "Forbidden",
        "status": 403,
        "detail": "The patient is not available to this doctor",
        "code": "PATIENT_NOT_AVAILABLE",
    }
    assert store.orders == {}
