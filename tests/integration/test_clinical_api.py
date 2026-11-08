"""Clinical knowledge and proposals (E5) against real SQL Server.

Knowledge administration uses its own analyte per test, so the seeded synthetic knowledge
is never changed by these tests. Concurrency tests assert exact counts.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import (
    _create_order,
    _detail,
    _doctor,
    _order_for,
    _patient_id,
    _test_type,
)
from tests.integration.test_scheduling_api import _count, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
HB_SEEDED = "a0000000-0000-4000-8000-00000000b001"  # synthetic hemoglobin, seed 0006
SOURCE = "f0000000-0000-4000-8000-000000000001"  # synthetic source, seed 0006


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _lab_result(detail: str, *, value: float, analito: str | None = HB_SEEDED) -> dict:
    item = {"nombre_analito": "Hemoglobina", "valor_numerico": value, "unidad": "g/dL"}
    if analito is not None:
        item["analito_id"] = analito
    return {"detalle_id": detail, "resultados": [item]}


def _complete(app, engine, *, value: float, analito: str | None = HB_SEEDED):
    """Order -> results entered by the lab. Returns (order_id, patient, doctor_headers)."""
    order_id, patient, _, doctor_headers = _order_for(app, engine)
    detail = _detail(app, order_id, doctor_headers)
    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json=_lab_result(detail, value=value, analito=analito),
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )
    return order_id, patient, doctor_headers


def _proposal(app, order_id: str, headers):
    return app.test_client().get(f"/api/v1/lab/orders/{order_id}/proposal", headers=headers)


def _analyte(app, engine, unidad: str = "mg/L") -> str:
    admin = _token(app, _user(engine, "ADMINISTRADOR"))
    response = app.test_client().post(
        "/api/v1/knowledge/analytes",
        json={"nombre": f"Analito de prueba {uuid.uuid4()}", "unidad": unidad},
        headers=admin,
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def _draft(app, engine, analito: str, **overrides) -> str:
    body = {
        "fuente_id": SOURCE,
        "unidad": "mg/L",
        "minimo": 1.0,
        "maximo": 2.0,
        "fecha_inicio": "2020-01-01",
    }
    body.update(overrides)
    response = app.test_client().post(
        f"/api/v1/knowledge/analytes/{analito}/references",
        json=body,
        headers=_token(app, _user(engine, "ADMINISTRADOR")),
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def _validate(app, engine, reference_id: str) -> int:
    return (
        app.test_client()
        .post(
            f"/api/v1/knowledge/references/{reference_id}/validate",
            headers=_token(app, _doctor(engine)),
        )
        .status_code
    )


# ---------- knowledge: reads and permissions ----------


def test_doctor_reads_the_seeded_synthetic_analytes(app, engine) -> None:
    response = app.test_client().get(
        "/api/v1/knowledge/analytes", headers=_token(app, _doctor(engine))
    )

    assert response.status_code == 200
    assert HB_SEEDED in [item["id"] for item in response.get_json()["items"]]


def test_patient_cannot_read_clinical_knowledge(app, engine) -> None:
    response = app.test_client().get(
        "/api/v1/knowledge/analytes", headers=_token(app, _user(engine, "PACIENTE"))
    )

    assert response.status_code == 403


def test_only_administrators_create_analytes_and_only_doctors_validate(app, engine) -> None:
    client = app.test_client()
    doctor = _token(app, _doctor(engine))

    created = client.post("/api/v1/knowledge/analytes", json={"nombre": "X"}, headers=doctor)
    admin = _token(app, _user(engine, "ADMINISTRADOR"))
    analito = _analyte(app, engine)
    draft = _draft(app, engine, analito)
    validated_by_admin = client.post(
        f"/api/v1/knowledge/references/{draft}/validate", headers=admin
    )

    assert created.status_code == 403
    assert validated_by_admin.status_code == 403


# ---------- knowledge: drafts and validation ----------


def test_a_draft_reference_is_not_listed_until_a_doctor_validates_it(app, engine) -> None:
    analito = _analyte(app, engine)
    draft = _draft(app, engine, analito)
    client = app.test_client()
    reader = _token(app, _doctor(engine))

    before = client.get(f"/api/v1/knowledge/analytes/{analito}/references", headers=reader)
    assert _validate(app, engine, draft) == 200
    after = client.get(f"/api/v1/knowledge/analytes/{analito}/references", headers=reader)

    assert draft not in [r["id"] for r in before.get_json()["items"]]
    assert draft in [r["id"] for r in after.get_json()["items"]]


def test_an_overlapping_reference_cannot_be_validated(app, engine) -> None:
    analito = _analyte(app, engine)
    first = _draft(app, engine, analito)
    second = _draft(app, engine, analito, minimo=1.5, maximo=3.0)
    assert _validate(app, engine, first) == 200

    assert _validate(app, engine, second) == 409


def test_sex_specific_references_do_not_overlap(app, engine) -> None:
    analito = _analyte(app, engine)
    female = _draft(app, engine, analito, sexo="F")
    male = _draft(app, engine, analito, sexo="M", minimo=3.0, maximo=4.0)

    assert _validate(app, engine, female) == 200
    assert _validate(app, engine, male) == 200


def test_validating_twice_is_a_conflict(app, engine) -> None:
    analito = _analyte(app, engine)
    draft = _draft(app, engine, analito)
    assert _validate(app, engine, draft) == 200

    assert _validate(app, engine, draft) == 409


def test_parallel_validation_of_overlapping_drafts_keeps_exactly_one(app, engine) -> None:
    analito = _analyte(app, engine)
    drafts = [_draft(app, engine, analito), _draft(app, engine, analito)]
    doctors = [_token(app, _doctor(engine)) for _ in drafts]

    def attempt(index: int) -> int:
        return (
            app.test_client()
            .post(
                f"/api/v1/knowledge/references/{drafts[index]}/validate",
                headers=doctors[index],
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(attempt, range(2)))

    assert statuses == [200, 409], statuses
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM conocimiento_referencias "
            "WHERE analito_id = :a AND estado = 'VALIDADA'",
            a=analito,
        )
        == 1
    )


def test_a_rule_with_an_unknown_analyte_is_rejected(app, engine) -> None:
    response = app.test_client().post(
        "/api/v1/knowledge/rules",
        json={
            "codigo": "REGLA-TEST-1",
            "nombre": "Regla de prueba",
            "tipo": "HALLAZGO",
            "expresion_logica": "TODOS",
            "mensaje": "Mensaje de prueba",
            "criterios": [{"analito_id": str(uuid.uuid4()), "operador": "LT", "umbral": 1.0}],
        },
        headers=_token(app, _user(engine, "ADMINISTRADOR")),
    )

    assert response.status_code == 422


# ---------- proposals: classification and evaluation ----------


def test_low_hemoglobin_is_classified_and_fires_the_seeded_rule(app, engine) -> None:
    order_id, _, doctor = _complete(app, engine, value=11.0)

    response = app.test_client().post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor)

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["estado"] == "PENDIENTE"
    assert body["aviso"].lower().startswith("propuesta preliminar")
    codes = [f["regla_codigo"] for f in body["hallazgos"]]
    assert "HB-BAJA-SINT" in codes
    finding = next(f for f in body["hallazgos"] if f["regla_codigo"] == "HB-BAJA-SINT")
    assert finding["evidencia"][0]["valor_observado"] == 11.0
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM resultados_laboratorio r "
            "JOIN ordenes_laboratorio_detalle d ON d.id = r.detalle_orden_id "
            "WHERE d.orden_id = :o AND r.interpretacion = 'BAJO' "
            "AND r.referencia_aplicada_id IS NOT NULL",
            o=order_id,
        )
        == 1
    )


def test_normal_hemoglobin_is_classified_without_findings(app, engine) -> None:
    order_id, _, doctor = _complete(app, engine, value=14.0)

    body = (
        app.test_client().post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor).get_json()
    )

    assert body["hallazgos"] == []
    assert body["estado"] == "PENDIENTE"


def test_a_value_without_a_reference_for_its_unit_gives_sin_evidencia(app, engine) -> None:
    order_id, _, _, doctor_headers = _order_for(app, engine)
    detail = _detail(app, order_id, doctor_headers)
    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json={
            "detalle_id": detail,
            "resultados": [
                {
                    "nombre_analito": "Hemoglobina",
                    "valor_numerico": 11.0,
                    "unidad": "mmol/L",
                    "analito_id": HB_SEEDED,
                }
            ],
        },
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )

    body = (
        app.test_client()
        .post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor_headers)
        .get_json()
    )

    assert body["estado"] == "SIN_EVIDENCIA"
    assert body["hallazgos"] == []


def test_a_result_without_its_mandatory_analyte_does_not_complete_the_order(app, engine) -> None:
    order_id, _, _, doctor_headers = _order_for(app, engine)
    detail = _detail(app, order_id, doctor_headers)
    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json={
            "detalle_id": detail,
            "resultados": [{"nombre_analito": "Texto libre", "valor_texto": "ok"}],
        },
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )

    response = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor_headers
    )

    assert response.status_code == 409
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM ordenes_laboratorio_detalle "
            "WHERE id = :d AND estado = 'EN_PROCESO'",
            d=detail,
        )
        == 1
    )


def test_evaluation_needs_a_complete_order(app, engine) -> None:
    order_id, _, _, doctor = _order_for(app, engine)

    response = app.test_client().post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor)

    assert response.status_code == 409


def test_an_order_is_evaluated_only_once(app, engine) -> None:
    order_id, _, doctor = _complete(app, engine, value=11.0)
    client = app.test_client()
    client.post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor)

    response = client.post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor)

    assert response.status_code == 409


def test_parallel_evaluations_leave_exactly_one_proposal(app, engine) -> None:
    order_id, _, doctor = _complete(app, engine, value=11.0)

    def attempt(_: int) -> int:
        return (
            app.test_client()
            .post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor)
            .status_code
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt, range(4)))

    assert statuses.count(201) == 1, statuses
    assert (
        _count(engine, "SELECT COUNT(*) FROM propuestas_clinicas WHERE orden_id = :o", o=order_id)
        == 1
    )


def test_patient_and_other_doctors_cannot_read_the_proposal(app, engine) -> None:
    order_id, patient, doctor = _complete(app, engine, value=11.0)
    app.test_client().post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor)

    patient_view = _proposal(app, order_id, _token(app, patient))
    stranger_view = _proposal(app, order_id, _token(app, _doctor(engine)))

    assert patient_view.status_code == 403
    assert stranger_view.status_code == 403


# ---------- review ----------


def _pending_proposal(app, engine, value: float = 11.0):
    order_id, _, doctor = _complete(app, engine, value=value)
    proposal = (
        app.test_client().post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor).get_json()
    )
    return proposal["id"], order_id, doctor


def test_accepting_is_final_and_keeps_the_original_proposal(app, engine) -> None:
    proposal_id, order_id, doctor = _pending_proposal(app, engine)
    client = app.test_client()
    before = client.get(f"/api/v1/lab/orders/{order_id}/proposal", headers=doctor).get_json()

    accepted = client.post(
        f"/api/v1/clinical/proposals/{proposal_id}/reviews",
        json={"decision": "ACEPTAR"},
        headers=doctor,
    )
    again = client.post(
        f"/api/v1/clinical/proposals/{proposal_id}/reviews",
        json={"decision": "RECHAZAR", "motivo": "Cambio tardio"},
        headers=doctor,
    )
    after = client.get(f"/api/v1/lab/orders/{order_id}/proposal", headers=doctor).get_json()

    assert accepted.status_code == 201
    assert again.status_code == 409
    assert after["estado"] == "ACEPTADA"
    assert after["decision"] == "ACEPTAR"
    assert after["resumen"] == before["resumen"]
    assert [f["explicacion"] for f in after["hallazgos"]] == [
        f["explicacion"] for f in before["hallazgos"]
    ]


def test_correction_and_rejection_need_their_text(app, engine) -> None:
    proposal_id, _, doctor = _pending_proposal(app, engine)
    client = app.test_client()

    no_correction = client.post(
        f"/api/v1/clinical/proposals/{proposal_id}/reviews",
        json={"decision": "CORREGIR"},
        headers=doctor,
    )
    no_reason = client.post(
        f"/api/v1/clinical/proposals/{proposal_id}/reviews",
        json={"decision": "RECHAZAR"},
        headers=doctor,
    )

    assert no_correction.status_code == 422
    assert no_reason.status_code == 422


def test_parallel_reviews_leave_exactly_one_decision(app, engine) -> None:
    proposal_id, _, doctor = _pending_proposal(app, engine)

    def attempt(_: int) -> int:
        return (
            app.test_client()
            .post(
                f"/api/v1/clinical/proposals/{proposal_id}/reviews",
                json={"decision": "ACEPTAR"},
                headers=doctor,
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(attempt, range(6)))

    assert statuses.count(201) == 1, statuses
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM propuestas_revisiones WHERE propuesta_id = :p",
            p=proposal_id,
        )
        == 1
    )


def test_only_the_ordering_doctor_reviews(app, engine) -> None:
    proposal_id, _, _ = _pending_proposal(app, engine)

    stranger = app.test_client().post(
        f"/api/v1/clinical/proposals/{proposal_id}/reviews",
        json={"decision": "ACEPTAR"},
        headers=_token(app, _doctor(engine)),
    )

    assert stranger.status_code == 403


def test_denied_proposal_reads_and_evaluations_are_audited(app, engine) -> None:
    order_id, patient, doctor = _complete(app, engine, value=11.0)
    app.test_client().post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor)

    _proposal(app, order_id, _token(app, patient))

    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad_id = :o "
            "AND accion = 'consultar' AND resultado = 'DENEGADO'",
            o=order_id,
        )
        >= 1
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad_id = :o AND accion = 'evaluar'",
            o=order_id,
        )
        == 1
    )


GLU_SEEDED = "a0000000-0000-4000-8000-00000000b002"  # synthetic glucose, seed 0006


def _order_with_test(app, engine, code: str):
    """An order for one specific synthetic test type. Returns (order_id, doctor_headers)."""
    patient = _user(engine, "PACIENTE")
    doctor = _doctor(engine)
    headers = _token(app, doctor)
    response = _create_order(
        app.test_client(), headers, _patient_id(engine, patient), [_test_type(engine, code)]
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"], headers


def test_a_result_for_an_analyte_outside_the_ordered_test_is_rejected(app, engine) -> None:
    order_id, doctor_headers = _order_with_test(app, engine, "HEMO-SINT")
    detail = _detail(app, order_id, doctor_headers)
    before = _count(engine, "SELECT COUNT(*) FROM resultados_laboratorio")

    response = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json={
            "detalle_id": detail,
            "resultados": [
                {
                    "nombre_analito": "Glucosa",
                    "valor_numerico": 95.0,
                    "unidad": "mg/dL",
                    "analito_id": GLU_SEEDED,
                }
            ],
        },
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )

    assert response.status_code == 422
    assert _count(engine, "SELECT COUNT(*) FROM resultados_laboratorio") == before


def test_glucose_outside_the_range_fires_the_seeded_fuera_rule(app, engine) -> None:
    order_id, doctor_headers = _order_with_test(app, engine, "GLUC-SINT")
    detail = _detail(app, order_id, doctor_headers)
    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json={
            "detalle_id": detail,
            "resultados": [
                {
                    "nombre_analito": "Glucosa",
                    "valor_numerico": 120.0,
                    "unidad": "mg/dL",
                    "analito_id": GLU_SEEDED,
                    "contexto_muestra": "AYUNO",
                }
            ],
        },
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )

    body = (
        app.test_client()
        .post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor_headers)
        .get_json()
    )

    assert "GLU-FUERA-SINT" in [f["regla_codigo"] for f in body["hallazgos"]]


def test_glucose_without_known_fasting_context_does_not_fire_the_fasting_rule(app, engine) -> None:
    order_id, doctor_headers = _order_with_test(app, engine, "GLUC-SINT")
    detail = _detail(app, order_id, doctor_headers)
    app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json={
            "detalle_id": detail,
            "resultados": [
                {
                    "nombre_analito": "Glucosa",
                    "valor_numerico": 120.0,
                    "unidad": "mg/dL",
                    "analito_id": GLU_SEEDED,
                }
            ],
        },
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )

    body = (
        app.test_client()
        .post(f"/api/v1/lab/orders/{order_id}/evaluate", headers=doctor_headers)
        .get_json()
    )

    assert "GLU-FUERA-SINT" not in [f["regla_codigo"] for f in body["hallazgos"]]


def test_unknown_analyte_in_a_lab_result_is_422_and_stores_nothing(app, engine) -> None:
    order_id, _, _, doctor_headers = _order_for(app, engine)
    detail = _detail(app, order_id, doctor_headers)
    before = _count(engine, "SELECT COUNT(*) FROM resultados_laboratorio")

    response = app.test_client().post(
        f"/api/v1/lab/orders/{order_id}/results",
        json={
            "detalle_id": detail,
            "resultados": [
                {
                    "nombre_analito": "Fantasma",
                    "valor_numerico": 10.0,
                    "unidad": "g/dL",
                    "analito_id": str(uuid.uuid4()),
                }
            ],
        },
        headers=_token(app, _user(engine, "LABORATORISTA")),
    )

    assert response.status_code == 422
    assert _count(engine, "SELECT COUNT(*) FROM resultados_laboratorio") == before


def test_the_database_keeps_one_proposal_per_order(engine) -> None:
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM sys.indexes WHERE name = N'UX_propuestas_orden' "
            "AND is_unique = 1",
        )
        == 1
    )


def test_the_seed_has_exactly_the_two_synthetic_validated_rules(engine) -> None:
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM conocimiento_reglas WHERE estado = 'VALIDADA' "
            "AND codigo IN ('HB-BAJA-SINT', 'GLU-FUERA-SINT')",
        )
        == 2
    )


def test_a_numeric_criterion_without_unit_is_rejected(app, engine) -> None:
    response = app.test_client().post(
        "/api/v1/knowledge/rules",
        json={
            "codigo": "REGLA-SIN-UNIDAD",
            "nombre": "Regla sin unidad",
            "tipo": "HALLAZGO",
            "expresion_logica": "TODOS",
            "mensaje": "Mensaje",
            "criterios": [{"analito_id": HB_SEEDED, "operador": "LT", "umbral": 12.0}],
        },
        headers=_token(app, _user(engine, "ADMINISTRADOR")),
    )

    assert response.status_code == 422


def test_a_test_completes_only_when_every_mandatory_analyte_is_reported(app, engine) -> None:
    code = f"PANEL-{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        panel = conn.execute(
            text(
                "INSERT INTO tipos_prueba_laboratorio (id, codigo, nombre, activo) "
                "OUTPUT inserted.id VALUES (NEWID(), :c, N'Panel de prueba', 1)"
            ),
            {"c": code},
        ).scalar_one()
        for analito in (HB_SEEDED, GLU_SEEDED):
            conn.execute(
                text(
                    "INSERT INTO conocimiento_pruebas_analitos (tipo_prueba_id, analito_id, "
                    "obligatorio, orden_presentacion) VALUES (:t, :a, 1, 1)"
                ),
                {"t": panel, "a": analito},
            )
    order_id, doctor_headers = _order_with_test(app, engine, code)
    detail = _detail(app, order_id, doctor_headers)
    lab = _token(app, _user(engine, "LABORATORISTA"))
    client = app.test_client()

    def report(analito: str, value: float, unit: str):
        return client.post(
            f"/api/v1/lab/orders/{order_id}/results",
            json={
                "detalle_id": detail,
                "resultados": [
                    {
                        "nombre_analito": "Analito del panel",
                        "valor_numerico": value,
                        "unidad": unit,
                        "analito_id": analito,
                    }
                ],
            },
            headers=lab,
        )

    first = report(HB_SEEDED, 14.0, "g/dL")
    repeated = report(HB_SEEDED, 15.0, "g/dL")
    partial_state = _count(
        engine,
        "SELECT COUNT(*) FROM ordenes_laboratorio_detalle WHERE id = :d AND estado = 'EN_PROCESO'",
        d=detail,
    )
    second = report(GLU_SEEDED, 90.0, "mg/dL")

    assert first.status_code == 200
    assert first.get_json()["estado"] == "EN_PROCESO"
    assert repeated.status_code == 409
    assert partial_state == 1
    assert second.status_code == 200
    assert second.get_json()["estado"] == "COMPLETADA"
