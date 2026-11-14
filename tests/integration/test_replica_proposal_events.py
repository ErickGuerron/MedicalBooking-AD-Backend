"""The physician review reaches the replica with the content it may show (unit 3).

ACEPTAR and CORREGIR project the proposal and its findings. RECHAZAR projects only the
state, with no content, so the replica removes anything it holds. The physician note is
never projected.
"""

import json

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_clinical_api import KEY, _pending_proposal

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings):
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _review_event(engine: Engine, proposal_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT payload FROM eventos_outbox "
                "WHERE agregado_tipo = 'propuesta_clinica' AND agregado_id = :p "
                "AND tipo_evento = 'propuesta.revisada'"
            ),
            {"p": proposal_id},
        ).one()
    return json.loads(row.payload)


def _stored_findings(engine: Engine, proposal_id: str) -> list[tuple]:
    with engine.connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                text(
                    "SELECT id, tipo, explicacion, regla_version FROM propuestas_hallazgos "
                    "WHERE propuesta_id = :p ORDER BY id"
                ),
                {"p": proposal_id},
            ).all()
        ]


def test_accepting_projects_the_proposal_and_its_findings(app, engine) -> None:
    proposal_id, order_id, doctor = _pending_proposal(app, engine)
    response = app.test_client().post(
        f"/api/v1/clinical/proposals/{proposal_id}/reviews",
        json={"decision": "ACEPTAR"},
        headers=doctor,
    )
    assert response.status_code == 201, response.get_json()

    event = _review_event(engine, proposal_id)

    propuesta = event["propuesta"]
    assert propuesta["id"] == proposal_id
    assert propuesta["orden_id"] == order_id
    assert propuesta["estado"] == "ACEPTADA"
    assert propuesta["resumen_propuesto"]
    expected = _stored_findings(engine, proposal_id)
    assert len(event["hallazgos"]) == len(expected)
    for item, (_id, tipo, explicacion, version) in zip(event["hallazgos"], expected, strict=True):
        assert item["tipo"] == tipo
        assert item["explicacion"] == explicacion
        assert item["regla_version"] == version


def test_rejecting_projects_only_the_state_and_no_content(app, engine) -> None:
    proposal_id, _order_id, doctor = _pending_proposal(app, engine)
    response = app.test_client().post(
        f"/api/v1/clinical/proposals/{proposal_id}/reviews",
        json={"decision": "RECHAZAR", "motivo": "Datos insuficientes"},
        headers=doctor,
    )
    assert response.status_code == 201, response.get_json()

    event = _review_event(engine, proposal_id)

    assert event["propuesta"]["estado"] == "RECHAZADA"
    assert event["propuesta"]["resumen_propuesto"] is None
    assert event["propuesta"]["conclusion_medica"] is None
    assert event["hallazgos"] == []
    assert "Datos insuficientes" not in json.dumps(event)


def test_the_physician_note_is_never_projected(app, engine) -> None:
    proposal_id, _order_id, doctor = _pending_proposal(app, engine)
    app.test_client().post(
        f"/api/v1/clinical/proposals/{proposal_id}/reviews",
        json={"decision": "ACEPTAR"},
        headers=doctor,
    )

    serialized = json.dumps(_review_event(engine, proposal_id))

    assert "observacion_medica" not in serialized
