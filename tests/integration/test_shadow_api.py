"""Shadow findings (S13, S14) against real SQL Server: read-only, audited, permission-checked.

A test-only analyte carries the verified hemoglobin LOINC 718-7 and is linked to the synthetic test
type. The code is released in a finally block, because codigo_loinc is unique across analytes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import _detail, _order_for, _test_type
from tests.integration.test_scheduling_api import _count, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
HB_LOINC = "718-7"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


@pytest.fixture
def hemoglobin_analyte(app: Flask, engine: Engine) -> Iterator[str]:
    """A test analyte with code 718-7. Any other holder of the code is restored afterwards."""
    with engine.begin() as conn:
        previous = conn.execute(
            text("SELECT id FROM conocimiento_analitos WHERE codigo_loinc = :c"),
            {"c": HB_LOINC},
        ).all()
        conn.execute(
            text("UPDATE conocimiento_analitos SET codigo_loinc = NULL WHERE codigo_loinc = :c"),
            {"c": HB_LOINC},
        )
    admin = _token(app, _user(engine, "ADMINISTRADOR"))
    response = app.test_client().post(
        "/api/v1/knowledge/analytes",
        json={"nombre": f"Hemoglobina prueba sombra {uuid.uuid4()}", "unidad": "g/dL"},
        headers=admin,
    )
    assert response.status_code == 201, response.get_json()
    analito = response.get_json()["id"]
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE conocimiento_analitos SET codigo_loinc = :c WHERE id = :a"),
                {"c": HB_LOINC, "a": analito},
            )
            conn.execute(
                text(
                    "INSERT INTO conocimiento_pruebas_analitos "
                    "(tipo_prueba_id, analito_id, obligatorio, orden_presentacion) "
                    "VALUES (:t, :a, 1, 1)"
                ),
                {"t": _test_type(engine), "a": analito},
            )
        yield analito
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE conocimiento_analitos SET codigo_loinc = NULL WHERE id = :a"),
                {"a": analito},
            )
            for row in previous:
                conn.execute(
                    text("UPDATE conocimiento_analitos SET codigo_loinc = :c WHERE id = :a"),
                    {"c": HB_LOINC, "a": row.id},
                )


def _order_with_hemoglobin(app, engine, analito: str, value: float, unit: str = "g/dL"):
    order_id, patient, doctor, doctor_headers = _order_for(app, engine)
    detail = _detail(app, order_id, doctor_headers)
    response = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json={
            "detalle_id": detail,
            "resultados": [
                {
                    "nombre_analito": "Hemoglobina",
                    "valor_numerico": value,
                    "unidad": unit,
                    "analito_id": analito,
                }
            ],
        },
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )
    assert response.status_code == 200, response.get_json()
    return order_id, doctor, doctor_headers


def _shadow(app, order_id: str, headers):
    return app.test_client().get(f"/api/v1/lab/orders/{order_id}/sombra", headers=headers)


def test_the_ordering_doctor_reads_shadow_findings_marked_as_not_decisive(
    app, engine, hemoglobin_analyte
) -> None:
    order_id, _, headers = _order_with_hemoglobin(app, engine, hemoglobin_analyte, 10.0)

    response = _shadow(app, order_id, headers)

    body = response.get_json()
    assert response.status_code == 200
    assert body["modo"] == "SOMBRA"
    assert body["modifica_decision"] is False
    # The synthetic patient has no birth date, so the anaemia reason is the missing age. That reason
    # proves the hemoglobin was read through its LOINC code (otherwise the reason would be
    # "sin valor de hemoglobina").
    assert body["anemia"]["motivo"] == "sin edad"


def test_a_result_in_another_unit_is_not_read(app, engine, hemoglobin_analyte) -> None:
    order_id, _, headers = _order_with_hemoglobin(
        app, engine, hemoglobin_analyte, 100.0, unit="g/L"
    )

    body = _shadow(app, order_id, headers).get_json()

    assert "sin valor de hemoglobina" in body["anemia"]["motivo"]


def test_another_doctor_is_refused_and_the_denial_is_audited(
    app, engine, hemoglobin_analyte
) -> None:
    order_id, _, _ = _order_with_hemoglobin(app, engine, hemoglobin_analyte, 10.0)
    stranger = _token(app, _user(engine, "PACIENTE"))

    response = _shadow(app, order_id, stranger)

    assert response.status_code == 403
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad = 'modo_sombra' "
            "AND entidad_id = :o AND resultado = 'DENEGADO'",
            o=order_id,
        )
        >= 1
    )


def test_a_read_is_audited_and_writes_no_clinical_decision(app, engine, hemoglobin_analyte) -> None:
    order_id, _, headers = _order_with_hemoglobin(app, engine, hemoglobin_analyte, 10.0)
    proposals_before = _count(
        engine, "SELECT COUNT(*) FROM propuestas_clinicas WHERE orden_id = :o", o=order_id
    )

    assert _shadow(app, order_id, headers).status_code == 200

    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad = 'modo_sombra' "
            "AND entidad_id = :o AND resultado = 'OK'",
            o=order_id,
        )
        == 1
    )
    assert (
        _count(engine, "SELECT COUNT(*) FROM propuestas_clinicas WHERE orden_id = :o", o=order_id)
        == proposals_before
    )
