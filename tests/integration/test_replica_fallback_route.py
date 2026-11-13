"""GET /api/v1/me/resultados-contingencia: the patient's results during a SQL Server outage.

The route is authenticated with the normal JWT and delegates to the fallback service. The
service is replaced by a recording fake here, so these tests check the HTTP contract, the
error mapping and the serialization; the replica and audit paths are covered by their own
tests.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.replica.application.fallback_read import (
    AccessDenied,
    AuditUnavailable,
    PatientResults,
)
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
ROUTE = "/api/v1/me/resultados-contingencia"


class FakeFallback:
    def __init__(self, outcome=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._outcome = outcome
        self._error = error

    def results_for(self, actor_id: UUID, correlation_id: UUID, ip: str | None):
        self.calls.append({"actor_id": actor_id, "correlation_id": correlation_id, "ip": ip})
        if self._error is not None:
            raise self._error
        return self._outcome


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings):
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


def _with(app: Flask, service) -> None:
    app.extensions["medicalbooking.fallback_read"] = service


def _paciente_token(app: Flask, engine: Engine) -> tuple[str, str]:
    user = _user(engine, "PACIENTE")
    return _token(app, user)["Authorization"].split(" ", 1)[1], user


def test_without_a_token_the_route_requires_authentication(app):
    response = app.test_client().get(ROUTE)

    assert response.status_code == 401


def test_an_authorized_patient_gets_orders_with_results_as_json(app, engine: Engine):
    patient = uuid.uuid4()
    order = {
        "id": str(uuid.uuid4()),
        "fecha_orden": datetime(2026, 11, 12, 18, 0, tzinfo=UTC),
        "estado": "COMPLETADA",
        "resultados": [
            {
                "id": str(uuid.uuid4()),
                "analito": "Hemoglobina",
                "valor_numerico": Decimal("14.200000"),
                "unidad": "g/dL",
                "validado_en": datetime(2026, 11, 12, 18, 5, tzinfo=UTC),
            }
        ],
        "diagnostico": None,
    }
    fake = FakeFallback(outcome=PatientResults(patient_id=patient, orders=(order,)))
    _with(app, fake)
    headers = _paciente_token(app, engine)[0]

    response = app.test_client().get(ROUTE, headers={"Authorization": f"Bearer {headers}"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["paciente_id"] == str(patient)
    assert body["fuente"] == "replica"
    resultado = body["ordenes"][0]["resultados"][0]
    assert resultado["valor_numerico"] == 14.2
    assert resultado["validado_en"].startswith("2026-11-12T18:05")


def test_the_service_receives_the_account_of_the_token_and_the_client_address(app, engine):
    fake = FakeFallback(outcome=PatientResults(patient_id=uuid.uuid4(), orders=()))
    _with(app, fake)
    token, user = _paciente_token(app, engine)

    app.test_client().get(
        ROUTE,
        headers={"Authorization": f"Bearer {token}"},
        environ_overrides={"REMOTE_ADDR": "10.0.0.7"},
    )

    assert fake.calls[0]["actor_id"] == UUID(str(user))
    assert fake.calls[0]["ip"] == "10.0.0.7"
    assert isinstance(fake.calls[0]["correlation_id"], UUID)


def test_a_denied_account_gets_403_with_no_data(app, engine):
    _with(app, FakeFallback(error=AccessDenied("no own-results permission")))
    token, _ = _paciente_token(app, engine)

    response = app.test_client().get(ROUTE, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.mimetype == "application/problem+json"
    assert "ordenes" not in response.get_json()


def test_an_audit_that_cannot_be_written_gets_503(app, engine):
    _with(app, FakeFallback(error=AuditUnavailable("audit store down")))
    token, _ = _paciente_token(app, engine)

    response = app.test_client().get(ROUTE, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert "ordenes" not in response.get_json()


def test_without_a_configured_replica_the_route_answers_503(app, engine):
    _with(app, None)
    token, _ = _paciente_token(app, engine)

    response = app.test_client().get(ROUTE, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
