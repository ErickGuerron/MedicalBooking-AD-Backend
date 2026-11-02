"""Laboratory flow (E3) against real SQL Server: order -> results -> review, PDF documents.

Each test creates its own users, so tests do not depend on order. Concurrency tests use
parallel requests and assert on exact counts. Documents are written to a temporary folder.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_scheduling_api import _count, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PDF = b"%PDF-1.4\n% synthetic lab report\n%%EOF\n"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Iterator[Flask]:
    documents = tmp_path_factory.mktemp("lab-documents")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _doctor(engine: Engine) -> str:
    user = _user(engine, "MEDICO")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO medicos (id, usuario_id, registro_profesional) "
                "VALUES (NEWID(), :u, :reg)"
            ),
            {"u": user, "reg": f"REG-{uuid.uuid4()}"},
        )
    return user


def _test_type(engine: Engine, code: str = "HEMO-SINT") -> str:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT id FROM tipos_prueba_laboratorio WHERE codigo = :c"), {"c": code}
        ).scalar_one()
    return str(uuid.UUID(str(value)))


def _patient_id(engine: Engine, user_id: str) -> str:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT id FROM pacientes WHERE usuario_id = :u"), {"u": user_id}
        ).scalar_one()
    return str(uuid.UUID(str(value)))


def _create_order(client, headers, patient_id: str, tests: list[str] | None = None):
    return client.post(
        "/api/v1/lab/orders",
        json={"paciente_id": patient_id, "tipo_prueba_ids": tests or []},
        headers=headers,
    )


def _order_for(app, engine) -> tuple[str, str, str, dict[str, str]]:
    """Return (order_id, patient_user, doctor_user, doctor_headers) for a fresh order."""
    patient = _user(engine, "PACIENTE")
    doctor = _doctor(engine)
    headers = _token(app, doctor)
    response = _create_order(
        app.test_client(), headers, _patient_id(engine, patient), [_test_type(engine)]
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"], patient, doctor, headers


def _detail(app, order_id: str, headers) -> str:
    order = app.test_client().get(f"/api/v1/lab/orders/{order_id}", headers=headers)
    return order.get_json()["pruebas"][0]["detalle_id"]


def _result_body(detail_id: str, value: float = 13.5) -> dict:
    return {
        "detalle_id": detail_id,
        "resultados": [
            {"nombre_analito": "Hemoglobina", "valor_numerico": value, "unidad": "g/dL"}
        ],
    }


# ---------- flow ----------


def test_order_to_results_to_review_and_patient_reads_them(app, engine) -> None:
    order_id, patient, doctor, doctor_headers = _order_for(app, engine)
    client = app.test_client()
    lab_headers = _token(app, _user(engine, "LABORATORISTA"))
    detail = _detail(app, order_id, doctor_headers)

    recorded = client.post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_result_body(detail),
        headers=lab_headers,
    )
    reviewed = client.post(
        f"/api/v1/lab/orders/{order_id}/review",
        json={"conclusion": "Resultados revisados (sintetico)", "recomendaciones": None},
        headers=doctor_headers,
    )
    patient_results = client.get(
        f"/api/v1/lab/orders/{order_id}/results", headers=_token(app, patient)
    )

    assert recorded.status_code == 200
    assert recorded.get_json()["estado"] == "COMPLETADA"
    assert reviewed.status_code == 201
    assert patient_results.status_code == 200
    assert patient_results.get_json()["items"][0]["nombre_analito"] == "Hemoglobina"


def test_results_are_stored_without_any_clinical_classification(app, engine) -> None:
    order_id, _, _, doctor_headers = _order_for(app, engine)
    lab_headers = _token(app, _user(engine, "LABORATORISTA"))
    detail = _detail(app, order_id, doctor_headers)

    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results", json=_result_body(detail), headers=lab_headers
    )

    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM resultados_laboratorio r "
            "JOIN ordenes_laboratorio_detalle d ON d.id = r.detalle_orden_id "
            "WHERE d.id = :d AND r.interpretacion IS NULL AND r.referencia_aplicada_id IS NULL",
            d=detail,
        )
        == 1
    )


def test_a_foreign_patient_and_a_foreign_doctor_cannot_read_the_order(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)
    client = app.test_client()

    stranger_patient = _token(app, _user(engine, "PACIENTE"))
    stranger_doctor = _token(app, _doctor(engine))

    assert client.get(f"/api/v1/lab/orders/{order_id}", headers=stranger_patient).status_code == 403
    assert client.get(f"/api/v1/lab/orders/{order_id}", headers=stranger_doctor).status_code == 403


def test_missing_and_foreign_orders_answer_the_same(app, engine) -> None:
    order_id, _, _, _ = _order_for(app, engine)
    client = app.test_client()
    intruder = _token(app, _user(engine, "PACIENTE"))

    missing = client.get(f"/api/v1/lab/orders/{uuid.uuid4()}", headers=intruder)
    foreign = client.get(f"/api/v1/lab/orders/{order_id}", headers=intruder)

    assert missing.status_code == foreign.status_code == 403
    assert missing.get_json()["detail"] == foreign.get_json()["detail"]


def test_patient_cannot_create_orders(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = _create_order(
        app.test_client(),
        _token(app, patient),
        _patient_id(engine, patient),
        [_test_type(engine)],
    )

    assert response.status_code == 403


def test_unknown_test_type_is_rejected_with_422(app, engine) -> None:
    patient = _user(engine, "PACIENTE")

    response = _create_order(
        app.test_client(),
        _token(app, _doctor(engine)),
        _patient_id(engine, patient),
        [str(uuid.uuid4())],
    )

    assert response.status_code == 422


# ---------- concurrency ----------


def test_parallel_results_for_one_test_leave_exactly_one_recorded(app, engine) -> None:
    order_id, _, _, doctor_headers = _order_for(app, engine)
    detail = _detail(app, order_id, doctor_headers)
    lab_headers = _token(app, _user(engine, "LABORATORISTA"))

    def attempt(index: int) -> int:
        return (
            app.test_client()
            .post(
                f"/api/v1/lab/orders/{order_id}/results",
                json=_result_body(detail, value=10.0 + index),
                headers=lab_headers,
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(8)))

    assert statuses.count(200) == 1, statuses
    assert statuses.count(409) == 7, statuses
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM resultados_laboratorio WHERE detalle_orden_id = :d",
            d=detail,
        )
        == 1
    )


def test_parallel_reviews_leave_exactly_one_review(app, engine) -> None:
    order_id, _, _, doctor_headers = _order_for(app, engine)
    detail = _detail(app, order_id, doctor_headers)
    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_result_body(detail),
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )

    def attempt(_: int) -> int:
        return (
            app.test_client()
            .post(
                f"/api/v1/lab/orders/{order_id}/review",
                json={"conclusion": "Revisado (sintetico)"},
                headers=doctor_headers,
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(attempt, range(6)))

    assert statuses.count(201) == 1, statuses
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM revisiones_laboratorio WHERE orden_id = :o",
            o=order_id,
        )
        == 1
    )


# ---------- review rules ----------


def test_review_before_results_is_a_conflict(app, engine) -> None:
    order_id, _, _, doctor_headers = _order_for(app, engine)

    response = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/review",
        json={"conclusion": "Antes de tiempo"},
        headers=doctor_headers,
    )

    assert response.status_code == 409


# ---------- documents ----------


def _upload(client, headers, order_id: str, content: bytes, mime: str = "application/pdf"):
    return client.post(
        f"/api/v1/lab/orders/{order_id}/documents",
        data={"archivo": (io.BytesIO(content), "informe.pdf", mime)},
        headers=headers,
        content_type="multipart/form-data",
    )


def test_pdf_is_stored_by_hash_and_downloaded_only_by_its_owners(app, engine) -> None:
    order_id, patient, _, doctor_headers = _order_for(app, engine)
    client = app.test_client()
    lab_headers = _token(app, _user(engine, "LABORATORISTA"))

    uploaded = _upload(client, lab_headers, order_id, PDF)
    document_id = uploaded.get_json()["id"]
    downloaded = client.get(
        f"/api/v1/documents/{document_id}/download", headers=_token(app, patient)
    )
    doctor_download = client.get(
        f"/api/v1/documents/{document_id}/download", headers=doctor_headers
    )
    stranger = client.get(
        f"/api/v1/documents/{document_id}/download", headers=_token(app, _user(engine, "PACIENTE"))
    )

    assert uploaded.status_code == 201
    assert uploaded.get_json()["hash_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert downloaded.status_code == 200
    assert downloaded.data == PDF
    assert downloaded.headers["Content-Disposition"].startswith("attachment; filename=")
    assert doctor_download.status_code == 200
    assert stranger.status_code == 403
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad_id = :d AND accion = 'descargar'",
            d=document_id,
        )
        >= 2
    )


def test_download_audit_has_the_patient_and_the_request_correlation_id(app, engine) -> None:
    order_id, patient, _, _ = _order_for(app, engine)
    client = app.test_client()
    document_id = _upload(
        client, _token(app, _user(engine, "LABORATORISTA")), order_id, PDF
    ).get_json()["id"]

    client.get(f"/api/v1/documents/{document_id}/download", headers=_token(app, patient))

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT paciente_id, correlation_id FROM auditoria_accesos "
                "WHERE entidad_id = :d AND accion = 'descargar' AND resultado = 'OK'"
            ),
            {"d": document_id},
        ).one()
    assert row.paciente_id is not None
    assert row.correlation_id is not None


@pytest.mark.parametrize(
    ("content", "mime", "status"),
    [
        (b"<html>no</html>", "application/pdf", 422),
        (PDF, "text/html", 422),
        (b"", "application/pdf", 422),
        (b"%PDF-" + b"0" * (10 * 1024 * 1024), "application/pdf", 422),
    ],
)
def test_invalid_uploads_are_rejected_and_store_nothing(app, engine, content, mime, status) -> None:
    order_id, _, _, _ = _order_for(app, engine)
    before = _count(
        engine, "SELECT COUNT(*) FROM documentos_clinicos WHERE orden_id = :o", o=order_id
    )

    response = _upload(
        app.test_client(), _token(app, _user(engine, "LABORATORISTA")), order_id, content, mime
    )

    assert response.status_code == status
    assert (
        _count(engine, "SELECT COUNT(*) FROM documentos_clinicos WHERE orden_id = :o", o=order_id)
        == before
    )
