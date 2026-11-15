"""Patient-requested lab exams (no doctor) and the laboratory worklist, against real SQL Server.

A patient asks for an exam; the order has no doctor, so there is no review and no proposal.
The laboratory sees every pending order, of both origins, oldest first, and records results on
them. The database itself refuses an order that mixes the two shapes.
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import (
    HB,
    _create_order,
    _doctor,
    _patient_id,
    _relate,
    _result_body,
    _test_type,
)
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PDF = b"%PDF-1.4\n% synthetic lab report\n%%EOF\n"
WORKLIST = "/api/v1/lab/worklist"
PATIENT_ORDERS = "/api/v1/lab/patient-orders"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Iterator[Flask]:
    documents = tmp_path_factory.mktemp("patient-orders-documents")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _patient(engine: Engine) -> str:
    return _user(engine, "PACIENTE")


def _profile(engine: Engine, table: str, user_id: str) -> str:
    """The profile id in the API's lower-case form (SQL Server returns upper-case GUIDs)."""
    with engine.connect() as conn:
        value = conn.execute(
            text(f"SELECT id FROM {table} WHERE usuario_id = :u"),  # noqa: S608
            {"u": user_id},
        ).scalar_one()
    return str(uuid.UUID(str(value)))


def _patient_profile(engine: Engine, user_id: str) -> str:
    return _profile(engine, "pacientes", user_id)


def _doctor_profile(engine: Engine, user_id: str) -> str:
    return _profile(engine, "medicos", user_id)


def _request(app: Flask, headers, tests: list[str], observaciones: str | None = None):
    body: dict = {"tipo_prueba_ids": tests}
    if observaciones is not None:
        body["observaciones"] = observaciones
    return app.test_client().post(PATIENT_ORDERS, json=body, headers=headers)


def _patient_request(app: Flask, engine: Engine) -> tuple[str, dict[str, str]]:
    """A patient asks for one exam. Returns (order_id, patient_headers)."""
    headers = _token(app, _patient(engine))
    response = _request(app, headers, [_test_type(engine)])
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"], headers


def _lab_headers(app: Flask, engine: Engine) -> dict[str, str]:
    return _token(app, _user(engine, "LABORATORISTA"))


