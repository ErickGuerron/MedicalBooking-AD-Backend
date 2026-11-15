"""The uploaded report of an order is listed for whoever may read the order, and nobody else.

Against real SQL Server: the laboratory records and lists, the ordering doctor and the patient
read the same list, and a stranger gets the uniform 403.
"""

from __future__ import annotations

import io

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import PDF, _order_for
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Flask:
    documents = tmp_path_factory.mktemp("lab-document-listing")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    return create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _upload(app: Flask, order_id: str, headers) -> dict:
    response = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/documents",
        data={"archivo": (io.BytesIO(PDF), "informe.pdf", "application/pdf")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _listed(app: Flask, order_id: str, headers):
    return app.test_client().get(f"/api/v1/lab/orders/{order_id}/documents", headers=headers)


def test_the_laboratory_lists_the_report_it_uploaded(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)
    lab = _token(app, _user(engine, "LABORATORISTA"))
    document = _upload(app, order_id, lab)

    response = _listed(app, order_id, lab)

    assert response.status_code == 200, response.get_json()
    [item] = response.get_json()["items"]
    assert item["id"] == document["id"]
    assert item["nombre_archivo"] == "informe.pdf"
    assert "ruta_storage" not in item  # the storage path never leaves the server


def test_the_patient_and_the_ordering_doctor_see_the_same_report(app, engine) -> None:
    order_id, patient, doctor, doctor_headers = _order_for(app, engine)
    _upload(app, order_id, _token(app, _user(engine, "LABORATORISTA")))

    patient_items = _listed(app, order_id, _token(app, patient)).get_json()["items"]
    doctor_items = _listed(app, order_id, doctor_headers).get_json()["items"]

    assert len(patient_items) == 1
    assert patient_items == doctor_items


def test_an_order_with_no_report_lists_nothing(app, engine) -> None:
    order_id, patient, _, _ = _order_for(app, engine)

    response = _listed(app, order_id, _token(app, patient))

    assert response.get_json() == {"items": []}


def test_a_stranger_gets_the_uniform_403_and_a_missing_order_the_same(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)
    stranger = _token(app, _user(engine, "PACIENTE"))

    assert _listed(app, order_id, stranger).status_code == 403
    missing = "00000000-0000-4000-8000-000000000000"
    assert _listed(app, missing, stranger).status_code == 403


def test_a_listed_report_downloads_for_its_patient(app, engine) -> None:
    order_id, patient, _, _ = _order_for(app, engine)
    _upload(app, order_id, _token(app, _user(engine, "LABORATORISTA")))
    patient_headers = _token(app, patient)
    [item] = _listed(app, order_id, patient_headers).get_json()["items"]

    download = app.test_client().get(
        f"/api/v1/documents/{item['id']}/download", headers=patient_headers
    )

    assert download.status_code == 200
    assert download.data == PDF
