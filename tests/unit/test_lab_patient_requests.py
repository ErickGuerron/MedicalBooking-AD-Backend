"""Patient-requested lab exams and the laboratory worklist: rules, HTTP contract and row grouping.

Rules run against an in-memory store; the SQL grouping runs against a statement-capturing engine.
The database behaviour is pinned by tests/integration/test_patient_requested_orders_api.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import LabDetail, LabOrder, NewOrder
from medicalbooking.modules.laboratory.application.use_cases import LabService
from medicalbooking.modules.laboratory.infrastructure.repositories import SqlLabStore
from medicalbooking.modules.laboratory.presentation.routes import lab_bp
from medicalbooking.shared.domain.errors import (
    AppError,
    Forbidden,
    Unauthenticated,
    ValidationFailed,
)

NOW = datetime(2026, 11, 15, 9, 0)
REQUEST_URL = "/api/v1/lab/patient-orders"
WORKLIST_URL = "/api/v1/lab/worklist"
FORBIDDEN = "Access to this resource is not allowed"

PATIENT_REQUEST = frozenset({"ordenes.solicitar_propias", "resultados.leer_propios"})
DOCTOR = frozenset({"ordenes.crear", "resultados.leer_con_acceso", "revisiones.registrar"})
LABORATORY = frozenset({"resultados.registrar"})
NOBODY = frozenset()


def _id() -> str:
    return str(uuid.uuid4())


def _actor(permissions: frozenset[str]) -> AccessProfile:
    return AccessProfile(user_id=_id(), status="ACTIVO", roles=frozenset(), permissions=permissions)


@dataclass
class FakeStore:
    patient_profile_id: str | None = None
    medico_profile_id: str | None = None
    active_tests: set[str] = field(default_factory=set)
    pending: list[LabOrder] = field(default_factory=list)
    created: list[NewOrder] = field(default_factory=list)
    worklist_calls: list[tuple[int, int]] = field(default_factory=list)
    audit: list = field(default_factory=list)

    def resolve_patient_id(self, user_id):
        return self.patient_profile_id

    def resolve_medico_id(self, user_id):
        return self.medico_profile_id

    def patient_is_related(self, medico_id, paciente_id):
        return True

    def patient_account_is_active(self, paciente_id):
        return True

    def active_test_ids(self, ids):
        return {i for i in ids if i in self.active_tests}

    def create_order(self, command: NewOrder) -> LabOrder:
        self.created.append(command)
        return LabOrder(
            id=command.order_id,
            paciente_id=command.paciente_id,
            paciente_user_id=_id(),
            medico_id=command.medico_id,
            medico_user_id=None,
            prioridad=command.prioridad,
            estado="SOLICITADA",
            observaciones=command.observaciones,
            details=tuple(
                LabDetail(id=_id(), tipo_prueba_id=t, estado="PENDIENTE")
                for t in command.tipo_prueba_ids
            ),
            origen=command.origen,
        )

    def list_pending_orders(self, *, limit: int, offset: int) -> list[LabOrder]:
        self.worklist_calls.append((limit, offset))
        return self.pending[offset : offset + limit]

    def get_order(self, order_id):
        return None

    def record_audit(self, event) -> None:
        self.audit.append(event)


def _service(store: FakeStore) -> LabService:
    return LabService(store=store, storage=None, clock=lambda: NOW)


def _pending_order(origen: str = "PACIENTE") -> LabOrder:
    return LabOrder(
        id=_id(),
        paciente_id=_id(),
        paciente_user_id=_id(),
        medico_id=None if origen == "PACIENTE" else _id(),
        medico_user_id=None,
        prioridad="NORMAL",
        estado="SOLICITADA",
        observaciones=None,
        details=(LabDetail(id=_id(), tipo_prueba_id=_id(), estado="PENDIENTE"),),
        origen=origen,
        fecha=NOW,
    )


# ---------- patient request: rules ----------


def _patient_store(test: str) -> FakeStore:
    return FakeStore(patient_profile_id=_id(), active_tests={test})


def test_patient_request_is_stored_with_origin_patient_and_no_doctor() -> None:
    test = _id()
    store = _patient_store(test)
    actor = _actor(PATIENT_REQUEST)

    order = _service(store).create_patient_order(
        actor, tipo_prueba_ids=[test], observaciones="ayuno"
    )

    [command] = store.created
    assert command.origen == "PACIENTE"
    assert command.medico_id is None
    assert command.prioridad == "NORMAL"
    assert command.paciente_id == store.patient_profile_id
    assert command.observaciones == "ayuno"
    assert order.origen == "PACIENTE"


def test_patient_request_is_audited_as_solicitar_without_values() -> None:
    test = _id()
    store = _patient_store(test)

    _service(store).create_patient_order(
        _actor(PATIENT_REQUEST), tipo_prueba_ids=[test], observaciones="secreto"
    )

    [event] = store.audit
    assert (event.accion, event.entidad, event.resultado) == (
        "solicitar",
        "orden_laboratorio",
        "OK",
    )
    assert event.paciente_id == store.patient_profile_id
    assert "secreto" not in repr(event)


def test_doctor_order_is_stored_with_origin_medico_and_its_doctor() -> None:
    test = _id()
    store = FakeStore(medico_profile_id=_id(), active_tests={test})

    order = _service(store).create_order(
        _actor(DOCTOR),
        paciente_id=_id(),
        prioridad="NORMAL",
        observaciones=None,
        tipo_prueba_ids=[test],
    )

    [command] = store.created
    assert command.origen == "MEDICO"
    assert command.medico_id == store.medico_profile_id
    assert order.origen == "MEDICO"


def test_only_a_patient_with_the_request_permission_can_request() -> None:
    test = _id()
    store = _patient_store(test)

    with pytest.raises(Forbidden) as doctor:
        _service(store).create_patient_order(
            _actor(DOCTOR), tipo_prueba_ids=[test], observaciones=None
        )
    with pytest.raises(Forbidden):
        _service(store).create_patient_order(
            _actor(LABORATORY), tipo_prueba_ids=[test], observaciones=None
        )

    assert doctor.value.detail == FORBIDDEN
    assert store.created == []


def test_a_user_with_the_permission_but_no_patient_profile_gets_the_uniform_403() -> None:
    test = _id()
    store = FakeStore(patient_profile_id=None, active_tests={test})

    with pytest.raises(Forbidden) as error:
        _service(store).create_patient_order(
            _actor(PATIENT_REQUEST), tipo_prueba_ids=[test], observaciones=None
        )

    assert error.value.detail == FORBIDDEN
    assert store.created == []


@pytest.mark.parametrize(
    "tests",
    [[], ["not-a-uuid"]],
    ids=["empty", "not-a-uuid"],
)
def test_invalid_test_lists_are_refused_before_anything_is_stored(tests) -> None:
    store = FakeStore(patient_profile_id=_id())

    with pytest.raises(ValidationFailed):
        _service(store).create_patient_order(
            _actor(PATIENT_REQUEST), tipo_prueba_ids=tests, observaciones=None
        )
    assert store.created == []


def test_an_unknown_or_inactive_test_is_refused() -> None:
    store = FakeStore(patient_profile_id=_id(), active_tests={_id()})

    with pytest.raises(ValidationFailed, match="Unknown or inactive test type"):
        _service(store).create_patient_order(
            _actor(PATIENT_REQUEST), tipo_prueba_ids=[_id()], observaciones=None
        )
    assert store.created == []


def test_duplicate_and_too_many_tests_are_refused() -> None:
    test = _id()
    store = _patient_store(test)

    with pytest.raises(ValidationFailed):
        _service(store).create_patient_order(
            _actor(PATIENT_REQUEST), tipo_prueba_ids=[test, test], observaciones=None
        )
    too_many = [_id() for _ in range(21)]
    with pytest.raises(ValidationFailed):
        _service(store).create_patient_order(
            _actor(PATIENT_REQUEST), tipo_prueba_ids=too_many, observaciones=None
        )
    assert store.created == []


def test_observations_over_1000_characters_are_refused() -> None:
    test = _id()
    store = _patient_store(test)

    with pytest.raises(ValidationFailed):
        _service(store).create_patient_order(
            _actor(PATIENT_REQUEST), tipo_prueba_ids=[test], observaciones="x" * 1001
        )
    assert store.created == []


# ---------- laboratory worklist: rules ----------


def test_the_laboratory_gets_the_pending_page_and_an_audit_without_patient() -> None:
    orders = [_pending_order("PACIENTE"), _pending_order("MEDICO")]
    store = FakeStore(pending=orders)

    rows = _service(store).list_worklist(_actor(LABORATORY), limit=2, offset=0)

    assert rows == orders
    assert store.worklist_calls == [(2, 0)]
    [event] = store.audit
    assert (event.accion, event.entidad_id, event.paciente_id) == ("listar_pendientes", None, None)


@pytest.mark.parametrize(
    "permissions", [PATIENT_REQUEST, DOCTOR, NOBODY], ids=["patient", "doctor", "nobody"]
)
def test_the_worklist_is_refused_without_the_laboratory_permission(permissions) -> None:
    store = FakeStore(pending=[_pending_order()])

    with pytest.raises(Forbidden) as error:
        _service(store).list_worklist(_actor(permissions), limit=50, offset=0)

    assert error.value.detail == FORBIDDEN
    assert store.worklist_calls == []
    assert store.audit == []


# ---------- HTTP contract ----------


class _Tokens:
    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self._profiles = profiles

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self._profiles:
            raise Unauthenticated("Authentication required")
        return self._profiles[token]


@pytest.fixture
def client_and_store():
    store = FakeStore(patient_profile_id=_id(), active_tests={_id()}, pending=[_pending_order()])
    app = Flask("test-lab-patient-requests")
    app.register_blueprint(lab_bp)
    app.extensions["medicalbooking.laboratory"] = _service(store)
    app.extensions["medicalbooking.identity"] = _Tokens(
        {
            "tok-patient": _actor(PATIENT_REQUEST),
            "tok-doctor": _actor(DOCTOR),
            "tok-lab": _actor(LABORATORY),
            "tok-nobody": _actor(NOBODY),
        }
    )

    @app.errorhandler(AppError)
    def _domain_error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status, "detail": error.detail}), error.status

    return app.test_client(), store


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_post_patient_orders_answers_201_with_origin_and_no_doctor(client_and_store) -> None:
    client, store = client_and_store
    test = next(iter(store.active_tests))

    response = client.post(
        REQUEST_URL, json={"tipo_prueba_ids": [test]}, headers=_bearer("tok-patient")
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["origen"] == "PACIENTE"
    assert body["medico_id"] is None
    assert body["prioridad"] == "NORMAL"


def test_post_patient_orders_refuses_fields_the_patient_may_not_choose(client_and_store) -> None:
    client, store = client_and_store
    test = next(iter(store.active_tests))

    for extra in ({"paciente_id": _id()}, {"prioridad": "URGENTE"}):
        response = client.post(
            REQUEST_URL,
            json={"tipo_prueba_ids": [test], **extra},
            headers=_bearer("tok-patient"),
        )
        assert response.status_code == 422, extra
    assert store.created == []


def test_post_patient_orders_needs_a_token_and_the_permission(client_and_store) -> None:
    client, store = client_and_store
    test = next(iter(store.active_tests))
    body = {"tipo_prueba_ids": [test]}

    assert client.post(REQUEST_URL, json=body).status_code == 401
    assert client.post(REQUEST_URL, json=body, headers=_bearer("tok-doctor")).status_code == 403
    assert client.post(REQUEST_URL, json=body, headers=_bearer("tok-lab")).status_code == 403
    assert store.created == []


def test_get_worklist_answers_200_with_origin_and_the_default_page(client_and_store) -> None:
    client, _ = client_and_store

    response = client.get(WORKLIST_URL, headers=_bearer("tok-lab"))

    assert response.status_code == 200
    body = response.get_json()
    assert body["limit"] == 50 and body["offset"] == 0
    assert body["items"][0]["origen"] == "PACIENTE"
    assert body["items"][0]["fecha"] == "2026-11-15T09:00:00Z"
    assert body["items"][0]["pruebas"][0]["detalle_id"]


def test_get_worklist_refuses_patients_doctors_and_strangers(client_and_store) -> None:
    client, _ = client_and_store

    for token in ("tok-patient", "tok-doctor", "tok-nobody"):
        assert client.get(WORKLIST_URL, headers=_bearer(token)).status_code == 403, token
    assert client.get(WORKLIST_URL).status_code == 401


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1"])
def test_get_worklist_answers_422_for_invalid_paging(client_and_store, query) -> None:
    client, _ = client_and_store

    response = client.get(f"{WORKLIST_URL}?{query}", headers=_bearer("tok-lab"))

    assert response.status_code == 422


# ---------- SQL row grouping (statement-capturing engine) ----------


def _row(order_id: str, detalle: str, *, origen: str = "PACIENTE", medico: str | None = None):
    return SimpleNamespace(
        id=order_id,
        paciente_id=_id(),
        paciente_user_id=_id(),
        medico_id=medico,
        medico_user_id=None,
        prioridad="NORMAL",
        estado="SOLICITADA",
        observaciones=None,
        origen=origen,
        fecha_orden=NOW,
        detalle_id=detalle,
        tipo_prueba_id=_id(),
        detalle_estado="PENDIENTE",
    )


class _Result:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, engine: _Engine) -> None:
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, statement, params=None):
        self._engine.calls.append((str(statement), dict(params or {})))
        return _Result(self._engine.results.pop(0))


class _Engine:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    def connect(self):
        return _Conn(self)


def test_worklist_rows_are_grouped_under_their_order_keeping_every_line() -> None:
    first, second = _id(), _id()
    rows = [
        _row(first, _id()),
        _row(first, _id()),
        _row(second, _id(), origen="MEDICO", medico=_id()),
    ]
    engine = _Engine(rows)

    orders = SqlLabStore(engine).list_pending_orders(limit=5, offset=10)

    assert [order.id for order in orders] == [first, second]
    assert [len(order.details) for order in orders] == [2, 1]
    assert orders[0].medico_id is None and orders[0].origen == "PACIENTE"
    assert orders[1].medico_id is not None and orders[1].origen == "MEDICO"


def test_worklist_statement_is_static_and_binds_only_the_paging_values() -> None:
    engine = _Engine([])

    SqlLabStore(engine).list_pending_orders(limit=5, offset=10)

    [(statement, params)] = engine.calls
    assert params == {"skip": 10, "take": 5}
    assert "IN ('SOLICITADA', 'EN_PROCESO')" in statement
    assert "LEFT JOIN medicos" in statement
    assert "FETCH NEXT :take ROWS ONLY" in statement


def test_an_order_with_no_doctor_loads_without_error() -> None:
    order_id = _id()
    header = _row(order_id, _id())
    engine = _Engine([header], [header])

    order = SqlLabStore(engine).get_order(order_id)

    assert order is not None
    assert order.medico_id is None and order.medico_user_id is None
    assert order.origen == "PACIENTE"