def _worklist(app: Flask, headers, **query) -> dict:
    response = app.test_client().get(WORKLIST, query_string=query, headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _set_order(engine: Engine, order_id: str, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    with engine.begin() as conn:
        # Only column names from this file's own calls enter the SQL; values are bound.
        conn.execute(
            text(f"UPDATE ordenes_laboratorio SET {assignments} WHERE id = :o"),  # noqa: S608
            {**columns, "o": order_id},
        )


def _complete(app: Flask, order_id: str, lab_headers) -> None:
    """Record the mandatory analyte on every test line, so the order becomes COMPLETADA.

    The laboratory cannot read a single order, so the line ids come from its worklist.
    """
    client = app.test_client()
    listed = _worklist(app, lab_headers, limit=200)["items"]
    lines = next(item for item in listed if item["id"] == order_id)["pruebas"]
    for line in lines:
        response = client.post(
            f"/api/v1/lab/orders/{order_id}/results",
            json=_result_body(line["detalle_id"], analito=HB),
            headers=lab_headers,
        )
        assert response.status_code == 200, response.get_json()


# ---------- the patient asks for an exam ----------


def test_patient_request_creates_an_order_with_no_doctor(app, engine) -> None:
    patient = _patient(engine)
    tests = [_test_type(engine)]
    response = _request(app, _token(app, patient), tests, observaciones="ayuno de 8 horas")

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["origen"] == "PACIENTE"
    assert body["medico_id"] is None
    assert body["prioridad"] == "NORMAL"
    assert body["estado"] == "SOLICITADA"
    assert body["observaciones"] == "ayuno de 8 horas"
    assert body["paciente_id"] == _patient_profile(engine, patient)
    assert [line["tipo_prueba_id"] for line in body["pruebas"]] == tests


def test_patient_request_is_stored_with_origin_and_no_doctor(app, engine) -> None:
    order_id, _ = _patient_request(app, engine)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT origen, medico_id FROM ordenes_laboratorio WHERE id = :o"),
            {"o": order_id},
        ).one()
    assert row.origen == "PACIENTE"
    assert row.medico_id is None


def test_patient_request_is_announced_to_the_replica_without_a_doctor(app, engine) -> None:
    order_id, _ = _patient_request(app, engine)

    with engine.connect() as conn:
        payload = conn.execute(
            text(
                "SELECT payload FROM eventos_outbox WHERE agregado_id = :o "
                "AND tipo_evento = 'orden_laboratorio.creada'"
            ),
            {"o": order_id},
        ).scalar_one()
    orden = json.loads(payload)["orden"]
    assert orden["origen"] == "PACIENTE"
    assert orden["medico_id"] is None


def test_patient_request_is_audited_without_values(app, engine) -> None:
    order_id, _ = _patient_request(app, engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT accion, resultado, entidad FROM auditoria_accesos WHERE entidad_id = :o"),
            {"o": order_id},
        ).all()
    assert [(r.accion, r.resultado, r.entidad) for r in rows] == [
        ("solicitar", "OK", "orden_laboratorio")
    ]


def test_patient_order_is_readable_by_its_patient_with_no_doctor(app, engine) -> None:
    order_id, headers = _patient_request(app, engine)

    order = app.test_client().get(f"/api/v1/lab/orders/{order_id}", headers=headers)
    assert order.status_code == 200, order.get_json()
    assert order.get_json()["medico_id"] is None

    listed = app.test_client().get("/api/v1/lab/orders", headers=headers)
    assert order_id in [item["id"] for item in listed.get_json()["items"]]


def test_doctor_cannot_read_review_or_evaluate_a_patient_order(app, engine) -> None:
    order_id, _ = _patient_request(app, engine)
    doctor_headers = _token(app, _doctor(engine))
    client = app.test_client()

    assert client.get(f"/api/v1/lab/orders/{order_id}", headers=doctor_headers).status_code == 403
    assert (
        client.get(f"/api/v1/lab/orders/{order_id}/results", headers=doctor_headers).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/lab/orders/{order_id}/review",
            json={"conclusion": "Sin hallazgos"},
            headers=doctor_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor_headers).status_code
        == 403
    )
    assert (
        client.get(f"/api/v1/lab/orders/{order_id}/proposal", headers=doctor_headers).status_code
        == 403
    )


def test_doctor_order_keeps_its_doctor_origin_and_response_shape(app, engine) -> None:
    patient = _patient(engine)
    doctor = _doctor(engine)
    _relate(app, engine, patient, doctor)
    response = _create_order(
        app.test_client(),
        _token(app, doctor),
        _patient_id(engine, patient),
        [_test_type(engine)],
    )

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["medico_id"] == _doctor_profile(engine, doctor)
    assert "origen" not in body  # the doctor order response keeps its shape
    with engine.connect() as conn:
        origen = conn.execute(
            text("SELECT origen FROM ordenes_laboratorio WHERE id = :o"), {"o": body["id"]}
        ).scalar_one()
    assert origen == "MEDICO"


# ---------- refusals ----------


def test_doctor_with_order_permission_cannot_request_as_patient(app, engine) -> None:
    response = _request(app, _token(app, _doctor(engine)), [_test_type(engine)])
    assert response.status_code == 403


def test_lab_staff_cannot_request_an_exam(app, engine) -> None:
    response = _request(app, _lab_headers(app, engine), [_test_type(engine)])
    assert response.status_code == 403


def test_patient_request_refuses_an_unknown_or_inactive_test(app, engine) -> None:
    response = _request(app, _token(app, _patient(engine)), [str(uuid.uuid4())])
    assert response.status_code == 422
    assert "Unknown or inactive test type" in response.get_json()["detail"]


@pytest.mark.parametrize(
    "tests",
    [[], [str(uuid.uuid4())] * 2, ["not-a-uuid"], [str(uuid.uuid4())] * 21],
    ids=["empty", "duplicate", "not-a-uuid", "too-many"],
)
def test_patient_request_refuses_an_invalid_test_list(app, engine, tests) -> None:
    response = _request(app, _token(app, _patient(engine)), tests)
    assert response.status_code == 422


