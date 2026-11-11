"""Order listing (S5) against real SQL Server: access, ordering, paging, state filter, HTTP codes.

Each test creates its own users and orders, so counts do not depend on other tests.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_lab_api import _doctor, _patient_id, _test_type
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
LIST_URL = "/api/v1/lab/orders"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Iterator[Flask]:
    documents = tmp_path_factory.mktemp("lab-listing-documents")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _create(app: Flask, doctor_headers: dict, patient_id: str) -> str:
    test_type = _test_type(app.extensions["medicalbooking.engine"])
    response = app.test_client().post(
        "/api/v1/lab/orders",
        json={"paciente_id": patient_id, "tipo_prueba_ids": [test_type]},
        headers=doctor_headers,
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def _set(engine: Engine, order_id: str, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE ordenes_laboratorio SET {assignments} WHERE id = :o"),  # noqa: S608
            {**columns, "o": order_id},
        )


def _listed(app: Flask, headers: dict, query: str = "") -> list[dict]:
    response = app.test_client().get(f"{LIST_URL}{query}", headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["items"]


# ---------- access ----------


def test_patient_lists_own_orders_and_not_other_patients(app, engine) -> None:
    doctor = _doctor(engine)
    doctor_headers = _token(app, doctor)
    mine_user = _user(engine, "PACIENTE")
    other_user = _user(engine, "PACIENTE")
    mine = _create(app, doctor_headers, _patient_id(engine, mine_user))
    theirs = _create(app, doctor_headers, _patient_id(engine, other_user))

    listed = _listed(app, _token(app, mine_user))

    ids = [item["id"] for item in listed]
    assert mine in ids
    assert theirs not in ids


def test_ordering_doctor_lists_only_orders_they_placed(app, engine) -> None:
    patient = _patient_id(engine, _user(engine, "PACIENTE"))
    doctor = _doctor(engine)
    other_doctor = _doctor(engine)
    own = _create(app, _token(app, doctor), patient)
    foreign = _create(app, _token(app, other_doctor), patient)

    ids = [item["id"] for item in _listed(app, _token(app, doctor))]

    assert own in ids
    assert foreign not in ids


def test_lab_staff_without_order_access_gets_403(app, engine) -> None:
    response = app.test_client().get(LIST_URL, headers=_token(app, _user(engine, "LABORATORISTA")))

    assert response.status_code == 403


# ---------- ordering, paging, filter ----------


def test_orders_are_listed_newest_first_with_id_as_tiebreak(app, engine) -> None:
    patient = _patient_id(engine, _user(engine, "PACIENTE"))
    doctor = _doctor(engine)
    headers = _token(app, doctor)
    older = _create(app, headers, patient)
    tie_a = _create(app, headers, patient)
    tie_b = _create(app, headers, patient)
    _set(engine, older, fecha_orden=datetime(2026, 1, 1, 8, 0))
    _set(engine, tie_a, fecha_orden=datetime(2026, 3, 1, 8, 0))
    _set(engine, tie_b, fecha_orden=datetime(2026, 3, 1, 8, 0))

    items = _listed(app, headers)
    ordered = [item["id"] for item in items if item["id"] in {older, tie_a, tie_b}]

    dates = [item["fecha"] for item in items if item["id"] in {older, tie_a, tie_b}]
    assert dates == sorted(dates, reverse=True)
    with engine.connect() as conn:
        tied = [
            str(uuid.UUID(str(row.id)))
            for row in conn.execute(
                text("SELECT id FROM ordenes_laboratorio WHERE id IN (:a, :b) ORDER BY id ASC"),
                {"a": tie_a, "b": tie_b},
            ).all()
        ]
    assert ordered[-1] == older
    assert ordered[:2] == tied


def test_estado_filter_returns_only_that_state(app, engine) -> None:
    patient = _patient_id(engine, _user(engine, "PACIENTE"))
    doctor = _doctor(engine)
    headers = _token(app, doctor)
    completed = _create(app, headers, patient)
    pending = _create(app, headers, patient)
    _set(engine, completed, estado="COMPLETADA")

    items = _listed(app, headers, "?estado=COMPLETADA")

    ids = [item["id"] for item in items]
    assert completed in ids
    assert pending not in ids
    assert all(item["estado"] == "COMPLETADA" for item in items)


def test_limit_and_offset_split_the_list_without_overlap(app, engine) -> None:
    patient = _patient_id(engine, _user(engine, "PACIENTE"))
    doctor = _doctor(engine)
    headers = _token(app, doctor)
    created = {_create(app, headers, patient) for _ in range(3)}

    first = [item["id"] for item in _listed(app, headers, "?limit=2&offset=0")]
    second = [item["id"] for item in _listed(app, headers, "?limit=2&offset=2")]
    everything = [item["id"] for item in _listed(app, headers)]

    assert len(first) == 2
    assert set(first).isdisjoint(second)
    assert created <= set(everything)
    assert set(first) | set(second) <= set(everything)


def test_listing_rows_never_carry_result_values(app, engine) -> None:
    patient = _patient_id(engine, _user(engine, "PACIENTE"))
    headers = _token(app, _doctor(engine))
    _create(app, headers, patient)

    items = _listed(app, headers)

    assert items
    assert all(set(item) == {"id", "paciente_id", "estado", "fecha"} for item in items)


# ---------- HTTP codes ----------


@pytest.mark.parametrize("query", ["?estado=ARCHIVADA", "?limit=0", "?limit=201", "?offset=-1"])
def test_invalid_listing_query_is_422(app, engine, query) -> None:
    response = app.test_client().get(
        f"{LIST_URL}{query}", headers=_token(app, _user(engine, "PACIENTE"))
    )

    assert response.status_code == 422


# ---------- PRESERVE ----------


def test_get_order_keeps_its_response_shape_over_sql(app, engine) -> None:
    patient_user = _user(engine, "PACIENTE")
    doctor_headers = _token(app, _doctor(engine))
    order_id = _create(app, doctor_headers, _patient_id(engine, patient_user))

    response = app.test_client().get(
        f"/api/v1/lab/orders/{order_id}", headers=_token(app, patient_user)
    )

    body = response.get_json()
    assert response.status_code == 200
    assert set(body) == {"id", "paciente_id", "prioridad", "estado", "observaciones", "pruebas"}
    assert set(body["pruebas"][0]) == {"detalle_id", "tipo_prueba_id", "estado"}
