"""Patient directory for lab orders and the active lab test-type catalog, with in-memory fakes.

The SQL ordering, the active-only filter and the bound paging values are pinned by a
statement-capturing engine here; the database behaviour is pinned by the integration suite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from flask import Flask, jsonify

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import (
    AuditEvent,
    LabOrder,
    LabOrderSummary,
    LabTestType,
    PatientSummary,
)
from medicalbooking.modules.laboratory.application.use_cases import LabService
from medicalbooking.modules.laboratory.infrastructure.repositories import SqlLabStore
from medicalbooking.modules.laboratory.presentation.routes import lab_bp
from medicalbooking.shared.domain.errors import AppError, Forbidden, Unauthenticated

NOW = datetime(2026, 11, 2, 9, 0)
PATIENTS_URL = "/api/v1/patients"
CATALOG_URL = "/api/v1/lab/test-types"
ORDERS_URL = "/api/v1/lab/orders"

MEDICO = frozenset({"ordenes.crear", "resultados.leer_con_acceso", "revisiones.registrar"})
PACIENTE = frozenset({"resultados.leer_propios", "citas.reservar"})
LABORATORISTA = frozenset({"resultados.registrar"})
NO_PERMISSIONS = frozenset()


def _id() -> str:
    return str(uuid.uuid4())


def _actor(permissions: frozenset[str]) -> AccessProfile:
    return AccessProfile(user_id=_id(), status="ACTIVO", roles=frozenset(), permissions=permissions)


@dataclass
class FakeStore:
    patients: list[PatientSummary] = field(default_factory=list)
    test_types: list[LabTestType] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)
    patient_calls: list[dict] = field(default_factory=list)
    orders: list[LabOrderSummary] = field(default_factory=list)
    patient_profile_id: str | None = None
    medico_profile_id: str | None = None
    list_medico_ids: list = field(default_factory=list)

    def list_patients(
        self, *, limit: int, offset: int, medico_id: str | None = None
    ) -> list[PatientSummary]:
        # The relation and account filter run in SQL (see the statement tests below).
        self.patient_calls.append({"limit": limit, "offset": offset})
        self.list_medico_ids.append(medico_id)
        return self.patients[offset : offset + limit]

    def list_test_types(self) -> list[LabTestType]:
        return list(self.test_types)

    def record_audit(self, event: AuditEvent) -> None:
        self.audit.append(event)

    def resolve_patient_id(self, user_id):
        return self.patient_profile_id

    def resolve_medico_id(self, user_id):
        return self.medico_profile_id

    def list_orders(self, *, paciente_id, medico_id, estado, limit, offset):
        return list(self.orders)

    def get_order(self, order_id) -> LabOrder | None:
        return None


def _service(store: FakeStore) -> LabService:
    return LabService(store=store, storage=None, clock=lambda: NOW)


def _patient(nombre: str = "Ana", apellido: str = "Lopez") -> PatientSummary:
    return PatientSummary(id=_id(), nombre=nombre, apellido=apellido)


def _test_type(codigo: str = "HEMO-SINT") -> LabTestType:
    return LabTestType(
        id=_id(),
        codigo=codigo,
        nombre="Hemograma completo",
        descripcion="Prueba sintetica",
        unidad_predeterminada="10^3/uL",
    )


class _Tokens:
    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self._profiles = profiles

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self._profiles:
            raise Unauthenticated("Authentication required")
        return self._profiles[token]


@dataclass
class World:
    store: FakeStore
    doctor: AccessProfile
    patient: AccessProfile
    lab: AccessProfile
    nobody: AccessProfile

    def client(self):
        app = Flask("test-lab-catalog")
        app.register_blueprint(lab_bp)
        app.extensions["medicalbooking.laboratory"] = _service(self.store)
        app.extensions["medicalbooking.identity"] = _Tokens(
            {
                "tok-doctor": self.doctor,
                "tok-patient": self.patient,
                "tok-lab": self.lab,
                "tok-nobody": self.nobody,
            }
        )

        @app.errorhandler(AppError)
        def _domain_error(error: AppError):  # noqa: ANN202
            return jsonify({"status": error.status, "detail": error.detail}), error.status

        return app.test_client()


@pytest.fixture
def world() -> World:
    return World(
        store=FakeStore(medico_profile_id=_id()),
        doctor=_actor(MEDICO),
        patient=_actor(PACIENTE),
        lab=_actor(LABORATORISTA),
        nobody=_actor(NO_PERMISSIONS),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- patient directory: access ----------


def test_patient_list_is_forbidden_without_order_create(world) -> None:
    with pytest.raises(Forbidden):
        _service(world.store).list_patients(world.lab, limit=50, offset=0)


def test_patient_list_is_scoped_to_the_doctor_profile(world) -> None:
    _service(world.store).list_patients(world.doctor, limit=50, offset=0)

    assert world.store.list_medico_ids[-1] == world.store.medico_profile_id


def test_a_doctor_without_a_profile_gets_403_on_the_patient_directory(world) -> None:
    world.store.medico_profile_id = None

    with pytest.raises(Forbidden):
        _service(world.store).list_patients(world.doctor, limit=50, offset=0)
    assert world.store.patient_calls == []


def test_patient_list_answers_403_over_http_for_a_patient(world) -> None:
    response = world.client().get(PATIENTS_URL, headers=_bearer("tok-patient"))

    assert response.status_code == 403


def test_patient_list_is_allowed_for_a_doctor(world) -> None:
    world.store.patients = [_patient()]

    response = world.client().get(PATIENTS_URL, headers=_bearer("tok-doctor"))

    assert response.status_code == 200


# ---------- patient directory: response shape ----------


def test_patient_rows_carry_only_id_and_names(world) -> None:
    world.store.patients = [_patient("Ana", "Lopez")]

    body = world.client().get(PATIENTS_URL, headers=_bearer("tok-doctor")).get_json()

    assert set(body) == {"items", "limit", "offset"}
    assert set(body["items"][0]) == {"id", "nombres", "apellidos"}
    assert body["items"][0]["nombres"] == "Ana"
    assert body["items"][0]["apellidos"] == "Lopez"


def test_patient_rows_never_expose_contact_documents_or_credentials(world) -> None:
    world.store.patients = [_patient()]

    response = world.client().get(PATIENTS_URL, headers=_bearer("tok-doctor"))
    assert response.status_code == 200
    text = str(response.get_json()).lower()

    for forbidden in ("email", "documento", "telefono", "fecha_nacimiento", "password", "hash"):
        assert forbidden not in text


# ---------- patient directory: pagination ----------


def test_patient_page_defaults_to_limit_50_and_offset_0(world) -> None:
    response = world.client().get(PATIENTS_URL, headers=_bearer("tok-doctor"))

    assert world.store.patient_calls[-1] == {"limit": 50, "offset": 0}
    assert response.get_json()["limit"] == 50
    assert response.get_json()["offset"] == 0


@pytest.mark.parametrize("limit", ["0", "101", "abc", "-5"])
def test_patient_limit_outside_1_to_100_is_422(world, limit) -> None:
    response = world.client().get(f"{PATIENTS_URL}?limit={limit}", headers=_bearer("tok-doctor"))

    assert response.status_code == 422


def test_patient_limit_of_100_is_accepted(world) -> None:
    response = world.client().get(f"{PATIENTS_URL}?limit=100", headers=_bearer("tok-doctor"))

    assert response.status_code == 200
    assert world.store.patient_calls[-1]["limit"] == 100


@pytest.mark.parametrize("offset", ["-1", "x", "2147483648"])
def test_patient_offset_must_be_a_non_negative_integer(world, offset) -> None:
    response = world.client().get(f"{PATIENTS_URL}?offset={offset}", headers=_bearer("tok-doctor"))

    assert response.status_code == 422


def test_patient_page_passes_limit_and_offset_to_the_store(world) -> None:
    world.client().get(f"{PATIENTS_URL}?limit=10&offset=20", headers=_bearer("tok-doctor"))

    assert world.store.patient_calls[-1] == {"limit": 10, "offset": 20}


# ---------- patient directory: audit ----------


def test_patient_list_writes_one_audit_row_without_personal_values(world) -> None:
    world.store.patients = [_patient("Ana", "Lopez")]

    world.client().get(PATIENTS_URL, headers=_bearer("tok-doctor"))

    assert len(world.store.audit) == 1
    event = world.store.audit[0]
    assert event.actor_user_id == world.doctor.user_id
    assert event.entidad == "paciente"
    assert event.accion == "listar"
    assert event.resultado == "OK"
    assert event.paciente_id is None
    assert event.entidad_id is None
    assert "Ana" not in repr(event)
    assert "Lopez" not in repr(event)


def test_denied_patient_list_writes_no_success_audit(world) -> None:
    with pytest.raises(Forbidden):
        _service(world.store).list_patients(world.patient, limit=50, offset=0)

    assert world.store.audit == []


# ---------- patient directory: SQL shape (statement captured, values bound) ----------


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def first(self):  # pragma: no cover - not used by the catalog queries
        return self._rows[0] if self._rows else None


class _CapturingConnection:
    def __init__(self, log: list) -> None:
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, statement, params=None):
        self._log.append((" ".join(str(statement).split()), dict(params or {})))
        return _Result([])


class _CapturingEngine:
    def __init__(self) -> None:
        self.log: list = []

    def connect(self) -> _CapturingConnection:
        return _CapturingConnection(self.log)


def test_patient_query_orders_by_apellido_nombre_and_id_with_bound_paging() -> None:
    engine = _CapturingEngine()

    SqlLabStore(engine).list_patients(medico_id=_id(), limit=5, offset=10)

    sql, params = engine.log[-1]
    assert "ORDER BY u.apellido, u.nombre, p.id" in sql
    assert "OFFSET :skip ROWS FETCH NEXT :take ROWS ONLY" in sql
    assert params["skip"] == 10
    assert params["take"] == 5


def test_patient_query_is_bound_to_the_doctor_relation_and_active_accounts() -> None:
    engine = _CapturingEngine()
    doctor = _id()

    SqlLabStore(engine).list_patients(medico_id=doctor, limit=5, offset=0)

    sql, params = engine.log[-1]
    assert "u.estado = 'ACTIVO'" in sql
    assert "EXISTS (SELECT 1 FROM citas c" in sql
    assert "EXISTS (SELECT 1 FROM ordenes_laboratorio o" in sql
    assert params["medico"] == doctor
    assert doctor not in sql


def test_relation_check_binds_the_doctor_and_the_patient_as_parameters() -> None:
    engine = _CapturingEngine()
    doctor, patient = _id(), _id()

    related = SqlLabStore(engine).patient_is_related(doctor, patient)

    sql, params = engine.log[-1]
    assert related is False
    assert "citas" in sql and "ordenes_laboratorio" in sql
    assert params["medico"] == doctor
    assert params["paciente"] == patient
    assert doctor not in sql and patient not in sql


def test_patient_query_joins_only_the_name_columns() -> None:
    engine = _CapturingEngine()

    SqlLabStore(engine).list_patients(medico_id=_id(), limit=5, offset=0)

    sql, _ = engine.log[-1]
    assert "u.email" not in sql
    assert "u.telefono" not in sql
    assert "p.numero_documento" not in sql
    assert "p.fecha_nacimiento" not in sql


# ---------- test-type catalog ----------


def test_catalog_returns_the_active_test_types_with_their_fields(world) -> None:
    world.store.test_types = [_test_type("HEMO-SINT")]

    body = world.client().get(CATALOG_URL, headers=_bearer("tok-doctor")).get_json()

    assert set(body) == {"items"}
    assert set(body["items"][0]) == {
        "id",
        "codigo",
        "nombre",
        "descripcion",
        "unidad_predeterminada",
    }
    assert body["items"][0]["codigo"] == "HEMO-SINT"


def test_catalog_query_filters_active_rows_and_orders_by_nombre_then_id() -> None:
    engine = _CapturingEngine()

    SqlLabStore(engine).list_test_types()

    sql, _ = engine.log[-1]
    assert "WHERE activo = 1" in sql
    assert "ORDER BY nombre, id" in sql


@pytest.mark.parametrize("token", ["tok-doctor", "tok-patient", "tok-lab", "tok-nobody"])
def test_catalog_is_open_to_any_authenticated_actor(world, token) -> None:
    response = world.client().get(CATALOG_URL, headers=_bearer(token))

    assert response.status_code == 200


def test_catalog_requires_a_bearer_token(world) -> None:
    response = world.client().get(CATALOG_URL)

    assert response.status_code == 401


def test_catalog_writes_no_audit_row(world) -> None:
    world.store.test_types = [_test_type()]

    response = world.client().get(CATALOG_URL, headers=_bearer("tok-patient"))

    assert response.status_code == 200
    assert world.store.audit == []


# ---------- PRESERVE: existing lab behaviour that shares the touched code ----------


def test_preserve_order_listing_keeps_its_items_envelope(world) -> None:
    world.store.patient_profile_id = _id()
    world.store.orders = [
        LabOrderSummary(
            id=_id(), paciente_id=world.store.patient_profile_id, estado="SOLICITADA", fecha=NOW
        )
    ]

    response = world.client().get(ORDERS_URL, headers=_bearer("tok-patient"))

    assert response.status_code == 200
    assert set(response.get_json()) == {"items"}


def test_preserve_read_audit_still_records_the_order_as_entity() -> None:
    store = FakeStore()
    order_id = _id()
    patient = _actor(PACIENTE)
    service = _service(store)
    store.get_order = lambda oid: LabOrder(  # type: ignore[method-assign]
        id=order_id,
        paciente_id=_id(),
        paciente_user_id=patient.user_id,
        medico_id=_id(),
        medico_user_id=_id(),
        prioridad="NORMAL",
        estado="SOLICITADA",
        observaciones=None,
        details=(),
    )
    store.list_results = lambda oid: []  # type: ignore[attr-defined]

    service.list_results(patient, order_id)

    assert store.audit[-1].entidad == "resultado_laboratorio"
    assert store.audit[-1].entidad_id == order_id
    assert store.audit[-1].accion == "consultar"
