"""Shadow edit history (feature doc S7) against real SQL Server.

The read is append-only, permission- and ownership-checked, and audited. A missing snapshot,
finding or order answers 403 with the same body as a foreign order.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration import test_shadow_api as shadow_api
from tests.integration.test_scheduling_api import _count, _token, _user
from tests.integration.test_shadow_api import (
    _edit,
    _order_with_hemoglobin,
    _shadow,
    _snapshot_with_anemia,
    _with_profile,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"

# The shared fixture: a test analyte with the hemoglobin LOINC, released after each test.
hemoglobin_analyte = shadow_api.hemoglobin_analyte


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _history(app, order_id: str, snapshot_id: str, hallazgo: str, headers):
    return app.test_client().get(
        f"/api/v1/lab/orders/{order_id}/sombra/snapshots/{snapshot_id}/hallazgos/{hallazgo}"
        "/ediciones",
        headers=headers,
    )


def test_the_owner_reads_the_edits_of_a_finding_in_creation_order(
    app, engine, hemoglobin_analyte
) -> None:
    order_id, headers, snapshot = _snapshot_with_anemia(app, engine, hemoglobin_analyte)
    assert (
        _edit(
            app,
            order_id,
            snapshot["id"],
            "ANEMIA",
            headers,
            {"accion": "CONFIRMAR", "motivo": "ok"},
        ).status_code
        == 201
    )
    assert (
        _edit(
            app,
            order_id,
            snapshot["id"],
            "ANEMIA",
            headers,
            {"accion": "MODIFICAR", "categoria_nueva": "SIN_ANEMIA", "motivo": "altitud"},
        ).status_code
        == 201
    )

    response = _history(app, order_id, snapshot["id"], "ANEMIA", headers)

    assert response.status_code == 200, response.get_json()
    items = response.get_json()["items"]
    assert [item["accion"] for item in items] == ["CONFIRMAR", "MODIFICAR"]
    assert items[1]["categoria_nueva"] == "SIN_ANEMIA"
    assert items[1]["motivo"] == "altitud"
    assert set(items[0]) == {
        "id",
        "accion",
        "categoria_anterior",
        "categoria_nueva",
        "motivo",
        "medico_user_id",
        "creado_en",
    }
    assert items[0]["creado_en"].endswith("+00:00")


def test_a_finding_without_edits_has_an_empty_history(app, engine, hemoglobin_analyte) -> None:
    order_id, headers, snapshot = _snapshot_with_anemia(app, engine, hemoglobin_analyte)

    response = _history(app, order_id, snapshot["id"], "ANEMIA", headers)

    assert response.status_code == 200
    assert response.get_json() == {"items": []}


def test_a_foreign_doctor_and_a_missing_finding_get_the_same_403(
    app, engine, hemoglobin_analyte
) -> None:
    order_id, headers, snapshot = _snapshot_with_anemia(app, engine, hemoglobin_analyte)
    other_doctor = _token(app, _user(engine, "MEDICO"))

    foreign = _history(app, order_id, snapshot["id"], "ANEMIA", other_doctor)
    missing = _history(app, order_id, snapshot["id"], "TIROIDES", headers)

    assert foreign.status_code == missing.status_code == 403
    # correlation_id is per request; the rest of the body must be identical.
    foreign_body = foreign.get_json()
    missing_body = missing.get_json()
    foreign_body.pop("correlation_id")
    missing_body.pop("correlation_id")
    assert foreign_body == missing_body


def test_a_snapshot_of_another_order_gets_the_same_403(app, engine, hemoglobin_analyte) -> None:
    _, _, snapshot = _snapshot_with_anemia(app, engine, hemoglobin_analyte)
    other_order, _, other_headers = _order_with_hemoglobin(app, engine, hemoglobin_analyte, 13.0)

    response = _history(app, other_order, snapshot["id"], "ANEMIA", other_headers)

    assert response.status_code == 403


def test_a_patient_cannot_read_the_history(app, engine, hemoglobin_analyte) -> None:
    order_id, _, snapshot = _snapshot_with_anemia(app, engine, hemoglobin_analyte)
    patient = _token(app, _user(engine, "PACIENTE"))

    response = _history(app, order_id, snapshot["id"], "ANEMIA", patient)

    assert response.status_code == 403


def test_reads_are_audited_with_their_outcome(app, engine, hemoglobin_analyte) -> None:
    order_id, headers, snapshot = _snapshot_with_anemia(app, engine, hemoglobin_analyte)
    other_doctor = _token(app, _user(engine, "MEDICO"))

    assert _history(app, order_id, snapshot["id"], "ANEMIA", headers).status_code == 200
    assert _history(app, order_id, snapshot["id"], "ANEMIA", other_doctor).status_code == 403

    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad = 'hallazgo_sombra_ediciones' "
            "AND entidad_id = :o AND resultado = 'OK'",
            o=order_id,
        )
        == 1
    )
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM auditoria_accesos WHERE entidad = 'hallazgo_sombra_ediciones' "
            "AND entidad_id = :o AND resultado = 'DENEGADO'",
            o=order_id,
        )
        == 1
    )


def test_reading_the_history_changes_no_row(app, engine, hemoglobin_analyte) -> None:
    order_id, headers, snapshot = _snapshot_with_anemia(app, engine, hemoglobin_analyte)
    _edit(app, order_id, snapshot["id"], "ANEMIA", headers, {"accion": "CONFIRMAR", "motivo": "ok"})
    count_sql = "SELECT COUNT(*) FROM hallazgos_sombra_ediciones WHERE snapshot_id = :s"

    _history(app, order_id, snapshot["id"], "ANEMIA", headers)
    _history(app, order_id, snapshot["id"], "ANEMIA", headers)

    assert _count(engine, count_sql, s=snapshot["id"]) == 1


def test_the_edit_route_and_the_shadow_read_keep_their_responses(
    app, engine, hemoglobin_analyte
) -> None:
    """PRESERVE: POST shadow edits and GET sombra are unchanged by the new history read."""
    order_id, headers, snapshot = _snapshot_with_anemia(app, engine, hemoglobin_analyte)
    _with_profile(engine, order_id)

    edit = _edit(
        app, order_id, snapshot["id"], "ANEMIA", headers, {"accion": "CONFIRMAR", "motivo": "ok"}
    )
    shadow = _shadow(app, order_id, headers)

    assert edit.status_code == 201
    assert set(edit.get_json()) == {
        "id",
        "snapshot_id",
        "hallazgo",
        "accion",
        "categoria_guardada",
    }
    assert shadow.status_code == 200
    assert shadow.get_json()["modifica_decision"] is False
