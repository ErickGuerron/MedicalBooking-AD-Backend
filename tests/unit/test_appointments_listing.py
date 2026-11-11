"""HTTP and service contract of GET /api/v1/appointments (S6): ownership, filter, paging.

Runs the real scheduling service behind the real blueprint, with an in-memory store.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask, jsonify
from flask.testing import FlaskClient

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.presentation.routes import scheduling_bp
from medicalbooking.shared.domain.errors import AppError, Unauthenticated
from tests.unit.test_scheduling_service import FakeStore

PATH = "/api/v1/appointments"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
INICIO = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
FIN = datetime(2026, 11, 12, 10, 30, tzinfo=UTC)
PATIENT_A = "patient-a"
PATIENT_B = "patient-b"
DOCTOR = "doctor-1"
OTHER_DOCTOR = "doctor-2"
LAB = "lab-user"


def _id() -> str:
    return str(uuid.uuid4())


def _profile(user_id: str, roles: set[str], permissions: frozenset[str] = frozenset()):
    return AccessProfile(
        user_id=user_id,
        status="ACTIVO",
        roles=frozenset(roles),
        permissions=permissions,
    )


@dataclass(frozen=True)
class Row:
    id: str
    estado: str
    inicio_utc: datetime
    fin_utc: datetime
    patient_user: str
    doctor_user: str


@dataclass
class ListingStore(FakeStore):
    """Booking fake plus the listing queries. Ownership is kept on each row."""

    rows: list[Row] = field(default_factory=list)
    doctor_users: set[str] = field(default_factory=set)
    list_calls: list[tuple] = field(default_factory=list)

    def has_doctor_profile(self, user_id: str) -> bool:
        return user_id in self.doctor_users

    def list_patient_appointments(self, patient_user_id, estado, limit, offset):
        self.list_calls.append(("patient", patient_user_id, estado, limit, offset))
        return self._page(
            [row for row in self.rows if row.patient_user == patient_user_id], estado, limit, offset
        )

    def list_agenda_appointments(self, doctor_user_id, estado, limit, offset):
        self.list_calls.append(("agenda", doctor_user_id, estado, limit, offset))
        return self._page(
            [row for row in self.rows if row.doctor_user == doctor_user_id], estado, limit, offset
        )

    @staticmethod
    def _page(rows, estado, limit, offset):
        matching = [row for row in rows if estado is None or row.estado == estado]
        matching.sort(key=lambda row: (row.inicio_utc, row.id))
        return matching[offset : offset + limit]


class FakeTokens:
    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self._profiles = profiles

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self._profiles:
            raise Unauthenticated("Authentication required")
        return self._profiles[token]


PROFILES = {
    "tok-a": _profile(PATIENT_A, {"PACIENTE"}, frozenset({"citas.reservar"})),
    "tok-b": _profile(PATIENT_B, {"PACIENTE"}, frozenset({"citas.reservar"})),
    "tok-doctor": _profile(DOCTOR, {"MEDICO"}),
    "tok-doctor-2": _profile(OTHER_DOCTOR, {"MEDICO"}),
    "tok-lab": _profile(LAB, {"LABORATORISTA"}),
    "tok-medico-sin-perfil": _profile("doctor-without-profile", {"MEDICO"}),
}


def _client(store: ListingStore) -> FlaskClient:
    service = SchedulingService(store=store, clock=lambda: NOW, hold_minutes=15)
    app = Flask("test-appointments-listing")
    app.register_blueprint(scheduling_bp)
    app.extensions["medicalbooking.identity"] = FakeTokens(PROFILES)
    app.extensions["medicalbooking.scheduling"] = service

    @app.errorhandler(AppError)
    def _domain_error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status, "title": error.title}), error.status

    return app.test_client()


def _row(patient: str, doctor: str, estado: str = "CONFIRMADA", inicio=INICIO) -> Row:
    return Row(
        id=_id(),
        estado=estado,
        inicio_utc=inicio,
        fin_utc=inicio + timedelta(minutes=30),
        patient_user=patient,
        doctor_user=doctor,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def store() -> ListingStore:
    fake = ListingStore()
    fake.doctor_users = {DOCTOR, OTHER_DOCTOR}
    return fake


@pytest.fixture
def client(store: ListingStore) -> FlaskClient:
    return _client(store)


# ---------- access ----------


def test_listing_requires_a_bearer_token(client: FlaskClient) -> None:
    assert client.get(PATH).status_code == 401


def test_laboratory_user_gets_403(client: FlaskClient, store: ListingStore) -> None:
    response = client.get(PATH, headers=_auth("tok-lab"))

    assert response.status_code == 403
    assert store.list_calls == []


def test_medico_without_a_doctor_profile_gets_403(client: FlaskClient, store: ListingStore) -> None:
    response = client.get(PATH, headers=_auth("tok-medico-sin-perfil"))

    assert response.status_code == 403
    assert store.list_calls == []


# ---------- ownership ----------


def test_patient_sees_only_own_appointments(client: FlaskClient, store: ListingStore) -> None:
    own = _row(PATIENT_A, DOCTOR)
    store.rows = [own, _row(PATIENT_B, DOCTOR)]

    items = client.get(PATH, headers=_auth("tok-a")).get_json()["items"]

    assert [item["id"] for item in items] == [own.id]


def test_doctor_sees_only_appointments_of_his_or_her_agenda(
    client: FlaskClient, store: ListingStore
) -> None:
    mine = _row(PATIENT_A, DOCTOR)
    store.rows = [mine, _row(PATIENT_B, OTHER_DOCTOR)]

    items = client.get(PATH, headers=_auth("tok-doctor")).get_json()["items"]

    assert [item["id"] for item in items] == [mine.id]


def test_response_never_carries_another_patients_data(
    client: FlaskClient, store: ListingStore
) -> None:
    store.rows = [_row(PATIENT_A, DOCTOR), _row(PATIENT_B, DOCTOR)]

    response = client.get(PATH, headers=_auth("tok-doctor"))
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert PATIENT_A not in body
    assert PATIENT_B not in body


# ---------- estado filter ----------


def test_estado_filter_keeps_only_matching_appointments(
    client: FlaskClient, store: ListingStore
) -> None:
    confirmed = _row(PATIENT_A, DOCTOR, estado="CONFIRMADA")
    cancelled = _row(PATIENT_A, DOCTOR, estado="CANCELADA")
    store.rows = [confirmed, cancelled]

    items = client.get(
        PATH, query_string={"estado": "CANCELADA"}, headers=_auth("tok-a")
    ).get_json()["items"]

    assert [item["id"] for item in items] == [cancelled.id]


@pytest.mark.parametrize("estado", ["BORRADOR", "", "confirmada"])
def test_unknown_estado_answers_422(client: FlaskClient, store: ListingStore, estado: str) -> None:
    response = client.get(PATH, query_string={"estado": estado}, headers=_auth("tok-a"))

    assert response.status_code == 422
    assert store.list_calls == []


# ---------- response shape ----------


def test_rows_carry_id_estado_inicio_and_fin_in_utc_iso_8601(
    client: FlaskClient, store: ListingStore
) -> None:
    row = _row(PATIENT_A, DOCTOR)
    store.rows = [row]

    item = client.get(PATH, headers=_auth("tok-a")).get_json()["items"][0]

    assert item == {
        "id": row.id,
        "estado": "CONFIRMADA",
        "inicio": "2026-11-12T10:00:00+00:00",
        "fin": "2026-11-12T10:30:00+00:00",
    }


# ---------- paging ----------


def test_default_page_is_fifty_rows_from_the_start(
    client: FlaskClient, store: ListingStore
) -> None:
    client.get(PATH, headers=_auth("tok-a"))

    assert store.list_calls == [("patient", PATIENT_A, None, 50, 0)]


def test_limit_and_offset_are_passed_to_the_store(client: FlaskClient, store: ListingStore) -> None:
    client.get(PATH, query_string={"limit": "5", "offset": "10"}, headers=_auth("tok-a"))

    assert store.list_calls == [("patient", PATIENT_A, None, 5, 10)]


@pytest.mark.parametrize(
    "query",
    [
        {"limit": "0"},
        {"limit": "201"},
        {"limit": "abc"},
        {"offset": "-1"},
        {"offset": "x"},
    ],
)
def test_invalid_paging_answers_422_without_querying(
    client: FlaskClient, store: ListingStore, query: dict[str, str]
) -> None:
    response = client.get(PATH, query_string=query, headers=_auth("tok-a"))

    assert response.status_code == 422
    assert store.list_calls == []


# ---------- preserved behavior of existing routes ----------


def test_booking_route_still_creates_a_pending_appointment_with_201(store: ListingStore) -> None:
    client = _client(store)
    horario, servicio = _id(), _id()
    store.slots[horario] = FIN + timedelta(days=2)
    store.services.add(servicio)
    store.patients.add(PATIENT_A)

    response = client.post(
        PATH,
        json={"horario_id": horario, "servicio_id": servicio},
        headers={**_auth("tok-a"), "Idempotency-Key": "preserved-key-0001"},
    )

    assert response.status_code == 201
    assert response.get_json()["estado"] == "PENDIENTE_PAGO"


def test_cancel_route_still_lets_only_the_owner_cancel(store: ListingStore) -> None:
    client = _client(store)
    horario, servicio = _id(), _id()
    store.slots[horario] = FIN + timedelta(days=2)
    store.services.add(servicio)
    store.patients.add(PATIENT_A)
    store.patients.add(PATIENT_B)
    booked = client.post(
        PATH,
        json={"horario_id": horario, "servicio_id": servicio},
        headers={**_auth("tok-a"), "Idempotency-Key": "preserved-key-0002"},
    ).get_json()

    intruder = client.post(f"{PATH}/{booked['id']}/cancel", headers=_auth("tok-b"))
    owner = client.post(f"{PATH}/{booked['id']}/cancel", headers=_auth("tok-a"))

    assert intruder.status_code == 403
    assert owner.status_code == 200
    assert owner.get_json()["estado"] == "CANCELADA"
