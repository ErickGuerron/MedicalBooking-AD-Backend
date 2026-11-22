"""Administrator reports and monitoring against real SQL Server: access by permission, read-only
answers and period validation. Every user is created with a unique synthetic identifier."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
SUMMARY = "/api/v1/admin/reportes/resumen"
MONITORING = "/api/v1/admin/monitoreo/estado"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def test_administrator_reads_the_summary_with_amounts_as_text(app: Flask, engine: Engine) -> None:
    headers = _token(app, _user(engine, "ADMINISTRADOR"))
    response = app.test_client().get(
        SUMMARY, query_string={"desde": "2026-01-01", "hasta": "2026-01-31"}, headers=headers
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["desde"] == "2026-01-01"
    assert body["hasta"] == "2026-01-31"
    for key in ("citas", "ordenes_laboratorio", "pagos", "tratamientos"):
        assert isinstance(body[key], list)
    for payment in body["pagos"]:
        assert isinstance(payment["monto"], str)
        assert payment["monto"] == f"{float(payment['monto']):.2f}"


def test_administrator_reads_the_monitoring_snapshot(app: Flask, engine: Engine) -> None:
    headers = _token(app, _user(engine, "ADMINISTRADOR"))
    response = app.test_client().get(MONITORING, headers=headers)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["base_principal"]["estado"] == "DISPONIBLE"
    assert body["base_principal"]["latencia_ms"] >= 0
    assert body["revisado_en"]
    for destination in body["replicacion"]:
        assert destination["codigo"]
        assert isinstance(destination["entregas"], list)


def test_patient_is_refused_both_reports(app: Flask, engine: Engine) -> None:
    headers = _token(app, _user(engine, "PACIENTE"))
    client = app.test_client()
    summary = client.get(
        SUMMARY, query_string={"desde": "2026-01-01", "hasta": "2026-01-31"}, headers=headers
    )
    monitoring = client.get(MONITORING, headers=headers)
    assert summary.status_code == 403
    assert monitoring.status_code == 403


def test_summary_refuses_a_period_that_ends_before_it_starts(app: Flask, engine: Engine) -> None:
    headers = _token(app, _user(engine, "ADMINISTRADOR"))
    response = app.test_client().get(
        SUMMARY, query_string={"desde": "2026-02-01", "hasta": "2026-01-01"}, headers=headers
    )
    assert response.status_code == 422


def test_summary_refuses_a_missing_or_malformed_date(app: Flask, engine: Engine) -> None:
    headers = _token(app, _user(engine, "ADMINISTRADOR"))
    client = app.test_client()
    missing = client.get(SUMMARY, query_string={"desde": "2026-01-01"}, headers=headers)
    malformed = client.get(
        SUMMARY, query_string={"desde": "01/01/2026", "hasta": "2026-01-31"}, headers=headers
    )
    assert missing.status_code == 422
    assert malformed.status_code == 422
