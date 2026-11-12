"""SOAP laboratory status against real SQL Server: authentication, ownership, minimal answer.

The ownership rule is the single-order rule of GET /api/v1/lab/orders/{id}, reused through the
laboratory service. Missing, foreign and malformed order ids must produce byte-identical faults,
so the response does not reveal which orders exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import defusedxml.ElementTree as ET
import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import HB, _detail, _order_for, _result_body
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
ENDPOINT = "/soap/laboratory"
NS_LAB = "urn:medicalbooking:laboratory:v1"
FOREIGN_FAULT = "Order not available for this account"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Iterator[Flask]:
    documents = tmp_path_factory.mktemp("soap-lab-documents")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _envelope(order_id: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        f'<lab:GetLaboratoryResultStatus xmlns:lab="{NS_LAB}">'
        f"<lab:orderId>{order_id}</lab:orderId>"
        "</lab:GetLaboratoryResultStatus>"
        "</soap:Body></soap:Envelope>"
    ).encode()


def _post(app: Flask, order_id: str, headers: dict[str, str] | None = None):
    return app.test_client().post(
        ENDPOINT,
        data=_envelope(order_id),
        headers={"Content-Type": "text/xml", **(headers or {})},
    )


def _fault_text(response) -> str | None:
    root = ET.fromstring(response.data)
    for element in root.iter():
        if element.tag.endswith("faultstring"):
            return element.text
    return None


def test_missing_token_gets_401_fault(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)

    response = _post(app, order_id)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert _fault_text(response) == "Authentication required"


def test_forged_token_gets_401_fault(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)

    response = _post(app, order_id, {"Authorization": "Bearer not.a.real.token"})

    assert response.status_code == 401
    assert _fault_text(response) == "Authentication required"


def test_patient_owner_gets_only_minimal_status(app, engine) -> None:
    order_id, patient, doctor, doctor_headers = _order_for(app, engine)
    client = app.test_client()
    lab_headers = _token(app, _user(engine, "LABORATORISTA"))
    detail = _detail(app, order_id, doctor_headers)
    client.post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_result_body(detail, value=13.5, analito=HB),
        headers=lab_headers,
    )

    response = _post(app, order_id, _token(app, patient))

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    # Analyte names, values and units never appear in the SOAP answer.
    assert "Hemoglobina" not in text
    assert "13.5" not in text
    assert "g/dL" not in text
    root = ET.fromstring(response.data)
    reply = next(
        e for e in root.iter() if e.tag == f"{{{NS_LAB}}}GetLaboratoryResultStatusResponse"
    )
    assert [child.tag.split("}")[-1] for child in reply] == ["orderId", "status", "timestamp"]
    assert reply[0].text == order_id
    assert reply[1].text == "COMPLETADA"
    assert reply[2].text.endswith("Z")


def test_ordering_doctor_gets_the_status(app, engine) -> None:
    order_id, _, _, doctor_headers = _order_for(app, engine)

    response = _post(app, order_id, doctor_headers)

    assert response.status_code == 200
    assert "SOLICITADA" in response.get_data(as_text=True)


def test_foreign_patient_gets_the_forbidden_fault(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)
    stranger = _user(engine, "PACIENTE")

    response = _post(app, order_id, _token(app, stranger))

    assert response.status_code == 403
    assert _fault_text(response) == FOREIGN_FAULT


def test_missing_order_gets_the_same_fault_bytes_as_a_foreign_order(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)
    stranger_headers = _token(app, _user(engine, "PACIENTE"))

    foreign = _post(app, order_id, stranger_headers)
    missing = _post(app, str(uuid.uuid4()), stranger_headers)

    assert missing.status_code == foreign.status_code == 403
    assert missing.data == foreign.data


def test_malformed_order_id_gets_the_same_fault_bytes(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)
    stranger_headers = _token(app, _user(engine, "PACIENTE"))

    foreign = _post(app, order_id, stranger_headers)
    malformed = _post(app, "not-a-uuid", stranger_headers)

    assert malformed.status_code == foreign.status_code == 403
    assert malformed.data == foreign.data


def test_rest_single_order_read_keeps_its_forbidden_answer(app, engine) -> None:
    """PRESERVE: the REST read shares the ownership rule with SOAP and must not change."""
    order_id, _, _, _ = _order_for(app, engine)
    stranger_headers = _token(app, _user(engine, "PACIENTE"))

    response = app.test_client().get(f"/api/v1/lab/orders/{order_id}", headers=stranger_headers)

    assert response.status_code == 403
    assert response.get_json()["detail"] == "Access to this resource is not allowed"