def test_patient_request_refuses_long_observations(app, engine) -> None:
    response = _request(
        app, _token(app, _patient(engine)), [_test_type(engine)], observaciones="x" * 1001
    )
    assert response.status_code == 422


def test_patient_request_cannot_choose_a_patient_or_a_priority(app, engine) -> None:
    headers = _token(app, _patient(engine))
    client = app.test_client()
    for extra in ({"paciente_id": str(uuid.uuid4())}, {"prioridad": "URGENTE"}):
        body = {"tipo_prueba_ids": [_test_type(engine)], **extra}
        response = client.post(PATIENT_ORDERS, json=body, headers=headers)
        assert response.status_code == 422, extra


def test_patient_request_without_a_token_is_401(app) -> None:
    response = app.test_client().post(PATIENT_ORDERS, json={"tipo_prueba_ids": []})
    assert response.status_code == 401


# ---------- the laboratory worklist ----------


def test_worklist_lists_pending_orders_of_both_origins(app, engine) -> None:
    patient_order, _ = _patient_request(app, engine)
    patient = _patient(engine)
    doctor = _doctor(engine)
    _relate(app, engine, patient, doctor)
    doctor_order = _create_order(
        app.test_client(), _token(app, doctor), _patient_id(engine, patient), [_test_type(engine)]
    ).get_json()["id"]

    items = _worklist(app, _lab_headers(app, engine), limit=200)["items"]
    by_id = {item["id"]: item for item in items}
    assert by_id[patient_order]["origen"] == "PACIENTE"
    assert by_id[patient_order]["medico_id"] is None
    assert by_id[doctor_order]["origen"] == "MEDICO"
    assert by_id[doctor_order]["medico_id"] is not None
    # The date is what the laboratory sorts and reads by, in UTC with a Z suffix.
    assert by_id[patient_order]["fecha"].endswith("Z")
    # The lines carry the ids the laboratory needs to record results.
    assert all(line["detalle_id"] for line in by_id[patient_order]["pruebas"])


def test_worklist_leaves_out_completed_and_cancelled_orders(app, engine) -> None:
    done, _ = _patient_request(app, engine)
    cancelled, _ = _patient_request(app, engine)
    pending, _ = _patient_request(app, engine)
    lab = _lab_headers(app, engine)
    _complete(app, done, lab)
    _set_order(engine, cancelled, estado="CANCELADA")

    ids = [item["id"] for item in _worklist(app, lab, limit=200)["items"]]
    assert done not in ids
    assert cancelled not in ids
    assert pending in ids


def test_worklist_is_oldest_first_and_pages_on_orders(app, engine) -> None:
    lab = _lab_headers(app, engine)
    first, _ = _patient_request(app, engine)
    second, _ = _patient_request(app, engine)
    third, _ = _patient_request(app, engine)
    # Pin the order times far in the past so these are the oldest pending orders of the run.
    _set_order(engine, first, fecha_orden="2000-01-01 08:00:00")
    _set_order(engine, second, fecha_orden="2000-01-02 08:00:00")
    _set_order(engine, third, fecha_orden="2000-01-03 08:00:00")

    page_one = _worklist(app, lab, limit=2, offset=0)
    page_two = _worklist(app, lab, limit=2, offset=2)
    assert [item["id"] for item in page_one["items"]] == [first, second]
    assert page_one["limit"] == 2 and page_one["offset"] == 0
    assert page_two["items"][0]["id"] == third
    # A page holds whole orders: every item keeps all of its test lines.
    for item in page_one["items"] + page_two["items"]:
        assert item["pruebas"], item


def test_worklist_answers_422_for_an_invalid_page(app, engine) -> None:
    response = app.test_client().get(
        WORKLIST, query_string={"limit": 0}, headers=_lab_headers(app, engine)
    )
    assert response.status_code == 422


def test_worklist_is_forbidden_to_patients(app, engine) -> None:
    response = app.test_client().get(WORKLIST, headers=_token(app, _patient(engine)))
    assert response.status_code == 403


def test_worklist_is_forbidden_to_doctors(app, engine) -> None:
    response = app.test_client().get(WORKLIST, headers=_token(app, _doctor(engine)))
    assert response.status_code == 403


