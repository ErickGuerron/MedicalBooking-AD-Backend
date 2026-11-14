"""The result-registered outbox event carries the validated results for the replica (unit 3).

The replica shows results without asking SQL Server, so the event carries the rows it
projects: the result values and their evidence, the detail line state and the order state.
The id of the staff member who validated the result is never projected.
"""

import json

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import _detail, _order_for, _result_body
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


def _result_event(engine: Engine, order_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT TOP 1 payload FROM eventos_outbox "
                "WHERE agregado_tipo = 'orden_laboratorio' AND agregado_id = :a "
                "AND tipo_evento = 'resultado_laboratorio.registrado' "
                "ORDER BY version_agregado DESC"
            ),
            {"a": order_id},
        ).one()
    return json.loads(row.payload)


def test_the_event_carries_the_validated_result_values_and_evidence(app, engine) -> None:
    order_id, _patient, _doctor, doctor_headers = _order_for(app, engine)
    detail_id = _detail(app, order_id, doctor_headers)
    lab_headers = _token(app, _user(engine, "LABORATORISTA"))

    response = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_result_body(detail_id, 14.2),
        headers=lab_headers,
    )
    assert response.status_code in (200, 201), response.get_json()

    event = _result_event(engine, order_id)

    assert event["detalle_id"] == detail_id
    resultados = event["resultados"]
    assert len(resultados) == 1
    result = resultados[0]
    assert result["nombre_analito"] == "Hemoglobina"
    assert result["valor_numerico"] == 14.2
    assert result["unidad"] == "g/dL"
    assert result["id"] and result["validado_en"]


def test_the_event_carries_the_detail_and_order_states_as_stored(app, engine) -> None:
    order_id, _patient, _doctor, doctor_headers = _order_for(app, engine)
    detail_id = _detail(app, order_id, doctor_headers)
    lab_headers = _token(app, _user(engine, "LABORATORISTA"))
    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_result_body(detail_id, 14.2),
        headers=lab_headers,
    )

    event = _result_event(engine, order_id)

    with engine.connect() as conn:
        detail_state = conn.execute(
            text("SELECT estado FROM ordenes_laboratorio_detalle WHERE id = :d"),
            {"d": detail_id},
        ).scalar_one()
        order_state = conn.execute(
            text("SELECT estado FROM ordenes_laboratorio WHERE id = :o"),
            {"o": order_id},
        ).scalar_one()
    assert event["detalle"]["id"] == detail_id
    assert event["detalle"]["estado"] == detail_state
    assert event["orden_estado"] == order_state


def test_the_validator_identity_is_never_projected(app, engine) -> None:
    order_id, _patient, _doctor, doctor_headers = _order_for(app, engine)
    detail_id = _detail(app, order_id, doctor_headers)
    lab = _user(engine, "LABORATORISTA")
    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_result_body(detail_id, 14.2),
        headers=_token(app, lab),
    )

    serialized = json.dumps(_result_event(engine, order_id))

    assert "validado_por" not in serialized
    assert str(lab) not in serialized
