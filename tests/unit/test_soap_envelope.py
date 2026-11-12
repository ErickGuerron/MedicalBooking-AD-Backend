"""SOAP laboratory endpoint: WSDL, envelope parsing and fault shapes, through HTTP.

The blueprint runs against fake identity and status services, so transport and parsing
rules are tested without a database. Ownership rules run against real SQL Server in
tests/integration/test_soap_laboratory_api.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import defusedxml.ElementTree as ET
import pytest
from flask import Flask

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.interoperability.presentation.soap import soap_bp
from medicalbooking.shared.domain.errors import Unauthenticated

VALID_TOKEN = "valid-test-token"  # noqa: S105 - synthetic token for the fake identity guard
ORDER_ID = str(uuid.UUID("9b1e0c5a-2f4d-4e7a-9c8b-1a2b3c4d5e6f"))
ENVELOPE_NS = "http://schemas.xmlsoap.org/soap/envelope/"


def _envelope(body_inner: str = "", order_id: str = ORDER_ID) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{ENVELOPE_NS}">'
        "<soap:Body>"
        '<lab:GetLaboratoryResultStatus xmlns:lab="urn:medicalbooking:laboratory:v1">'
        f"<lab:orderId>{order_id}</lab:orderId>{body_inner}"
        "</lab:GetLaboratoryResultStatus>"
        "</soap:Body>"
        "</soap:Envelope>"
    ).encode()


class FakeIdentity:
    """Stands in for IdentityService.authenticate_token: one valid token, anything else 401."""

    def authenticate_token(self, token: str) -> AccessProfile:
        if token != VALID_TOKEN:
            raise Unauthenticated("Authentication required")
        return AccessProfile(
            user_id="user-1",
            status="ACTIVO",
            roles=frozenset({"PACIENTE"}),
            permissions=frozenset({"pacientes.leer_propio"}),
        )


class FakeStatusService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def status_of(self, actor: AccessProfile, order_id: str) -> Any:
        self.calls.append((actor.user_id, order_id))
        checked_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        return SimpleNamespace(order_id=order_id, status="SOLICITADA", checked_at=checked_at)


@pytest.fixture
def status_service() -> FakeStatusService:
    return FakeStatusService()


@pytest.fixture
def client(status_service: FakeStatusService):
    app = Flask("test-soap")
    app.extensions["medicalbooking.identity"] = FakeIdentity()
    app.extensions["medicalbooking.laboratory_status"] = status_service
    app.register_blueprint(soap_bp)
    return app.test_client()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {VALID_TOKEN}", "Content-Type": "text/xml"}


def _local_names(root: ET.Element) -> set[str]:
    return {element.tag.split("}")[-1] for element in root.iter()}


def _text(root: ET.Element, local_name: str) -> str | None:
    for element in root.iter():
        if element.tag.split("}")[-1] == local_name:
            return element.text
    return None


def test_wsdl_is_served_with_the_operation_and_namespace(client) -> None:
    response = client.get("/soap/laboratory?wsdl")

    assert response.status_code == 200
    assert response.mimetype == "text/xml"
    root = ET.fromstring(response.data)
    assert root.get("targetNamespace") == "urn:medicalbooking:laboratory:v1"
    operation_names = {element.get("name") for element in root.iter()}
    assert "GetLaboratoryResultStatus" in operation_names
    assert "orderId" in operation_names


def test_valid_envelope_passes_the_order_id_and_answers_only_minimal_fields(
    client, status_service
) -> None:
    response = client.post("/soap/laboratory", data=_envelope(), headers=_auth())

    assert response.status_code == 200
    assert status_service.calls == [("user-1", ORDER_ID)]
    root = ET.fromstring(response.data)
    assert _text(root, "orderId") == ORDER_ID
    assert _text(root, "status") == "SOLICITADA"
    assert {"orderId", "status", "timestamp"} <= _local_names(root)


def test_missing_bearer_token_returns_401_soap_fault(client, status_service) -> None:
    response = client.post(
        "/soap/laboratory", data=_envelope(), headers={"Content-Type": "text/xml"}
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    root = ET.fromstring(response.data)
    assert "Fault" in _local_names(root)
    assert status_service.calls == []


def test_token_inside_the_soap_body_never_authenticates(client, status_service) -> None:
    body = _envelope(body_inner=f"<lab:token>{VALID_TOKEN}</lab:token>")

    response = client.post("/soap/laboratory", data=body, headers={"Content-Type": "text/xml"})

    assert response.status_code == 401
    assert status_service.calls == []


def test_doctype_is_rejected_before_the_operation_runs(client, status_service) -> None:
    body = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE soap [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
        b"<soap:Body><lab:GetLaboratoryResultStatus "
        b"xmlns:lab='urn:medicalbooking:laboratory:v1'>"
        b"<lab:orderId>&xxe;</lab:orderId></lab:GetLaboratoryResultStatus>"
        b"</soap:Body></soap:Envelope>"
    )

    response = client.post("/soap/laboratory", data=body, headers=_auth())

    assert response.status_code == 400
    assert "Fault" in _local_names(ET.fromstring(response.data))
    assert status_service.calls == []


def test_unknown_operation_is_a_client_fault(client, status_service) -> None:
    body = _envelope().replace(b"GetLaboratoryResultStatus", b"DeleteEverything")

    response = client.post("/soap/laboratory", data=body, headers=_auth())

    assert response.status_code == 400
    assert status_service.calls == []
