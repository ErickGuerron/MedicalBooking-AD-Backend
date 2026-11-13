"""The order-created outbox event carries the replica snapshot of the order (unit 3).

The replica cannot ask SQL Server during an outage, so the event must carry the rows it
projects: the order and its detail lines. Free-text observations are never projected.
"""

import json

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings

from tests.integration.test_lab_api import _create_order, _doctor, _patient_id, _test_type
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings):
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _created_event(engine, order_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT payload, version_agregado FROM eventos_outbox "
                "WHERE agregado_tipo = 'orden_laboratorio' AND agregado_id = :a "
                "AND tipo_evento = 'orden_laboratorio.creada'"
            ),
            {"a": order_id},
        ).one()
    body = json.loads(row.payload)
    body["_version"] = row.version_agregado
    return body


def test_the_created_event_carries_the_order_and_its_detail_lines(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    doctor = _doctor(engine)
    tests = [_test_type(engine, "HEMO-SINT")]
    response = _create_order(
        app.test_client(),
        _token(app, doctor),
        _patient_id(engine, patient),
        tests,
    )
    assert response.status_code == 201, response.get_json()
    order_id = response.get_json()["id"]

    event = _created_event(engine, order_id)

    assert event["_version"] == 1
    orden = event["orden"]
    assert orden["id"] == order_id
    assert orden["paciente_id"] == _patient_id(engine, patient)
    assert orden["estado"] == "SOLICITADA"
    assert orden["prioridad"] == "NORMAL"
    assert len(orden["detalles"]) == 1
    detalle = orden["detalles"][0]
    assert detalle["tipo_prueba_id"] == tests[0]
    assert detalle["estado"] == "PENDIENTE"


def test_free_text_observations_are_never_projected(app, engine) -> None:
    patient = _user(engine, "PACIENTE")
    doctor = _doctor(engine)
    response = app.test_client().post(
        "/api/v1/lab/orders",
        json={
            "paciente_id": _patient_id(engine, patient),
            "tipo_prueba_ids": [_test_type(engine, "HEMO-SINT")],
            "observaciones": "nota clinica privada",
        },
        headers=_token(app, doctor),
    )
    assert response.status_code == 201, response.get_json()

    event = _created_event(engine, response.get_json()["id"])

    assert "observaciones" not in event["orden"]
    assert "nota clinica privada" not in json.dumps(event)
