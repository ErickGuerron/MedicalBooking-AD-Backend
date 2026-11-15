"""Confirming a completed lab order: the laboratory closes it, with or without its report.

Against real SQL Server: a completed order can be confirmed once; after that results and reports
are refused, and the order leaves the laboratory queue. An incomplete order cannot be confirmed.
The confirmation is the moment the results email is announced, with the report when there is one.
"""

from __future__ import annotations

import io
import json

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import HB, PDF, _result_body
from tests.integration.test_patient_requested_orders_api import (
    _complete,
    _lab_headers,
    _patient_request,
    _worklist,
)
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Flask:
    documents = tmp_path_factory.mktemp("order-confirmation")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    return create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _confirm(app: Flask, order_id: str, headers):
    return app.test_client().post(f"/api/v1/lab/orders/{order_id}/confirm", headers=headers)


def _completed_order(app: Flask, engine: Engine) -> str:
    order_id, _ = _patient_request(app, engine)
    _complete(app, order_id, _lab_headers(app, engine))
    return order_id


def _outbox_payloads(engine: Engine, order_id: str, event: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT payload FROM eventos_outbox WHERE agregado_id = :o AND tipo_evento = :e"),
            {"o": order_id, "e": event},
        ).all()
    return [json.loads(row.payload) for row in rows]


def test_a_completed_order_is_confirmed_once_and_then_closed(app, engine) -> None:
    order_id = _completed_order(app, engine)
    lab = _lab_headers(app, engine)

    first = _confirm(app, order_id, lab)
    second = _confirm(app, order_id, lab)

    assert first.status_code == 200, first.get_json()
    assert second.status_code == 409
    with engine.connect() as conn:
        confirmed = conn.execute(
            text("SELECT confirmada_en FROM ordenes_laboratorio WHERE id = :o"), {"o": order_id}
        ).scalar()
    assert confirmed is not None


def test_a_confirmed_order_refuses_new_results_and_new_reports(app, engine) -> None:
    order_id = _completed_order(app, engine)
    lab = _lab_headers(app, engine)
    assert _confirm(app, order_id, lab).status_code == 200

    line = _worklist(app, lab, limit=200)  # the order has left the queue
    assert order_id not in [item["id"] for item in line["items"]]
    results = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_result_body("00000000-0000-4000-8000-000000000000", analito=HB),
        headers=lab,
    )
    upload = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/documents",
        data={"archivo": (io.BytesIO(PDF), "tarde.pdf", "application/pdf")},
        headers=lab,
        content_type="multipart/form-data",
    )
    assert results.status_code == 409, results.get_json()
    assert upload.status_code == 409, upload.get_json()


def test_an_order_with_pending_tests_cannot_be_confirmed(app, engine) -> None:
    order_id, _ = _patient_request(app, engine)
    lab = _lab_headers(app, engine)

    response = _confirm(app, order_id, lab)

    assert response.status_code == 409
    assert "results" in response.get_json()["detail"]


def test_confirming_an_unknown_order_is_not_found(app, engine) -> None:
    response = _confirm(app, "00000000-0000-4000-8000-00000000dead", _lab_headers(app, engine))

    assert response.status_code == 404


@pytest.mark.parametrize("role", ["PACIENTE", "MEDICO"])
def test_only_the_laboratory_confirms(app, engine, role) -> None:
    order_id = _completed_order(app, engine)
    headers = _token(app, _user(engine, role))

    assert _confirm(app, order_id, headers).status_code == 403


def test_the_patient_still_reads_the_results_after_confirmation(app, engine) -> None:
    order_id, patient_headers = _patient_request(app, engine)
    _complete(app, order_id, _lab_headers(app, engine))
    _confirm(app, order_id, _lab_headers(app, engine))

    results = app.test_client().get(
        f"/api/v1/lab/orders/{order_id}/results", headers=patient_headers
    )

    assert results.status_code == 200
    assert results.get_json()["items"][0]["valor_numerico"] == 13.5


def test_confirmation_announces_the_order_with_no_report_when_none_was_uploaded(
    app, engine
) -> None:
    order_id = _completed_order(app, engine)

    _confirm(app, order_id, _lab_headers(app, engine))

    [payload] = _outbox_payloads(engine, order_id, "orden_laboratorio.confirmada")
    assert payload == {"estado_orden": "COMPLETADA", "tiene_informe": False}


def test_confirmation_announces_the_report_when_one_was_uploaded(app, engine) -> None:
    order_id = _completed_order(app, engine)
    lab = _lab_headers(app, engine)
    upload = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/documents",
        data={"archivo": (io.BytesIO(PDF), "informe.pdf", "application/pdf")},
        headers=lab,
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201, upload.get_json()

    _confirm(app, order_id, lab)

    [payload] = _outbox_payloads(engine, order_id, "orden_laboratorio.confirmada")
    assert payload["tiene_informe"] is True


def test_completing_results_no_longer_announces_the_mail(app, engine) -> None:
    order_id = _completed_order(app, engine)

    assert _outbox_payloads(engine, order_id, "orden_laboratorio.confirmada") == []
