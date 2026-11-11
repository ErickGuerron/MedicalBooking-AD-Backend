"""Knowledge listings (feature doc S3, S4) against real SQL Server.

Roles come from the seed: MEDICO validates, ADMINISTRADOR administers, LABORATORISTA only reads.
Each test creates its own analyte and rule codes, so the seeded knowledge is never changed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_clinical_api import _analyte, _draft, _validate
from tests.integration.test_lab_api import _doctor
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _rule_body(analito: str, codigo: str) -> dict:
    return {
        "codigo": codigo,
        "nombre": "Regla de listado de prueba",
        "tipo": "ALERTA",
        "expresion_logica": "TODOS",
        "mensaje": "Revisar el resultado",
        "prioridad": "INFORMATIVA",
        "criterios": [{"analito_id": analito, "operador": "LT", "umbral": 12.0, "unidad": "g/dL"}],
    }


def _rule(app, engine, *, validated: bool) -> str:
    """Create a rule draft with its own analyte; validate it when asked. Returns its code."""
    analito = _analyte(app, engine, unidad="g/dL")
    codigo = f"LISTA_{uuid.uuid4().hex[:12].upper()}"
    created = app.test_client().post(
        "/api/v1/knowledge/rules",
        json=_rule_body(analito, codigo),
        headers=_token(app, _user(engine, "ADMINISTRADOR")),
    )
    assert created.status_code == 201, created.get_json()
    if validated:
        validated_response = app.test_client().post(
            f"/api/v1/knowledge/rules/{created.get_json()['id']}/validate",
            headers=_token(app, _doctor(engine)),
        )
        assert validated_response.status_code == 200, validated_response.get_json()
    return codigo


def _list_rules(app, headers, query: str = ""):
    return app.test_client().get(f"/api/v1/knowledge/rules{query}", headers=headers)


# ---------- S3: rules ----------


def test_a_reader_lists_only_validated_rules(app, engine) -> None:
    validated = _rule(app, engine, validated=True)
    draft = _rule(app, engine, validated=False)

    response = _list_rules(app, _token(app, _user(engine, "LABORATORISTA")))

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert {item["estado"] for item in items} == {"VALIDADA"}
    codes = [item["codigo"] for item in items]
    assert validated in codes
    assert draft not in codes


def test_a_validator_lists_drafts_with_their_state(app, engine) -> None:
    draft = _rule(app, engine, validated=False)

    response = _list_rules(app, _token(app, _doctor(engine)), "?estado=BORRADOR")

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert {item["estado"] for item in items} == {"BORRADOR"}
    assert draft in [item["codigo"] for item in items]


def test_an_administrator_lists_drafts_too(app, engine) -> None:
    draft = _rule(app, engine, validated=False)

    response = _list_rules(app, _token(app, _user(engine, "ADMINISTRADOR")), "?estado=BORRADOR")

    assert draft in [item["codigo"] for item in response.get_json()["items"]]


def test_a_reader_asking_for_drafts_is_refused(app, engine) -> None:
    _rule(app, engine, validated=False)

    response = _list_rules(app, _token(app, _user(engine, "LABORATORISTA")), "?estado=BORRADOR")

    assert response.status_code == 403


def test_an_unknown_rule_state_is_422(app, engine) -> None:
    response = _list_rules(app, _token(app, _doctor(engine)), "?estado=ARCHIVADA")

    assert response.status_code == 422


def test_a_patient_cannot_list_rules(app, engine) -> None:
    response = _list_rules(app, _token(app, _user(engine, "PACIENTE")))

    assert response.status_code == 403


def test_a_rule_row_carries_its_criteria_and_state(app, engine) -> None:
    validated = _rule(app, engine, validated=True)

    items = _list_rules(app, _token(app, _user(engine, "LABORATORISTA"))).get_json()["items"]
    row = next(item for item in items if item["codigo"] == validated)

    assert row["estado"] == "VALIDADA"
    assert row["version"] == 1
    criterion = row["criterios"][0]
    assert (criterion["operador"], criterion["umbral"], criterion["unidad"]) == (
        "LT",
        12.0,
        "g/dL",
    )
    assert criterion["analito_id"]


def test_creating_a_rule_still_answers_201_as_a_draft(app, engine) -> None:
    """PRESERVE: the existing POST /knowledge/rules keeps its response."""
    analito = _analyte(app, engine, unidad="g/dL")

    response = app.test_client().post(
        "/api/v1/knowledge/rules",
        json=_rule_body(analito, f"LISTA_{uuid.uuid4().hex[:12].upper()}"),
        headers=_token(app, _user(engine, "ADMINISTRADOR")),
    )

    assert response.status_code == 201
    assert response.get_json()["estado"] == "BORRADOR"


# ---------- S4: references ----------


def _list_references(app, headers, query: str):
    return app.test_client().get(f"/api/v1/knowledge/references{query}", headers=headers)


def test_a_validator_lists_the_draft_references_of_one_analyte(app, engine) -> None:
    analito = _analyte(app, engine)
    draft = _draft(app, engine, analito)
    other = _draft(app, engine, _analyte(app, engine))

    response = _list_references(
        app, _token(app, _doctor(engine)), f"?analito_id={analito}&estado=BORRADOR"
    )

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert [(i["id"], i["estado"], i["analito_id"]) for i in items] == [
        (draft, "BORRADOR", analito)
    ]
    assert other not in [i["id"] for i in items]
    assert {"unidad", "minimo", "maximo", "fecha_inicio", "fecha_fin"} <= set(items[0])


def test_an_administrator_lists_drafts_of_an_analyte_too(app, engine) -> None:
    analito = _analyte(app, engine)
    draft = _draft(app, engine, analito)

    response = _list_references(
        app,
        _token(app, _user(engine, "ADMINISTRADOR")),
        f"?analito_id={analito}&estado=BORRADOR",
    )

    assert draft in [i["id"] for i in response.get_json()["items"]]


def test_the_validated_references_of_an_analyte_show_their_state(app, engine) -> None:
    analito = _analyte(app, engine)
    draft = _draft(app, engine, analito)
    assert _validate(app, engine, draft) == 200

    response = _list_references(
        app, _token(app, _doctor(engine)), f"?analito_id={analito}&estado=VALIDADA"
    )

    assert [(i["id"], i["estado"]) for i in response.get_json()["items"]] == [(draft, "VALIDADA")]


def test_drafts_without_an_analyte_are_422(app, engine) -> None:
    response = _list_references(app, _token(app, _doctor(engine)), "?estado=BORRADOR")

    assert response.status_code == 422


def test_a_reader_cannot_list_references(app, engine) -> None:
    analito = _analyte(app, engine)
    _draft(app, engine, analito)

    response = _list_references(
        app,
        _token(app, _user(engine, "LABORATORISTA")),
        f"?analito_id={analito}&estado=BORRADOR",
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "query",
    ["?analito_id=no-es-uuid", "?estado=ARCHIVADA", "?analito_id="],
    ids=["bad-analyte-id", "unknown-state", "empty-analyte-id"],
)
def test_invalid_reference_filters_are_422(app, engine, query: str) -> None:
    response = _list_references(app, _token(app, _doctor(engine)), query)

    assert response.status_code == 422


def test_the_analyte_references_route_still_returns_only_validated_ranges(app, engine) -> None:
    """PRESERVE: GET /knowledge/analytes/{id}/references is unchanged by the new listing."""
    analito = _analyte(app, engine)
    draft = _draft(app, engine, analito)

    response = app.test_client().get(
        f"/api/v1/knowledge/analytes/{analito}/references",
        headers=_token(app, _doctor(engine)),
    )

    assert response.status_code == 200
    assert draft not in [i["id"] for i in response.get_json()["items"]]
