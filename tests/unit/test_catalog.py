"""Catalog: attention schedule derivation, authorization, status mapping and the HTTP contract.

Runs the real service behind the real blueprint, with an in-memory store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import pytest
from flask import Flask, jsonify
from flask.testing import FlaskClient

from medicalbooking.modules.catalog.application.ports import (
    Doctor,
    Service,
    Specialty,
    SpecialtyRef,
    WriteStatus,
)
from medicalbooking.modules.catalog.application.use_cases import CatalogService
from medicalbooking.modules.catalog.domain.policies import (
    AttentionSchedule,
    WorkingDay,
    derive_attention_schedule,
)
from medicalbooking.modules.catalog.presentation.routes import catalog_bp
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.shared.domain.errors import AppError, Unauthenticated

DOCTOR_ID = "11111111-1111-4111-8111-111111111111"
SERVICE_ID = "22222222-2222-4222-8222-222222222222"


def _slot(day: int, start: str, end: str) -> tuple[datetime, datetime]:
    """A local slot on 2026-11-<day> (day 2 is a Monday)."""
    return (
        datetime.fromisoformat(f"2026-11-{day:02d}T{start}:00"),
        datetime.fromisoformat(f"2026-11-{day:02d}T{end}:00"),
    )


# ---------- schedule derivation ----------


def test_schedule_without_active_agendas_is_empty() -> None:
    assert derive_attention_schedule([], [_slot(2, "08:00", "08:30")]) == AttentionSchedule(
        None, None, ()
    )


def test_schedule_spans_earliest_start_to_latest_end_per_weekday() -> None:
    slots = [
        _slot(2, "09:00", "09:30"),
        _slot(2, "08:00", "08:30"),
        _slot(2, "11:30", "12:00"),
        _slot(4, "14:00", "14:30"),
        _slot(9, "08:30", "09:00"),  # the next Monday widens nothing
    ]

    schedule = derive_attention_schedule([(date(2026, 1, 1), date(2026, 12, 31))], slots)

    assert schedule.dias == (WorkingDay(1, "08:00", "12:00"), WorkingDay(3, "14:00", "14:30"))
    assert (schedule.vigente_desde, schedule.vigente_hasta) == (
        date(2026, 1, 1),
        date(2026, 12, 31),
    )


def test_schedule_is_open_ended_when_any_agenda_has_no_end() -> None:
    agendas = [(date(2026, 3, 1), date(2026, 6, 30)), (date(2026, 1, 1), None)]

    schedule = derive_attention_schedule(agendas, [])

    assert (schedule.vigente_desde, schedule.vigente_hasta, schedule.dias) == (
        date(2026, 1, 1),
        None,
        (),
    )


def test_a_slot_crossing_midnight_is_clipped_to_its_day() -> None:
    crossing = (datetime(2026, 11, 2, 23, 30), datetime(2026, 11, 3, 0, 30))

    schedule = derive_attention_schedule([(date(2026, 1, 1), None)], [crossing])

    assert schedule.dias == (WorkingDay(1, "23:30", "23:59"),)


# ---------- service ----------


@dataclass
class FakeStore:
    status: WriteStatus = WriteStatus.OK
    audit: list[tuple] = field(default_factory=list)
    doctor_filter: list = field(default_factory=list)

    def list_specialties(self):
        return [Specialty(1, "Cardiologia", None)]

    def list_doctors(self, especialidad_id):
        self.doctor_filter.append(especialidad_id)
        return [
            Doctor(
                id=DOCTOR_ID,
                nombre="Ana Perez",
                especialidades=(SpecialtyRef(1, "Cardiologia"),),
                duracion_cita_min=30,
                horario_atencion=AttentionSchedule(
                    date(2026, 1, 1), None, (WorkingDay(1, "08:00", "12:00"),)
                ),
            )
        ]

    def list_services(self):
        return [Service(SERVICE_ID, "Consulta", 30, Decimal("25.00"), "USD", 1)]

    def create_specialty(self, nombre, descripcion):
        return self.status, Specialty(
            7, nombre, descripcion
        ) if self.status is WriteStatus.OK else None

    def update_specialty(self, especialidad_id, changes):
        return self.status, Specialty(
            especialidad_id, "Nueva", None
        ) if self.status is WriteStatus.OK else None

    def set_doctor_specialties(self, medico_id, especialidad_ids):
        return self.status, [SpecialtyRef(i, f"E{i}") for i in especialidad_ids]

    def create_service(self, nombre, duracion_min, precio_base, moneda, especialidad_id):
        service = Service(SERVICE_ID, nombre, duracion_min, precio_base, moneda, especialidad_id)
        return self.status, service if self.status is WriteStatus.OK else None

    def set_service_specialty(self, servicio_id, especialidad_id):
        service = Service(servicio_id, "Consulta", 30, Decimal("25.00"), "USD", especialidad_id)
        return self.status, service if self.status is WriteStatus.OK else None

    def record_audit(self, actor_user_id, entidad, entidad_id, accion, detalle):
        self.audit.append((actor_user_id, entidad, entidad_id, accion, detalle))


def _profile(user_id: str, *permissions: str) -> AccessProfile:
    return AccessProfile(
        user_id=user_id, status="ACTIVO", roles=frozenset(), permissions=frozenset(permissions)
    )


class FakeTokens:
    def __init__(self) -> None:
        self.profiles = {
            "tok-admin": _profile("admin", "catalogo.administrar"),
            "tok-patient": _profile("patient", "citas.reservar"),
            "tok-doctor": _profile("doctor", "ordenes.crear"),
        }

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self.profiles:
            raise Unauthenticated("Authentication required")
        return self.profiles[token]


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def client(store: FakeStore) -> FlaskClient:
    app = Flask("test-catalog")
    app.register_blueprint(catalog_bp)
    app.extensions["medicalbooking.identity"] = FakeTokens()
    app.extensions["medicalbooking.catalog"] = CatalogService(store)

    @app.errorhandler(AppError)
    def _domain_error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status, "detail": error.detail}), error.status

    return app.test_client()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", ["/api/v1/specialties", "/api/v1/doctors", "/api/v1/services"])
def test_listings_require_authentication_but_no_permission(client, path) -> None:
    assert client.get(path).status_code == 401
    for token in ("tok-patient", "tok-doctor", "tok-admin"):
        assert client.get(path, headers=_auth(token)).status_code == 200


def test_doctor_listing_shape_never_exposes_registry_or_email(client) -> None:
    item = client.get("/api/v1/doctors", headers=_auth("tok-patient")).get_json()["items"][0]

    assert item == {
        "id": DOCTOR_ID,
        "nombre": "Ana Perez",
        "especialidades": [{"id": 1, "nombre": "Cardiologia"}],
        "duracion_cita_min": 30,
        "horario_atencion": {
            "vigente_desde": "2026-01-01",
            "vigente_hasta": None,
            "dias": [{"dia_semana": 1, "desde": "08:00", "hasta": "12:00"}],
        },
    }


def test_doctor_listing_passes_the_specialty_filter(client, store) -> None:
    client.get("/api/v1/doctors?especialidad_id=3", headers=_auth("tok-patient"))
    client.get("/api/v1/doctors", headers=_auth("tok-patient"))

    assert store.doctor_filter == [3, None]


@pytest.mark.parametrize("value", ["abc", "0", "-1", "99999999999", "1.5"])
def test_doctor_listing_rejects_an_invalid_specialty_filter(client, value) -> None:
    response = client.get(f"/api/v1/doctors?especialidad_id={value}", headers=_auth("tok-patient"))

    assert response.status_code == 422


def test_service_listing_shape(client) -> None:
    item = client.get("/api/v1/services", headers=_auth("tok-patient")).get_json()["items"][0]

    assert item == {
        "id": SERVICE_ID,
        "nombre": "Consulta",
        "duracion_min": 30,
        "precio_base": "25.00",
        "moneda": "USD",
        "especialidad_id": 1,
    }


WRITES = [
    ("post", "/api/v1/specialties", {"nombre": "Cardiologia"}),
    ("patch", "/api/v1/specialties/1", {"nombre": "Otra"}),
    ("put", f"/api/v1/doctors/{DOCTOR_ID}/specialties", {"especialidad_ids": [1]}),
    ("post", "/api/v1/services", {"nombre": "Consulta", "duracion_min": 30, "precio_base": "10"}),
    ("patch", f"/api/v1/services/{SERVICE_ID}", {"especialidad_id": 1}),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_writes_are_admin_only_and_audited(client, store, method, path, body) -> None:
    caller = getattr(client, method)

    assert caller(path, json=body).status_code == 401
    for token in ("tok-patient", "tok-doctor"):
        assert caller(path, json=body, headers=_auth(token)).status_code == 403
    assert store.audit == []

    assert caller(path, json=body, headers=_auth("tok-admin")).status_code in (200, 201)
    assert [entry[0] for entry in store.audit] == ["admin"]


def test_create_specialty_returns_201_and_trims_the_name(client) -> None:
    response = client.post(
        "/api/v1/specialties",
        json={"nombre": "  Pediatria ", "descripcion": "Ninos"},
        headers=_auth("tok-admin"),
    )

    assert response.status_code == 201
    assert response.get_json() == {"id": 7, "nombre": "Pediatria", "descripcion": "Ninos"}


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (WriteStatus.NOT_FOUND, 404),
        (WriteStatus.UNKNOWN_SPECIALTY, 422),
        (WriteStatus.DUPLICATE_NAME, 409),
    ],
)
def test_store_statuses_map_to_http_errors(client, store, status, code) -> None:
    store.status = status

    response = client.patch(
        "/api/v1/specialties/1", json={"nombre": "X"}, headers=_auth("tok-admin")
    )

    assert response.status_code == code
    assert store.audit == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/specialties", {"nombre": "   "}),
        ("post", "/api/v1/specialties", {"nombre": "X", "extra": 1}),
        ("patch", "/api/v1/specialties/1", {}),
        ("patch", "/api/v1/specialties/1", {"nombre": None}),
        ("patch", "/api/v1/specialties/1", {"activo": None}),
        ("put", f"/api/v1/doctors/{DOCTOR_ID}/specialties", {"especialidad_ids": [0]}),
        ("put", f"/api/v1/doctors/{DOCTOR_ID}/specialties", {"especialidad_ids": "1"}),
        ("post", "/api/v1/services", {"nombre": "S", "duracion_min": 0, "precio_base": "1"}),
        ("post", "/api/v1/services", {"nombre": "S", "duracion_min": 5, "precio_base": "-1"}),
        ("post", "/api/v1/services", {"nombre": "S", "duracion_min": 5, "precio_base": "1.234"}),
        (
            "post",
            "/api/v1/services",
            {"nombre": "S", "duracion_min": 5, "precio_base": "1", "moneda": "usd"},
        ),
        ("patch", f"/api/v1/services/{SERVICE_ID}", {}),
    ],
)
def test_invalid_admin_bodies_are_422(client, store, method, path, body) -> None:
    response = getattr(client, method)(path, json=body, headers=_auth("tok-admin"))

    assert response.status_code == 422
    assert store.audit == []


def test_doctor_specialties_are_deduplicated_and_sorted(client) -> None:
    response = client.put(
        f"/api/v1/doctors/{DOCTOR_ID}/specialties",
        json={"especialidad_ids": [3, 1, 3]},
        headers=_auth("tok-admin"),
    )

    assert [item["id"] for item in response.get_json()["especialidades"]] == [1, 3]


def test_service_specialty_can_be_cleared_with_null(client) -> None:
    response = client.patch(
        f"/api/v1/services/{SERVICE_ID}", json={"especialidad_id": None}, headers=_auth("tok-admin")
    )

    assert response.status_code == 200
    assert response.get_json()["especialidad_id"] is None
