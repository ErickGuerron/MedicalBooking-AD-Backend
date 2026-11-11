"""Order listing (S5) through the service and the HTTP contract, with in-memory fakes.

The SQL ordering, paging and state filter are pinned by the integration suite; here the
rules are access per actor, query validation and the response shape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime

import pytest
from flask import Flask, jsonify

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import LabDetail, LabOrder, NewOrder
from medicalbooking.modules.laboratory.application.use_cases import LabService
from medicalbooking.modules.laboratory.presentation.routes import lab_bp
from medicalbooking.shared.domain.errors import AppError, Forbidden, Unauthenticated

NOW = datetime(2026, 11, 2, 9, 0)  # naive UTC, as the adapter stores fecha_orden
LIST_URL = "/api/v1/lab/orders"
TEST_TYPE = str(uuid.UUID("a0000000-0000-4000-8000-00000000c001"))

PACIENTE = frozenset({"resultados.leer_propios", "citas.reservar"})
MEDICO = frozenset({"ordenes.crear", "resultados.leer_con_acceso", "revisiones.registrar"})
LABORATORISTA = frozenset({"resultados.registrar"})


def _id() -> str:
    return str(uuid.uuid4())


def _actor(permissions: frozenset[str], user_id: str | None = None) -> AccessProfile:
    return AccessProfile(
        user_id=user_id or _id(),
        status="ACTIVO",
        roles=frozenset(),
        permissions=permissions,
    )


@dataclass(frozen=True)
class _Row:
    """Duck-typed listing row: the service returns whatever the store returns."""

    id: str
    paciente_id: str
    estado: str
    fecha: datetime


@dataclass
class FakeStore:
    patient_by_user: dict[str, str] = field(default_factory=dict)
    medico_by_user: dict[str, str] = field(default_factory=dict)
    orders: list[LabOrder] = field(default_factory=list)
    fecha_by_order: dict[str, datetime] = field(default_factory=dict)
    list_calls: list[dict] = field(default_factory=list)

    def add(self, order: LabOrder, fecha: datetime = NOW) -> LabOrder:
        # The adapter joins the owning user ids; the fake fills them the same way.
        order = replace(
            order,
            paciente_user_id=_owner(self.patient_by_user, order.paciente_id),
            medico_user_id=_owner(self.medico_by_user, order.medico_id),
        )
        self.orders.append(order)
        self.fecha_by_order[order.id] = fecha
        return order

    def resolve_patient_id(self, user_id):
        return self.patient_by_user.get(user_id)

    def resolve_medico_id(self, user_id):
        return self.medico_by_user.get(user_id)

    def patient_exists(self, paciente_id):
        return paciente_id in self.patient_by_user.values()

    def active_test_ids(self, ids):
        return set(ids)

    def create_order(self, command: NewOrder) -> LabOrder:
        order = LabOrder(
            id=command.order_id,
            paciente_id=command.paciente_id,
            paciente_user_id=_owner(self.patient_by_user, command.paciente_id),
            medico_id=command.medico_id,
            medico_user_id=_owner(self.medico_by_user, command.medico_id),
            prioridad=command.prioridad,
            estado="SOLICITADA",
            observaciones=command.observaciones,
            details=tuple(
                LabDetail(id=_id(), tipo_prueba_id=test, estado="PENDIENTE")
                for test in command.tipo_prueba_ids
            ),
        )
        return self.add(order)

    def get_order(self, order_id):
        return next((o for o in self.orders if o.id == order_id), None)

    def record_audit(self, event):
        return None

    def list_orders(self, *, paciente_id, medico_id, estado, limit, offset):
        self.list_calls.append(
            {
                "paciente_id": paciente_id,
                "medico_id": medico_id,
                "estado": estado,
                "limit": limit,
                "offset": offset,
            }
        )
        rows = [o for o in self.orders if o.paciente_id == paciente_id or o.medico_id == medico_id]
        if estado is not None:
            rows = [o for o in rows if o.estado == estado]
        return [
            _Row(o.id, o.paciente_id, o.estado, self.fecha_by_order[o.id])
            for o in rows[offset : offset + limit]
        ]


def _owner(by_user: dict[str, str], entity_id: str) -> str:
    return next(user for user, value in by_user.items() if value == entity_id)


def _order(paciente_id: str, medico_id: str, *, estado: str = "SOLICITADA") -> LabOrder:
    return LabOrder(
        id=_id(),
        paciente_id=paciente_id,
        paciente_user_id="",
        medico_id=medico_id,
        medico_user_id="",
        prioridad="NORMAL",
        estado=estado,
        observaciones=None,
        details=(LabDetail(id=_id(), tipo_prueba_id=TEST_TYPE, estado="PENDIENTE"),),
    )


@dataclass
class World:
    store: FakeStore
    patient: AccessProfile
    patient_id: str
    other_patient_id: str
    doctor: AccessProfile
    doctor_id: str
    other_doctor_id: str
    lab: AccessProfile


@pytest.fixture
def world() -> World:
    store = FakeStore()
    patient = _actor(PACIENTE)
    doctor = _actor(MEDICO)
    other_patient = _actor(PACIENTE)
    other_doctor = _actor(MEDICO)
    store.patient_by_user[patient.user_id] = _id()
    store.patient_by_user[other_patient.user_id] = _id()
    store.medico_by_user[doctor.user_id] = _id()
    store.medico_by_user[other_doctor.user_id] = _id()
    return World(
        store=store,
        patient=patient,
        patient_id=store.patient_by_user[patient.user_id],
        other_patient_id=store.patient_by_user[other_patient.user_id],
        doctor=doctor,
        doctor_id=store.medico_by_user[doctor.user_id],
        other_doctor_id=store.medico_by_user[other_doctor.user_id],
        lab=_actor(LABORATORISTA),
    )


def _service(world: World) -> LabService:
    return LabService(store=world.store, storage=None, clock=lambda: NOW)


# ---------- access rules (service) ----------


def test_patient_lists_only_own_orders(world) -> None:
    own = world.store.add(_order(world.patient_id, world.doctor_id))
    world.store.add(_order(world.other_patient_id, world.doctor_id))

    items = _service(world).list_orders(world.patient, estado=None, limit=50, offset=0)

    assert [item.id for item in items] == [own.id]


def test_ordering_doctor_lists_only_orders_they_placed(world) -> None:
    own = world.store.add(_order(world.patient_id, world.doctor_id))
    world.store.add(_order(world.patient_id, world.other_doctor_id))

    items = _service(world).list_orders(world.doctor, estado=None, limit=50, offset=0)

    assert [item.id for item in items] == [own.id]


def test_actor_without_order_read_access_is_forbidden(world) -> None:
    with pytest.raises(Forbidden):
        _service(world).list_orders(world.lab, estado=None, limit=50, offset=0)


def test_doctor_permission_without_doctor_profile_is_forbidden(world) -> None:
    nobody = _actor(MEDICO)

    with pytest.raises(Forbidden):
        _service(world).list_orders(nobody, estado=None, limit=50, offset=0)


def test_patient_permission_without_patient_profile_is_forbidden(world) -> None:
    nobody = _actor(PACIENTE)

    with pytest.raises(Forbidden):
        _service(world).list_orders(nobody, estado=None, limit=50, offset=0)


# ---------- HTTP contract ----------


class _Tokens:
    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self._profiles = profiles

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self._profiles:
            raise Unauthenticated("Authentication required")
        return self._profiles[token]


def _http(world: World):
    app = Flask("test-lab-listing")
    app.register_blueprint(lab_bp)
    app.extensions["medicalbooking.laboratory"] = _service(world)
    app.extensions["medicalbooking.identity"] = _Tokens(
        {
            "tok-patient": world.patient,
            "tok-doctor": world.doctor,
            "tok-lab": world.lab,
        }
    )

    @app.errorhandler(AppError)
    def _domain_error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status, "detail": error.detail}), error.status

    return app.test_client()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_listing_requires_a_bearer_token(world) -> None:
    assert _http(world).get(LIST_URL).status_code == 401


def test_listing_answers_403_for_an_actor_without_access(world) -> None:
    response = _http(world).get(LIST_URL, headers=_bearer("tok-lab"))

    assert response.status_code == 403


def test_listing_rows_carry_only_identifiers_state_and_date(world) -> None:
    world.store.add(_order(world.patient_id, world.doctor_id))

    response = _http(world).get(LIST_URL, headers=_bearer("tok-patient"))

    body = response.get_json()
    assert response.status_code == 200
    assert set(body) == {"items"}
    assert set(body["items"][0]) == {"id", "paciente_id", "estado", "fecha"}


def test_listing_dates_are_iso_8601_utc(world) -> None:
    world.store.add(_order(world.patient_id, world.doctor_id))

    response = _http(world).get(LIST_URL, headers=_bearer("tok-patient"))

    assert response.get_json()["items"][0]["fecha"] == "2026-11-02T09:00:00Z"


def test_estado_filter_is_passed_to_the_store_and_applied(world) -> None:
    world.store.add(_order(world.patient_id, world.doctor_id, estado="COMPLETADA"))
    pending = world.store.add(_order(world.patient_id, world.doctor_id, estado="SOLICITADA"))

    response = _http(world).get(f"{LIST_URL}?estado=SOLICITADA", headers=_bearer("tok-patient"))

    assert [item["id"] for item in response.get_json()["items"]] == [pending.id]
    assert world.store.list_calls[-1]["estado"] == "SOLICITADA"


@pytest.mark.parametrize("estado", ["ARCHIVADA", "solicitada", ""])
def test_unknown_estado_is_422(world, estado) -> None:
    response = _http(world).get(f"{LIST_URL}?estado={estado}", headers=_bearer("tok-patient"))

    assert response.status_code == 422


def test_limit_defaults_to_50_and_offset_to_0(world) -> None:
    _http(world).get(LIST_URL, headers=_bearer("tok-patient"))

    assert world.store.list_calls[-1]["limit"] == 50
    assert world.store.list_calls[-1]["offset"] == 0


@pytest.mark.parametrize("limit", ["0", "201", "abc"])
def test_limit_must_be_between_1_and_200(world, limit) -> None:
    response = _http(world).get(f"{LIST_URL}?limit={limit}", headers=_bearer("tok-patient"))

    assert response.status_code == 422


def test_limit_of_200_is_accepted(world) -> None:
    response = _http(world).get(f"{LIST_URL}?limit=200", headers=_bearer("tok-patient"))

    assert response.status_code == 200
    assert world.store.list_calls[-1]["limit"] == 200


@pytest.mark.parametrize("offset", ["-1", "x"])
def test_offset_must_be_a_non_negative_integer(world, offset) -> None:
    response = _http(world).get(f"{LIST_URL}?offset={offset}", headers=_bearer("tok-patient"))

    assert response.status_code == 422


# ---------- PRESERVE: existing order routes keep their responses ----------


def test_create_order_response_keeps_its_shape(world) -> None:
    response = _http(world).post(
        LIST_URL,
        json={"paciente_id": world.patient_id, "tipo_prueba_ids": [TEST_TYPE]},
        headers=_bearer("tok-doctor"),
    )

    body = response.get_json()
    assert response.status_code == 201
    assert set(body) == {"id", "paciente_id", "prioridad", "estado", "observaciones", "pruebas"}
    assert set(body["pruebas"][0]) == {"detalle_id", "tipo_prueba_id", "estado"}


def test_get_order_response_keeps_its_shape(world) -> None:
    order = world.store.add(_order(world.patient_id, world.doctor_id))

    response = _http(world).get(f"{LIST_URL}/{order.id}", headers=_bearer("tok-patient"))

    body = response.get_json()
    assert response.status_code == 200
    assert set(body) == {"id", "paciente_id", "prioridad", "estado", "observaciones", "pruebas"}
    assert set(body["pruebas"][0]) == {"detalle_id", "tipo_prueba_id", "estado"}