def test_worklist_is_audited_without_patient_identity(app, engine) -> None:
    _patient_request(app, engine)
    _worklist(app, _lab_headers(app, engine), limit=5)

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT TOP 1 accion, paciente_id, entidad_id FROM auditoria_accesos "
                "WHERE accion = 'listar_pendientes' ORDER BY fecha_evento DESC"
            )
        ).one()
    assert row.accion == "listar_pendientes"
    assert row.paciente_id is None
    assert row.entidad_id is None


def test_laboratory_still_cannot_read_a_single_order(app, engine) -> None:
    order_id, _ = _patient_request(app, engine)
    response = app.test_client().get(
        f"/api/v1/lab/orders/{order_id}", headers=_lab_headers(app, engine)
    )
    assert response.status_code == 403


# ---------- the whole flows ----------


def test_patient_request_to_results_to_completed_and_the_patient_reads_them(app, engine) -> None:
    order_id, patient_headers = _patient_request(app, engine)
    lab = _lab_headers(app, engine)

    listed = _worklist(app, lab, limit=200)["items"]
    assert order_id in [item["id"] for item in listed]

    detail = next(item for item in listed if item["id"] == order_id)["pruebas"][0]["detalle_id"]
    recorded = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_result_body(detail, analito=HB),
        headers=lab,
    )
    assert recorded.status_code == 200, recorded.get_json()
    assert recorded.get_json()["estado"] == "COMPLETADA"

    client = app.test_client()
    results = client.get(f"/api/v1/lab/orders/{order_id}/results", headers=patient_headers)
    assert results.status_code == 200
    assert results.get_json()["items"][0]["valor_numerico"] == 13.5
    report = client.get(f"/api/v1/lab/orders/{order_id}/report.pdf", headers=patient_headers)
    assert report.status_code == 200
    assert report.mimetype == "application/pdf"

    # A completed order leaves the worklist.
    assert order_id not in [item["id"] for item in _worklist(app, lab, limit=200)["items"]]


def test_lab_uploads_the_pdf_of_a_patient_order_and_the_patient_downloads_it(app, engine) -> None:
    order_id, patient_headers = _patient_request(app, engine)
    lab = _lab_headers(app, engine)

    upload = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/documents",
        data={"archivo": (io.BytesIO(PDF), "informe.pdf", "application/pdf")},
        headers=lab,
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201, upload.get_json()

    download = app.test_client().get(
        f"/api/v1/documents/{upload.get_json()['id']}/download", headers=patient_headers
    )
    assert download.status_code == 200
    assert download.data == PDF


# ---------- the database keeps the two shapes apart ----------


def test_database_refuses_a_patient_origin_order_with_a_doctor(app, engine) -> None:
    paciente_id = _patient_profile(engine, _patient(engine))
    medico_id = _doctor_profile(engine, _doctor(engine))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio (id, paciente_id, medico_id, fecha_orden, "
                "prioridad, estado, origen) VALUES (NEWID(), :p, :m, SYSUTCDATETIME(), "
                "'NORMAL', 'SOLICITADA', 'PACIENTE')"
            ),
            {"p": paciente_id, "m": medico_id},
        )


def test_database_refuses_a_doctor_origin_order_without_a_doctor(app, engine) -> None:
    paciente_id = _patient_profile(engine, _patient(engine))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio (id, paciente_id, medico_id, fecha_orden, "
                "prioridad, estado, origen) VALUES (NEWID(), :p, NULL, SYSUTCDATETIME(), "
                "'NORMAL', 'SOLICITADA', 'MEDICO')"
            ),
            {"p": paciente_id},
        )


def test_migration_grants_the_request_permission_to_patients_only(app, engine) -> None:
    with engine.connect() as conn:
        roles = {
            row.rol
            for row in conn.execute(
                text(
                    "SELECT r.nombre AS rol FROM roles_permisos rp "
                    "JOIN roles r ON r.id = rp.rol_id JOIN permisos p ON p.id = rp.permiso_id "
                    "WHERE p.codigo = 'ordenes.solicitar_propias'"
                )
            ).all()
        }
    assert roles == {"PACIENTE"}
