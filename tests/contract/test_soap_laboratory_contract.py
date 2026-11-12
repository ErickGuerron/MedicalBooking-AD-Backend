"""Contract (S2): an independent consumer calls the WSDL and the operation over real HTTP.

The server runs on a loopback socket in a thread. The consumer in soap_consumer.py imports no
server code: it discovers the operation and address from the WSDL, then checks a 401 without a
token and an authorized success with the minimal answer.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine
from werkzeug.serving import make_server

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.contract.soap_consumer import LaboratoryConsumer
from tests.integration.test_lab_api import _order_for
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
NAMESPACE = "urn:medicalbooking:laboratory:v1"


@pytest.fixture(scope="module")
def running(
    migrated_engine: Engine, test_settings: Settings, tmp_path_factory
) -> Iterator[tuple[Flask, str]]:
    documents = tmp_path_factory.mktemp("soap-contract-documents")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    app = create_app(settings)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_wsdl_advertises_the_operation_namespace_and_endpoint(running) -> None:
    _, base_url = running

    description = LaboratoryConsumer(base_url).describe()

    assert description.namespace == NAMESPACE
    assert description.operation == "GetLaboratoryResultStatus"
    assert description.address == f"{base_url}/soap/laboratory"


def test_call_without_token_is_refused_with_401_fault(running) -> None:
    app, base_url = running
    consumer = LaboratoryConsumer(base_url)
    description = consumer.describe()
    order_id, _, _, _ = _order_for(app, app.extensions["medicalbooking.engine"])

    response = consumer.get_status(description, order_id, token=None)

    assert response.status_code == 401
    assert consumer.fault_string(response) == "Authentication required"


def test_authorized_owner_gets_only_the_minimal_status(running) -> None:
    app, base_url = running
    engine = app.extensions["medicalbooking.engine"]
    consumer = LaboratoryConsumer(base_url)
    description = consumer.describe()
    order_id, patient, _, _ = _order_for(app, engine)
    bearer = _token(app, patient)["Authorization"].removeprefix("Bearer ")

    response = consumer.get_status(description, order_id, token=bearer)

    assert response.status_code == 200
    reply = consumer.parse_status(response)
    assert reply.order_id == order_id
    assert reply.status == "SOLICITADA"
    assert reply.timestamp.endswith("Z")
    assert reply.field_names == ("orderId", "status", "timestamp")


def test_another_patient_is_refused_with_the_same_fault_as_a_missing_order(running) -> None:
    app, base_url = running
    engine = app.extensions["medicalbooking.engine"]
    consumer = LaboratoryConsumer(base_url)
    description = consumer.describe()
    order_id, _, _, _ = _order_for(app, engine)
    stranger = _token(app, _user(engine, "PACIENTE"))["Authorization"].removeprefix("Bearer ")

    foreign = consumer.get_status(description, order_id, token=stranger)
    missing = consumer.get_status(description, "00000000-0000-4000-8000-000000000000", stranger)

    assert foreign.status_code == missing.status_code == 403
    assert foreign.content == missing.content
