"""HTTP and service contract of the appointment listings (S6 and cancellation review).

GET /api/v1/appointments serves patients and doctors.
GET /api/v1/appointments/cancellation-approvals serves the administrator's cancellation review.
Runs the real scheduling service behind the real blueprint, with an in-memory store.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask, jsonify
from flask.testing import FlaskClient

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.presentation.routes import scheduling_bp
from medicalbooking.shared.domain.errors import AppError, Unauthenticated
from tests.unit.test_scheduling_service import FakeStore

PATH = "/api/v1/appointments"
CANDIDATES = "/api/v1/appointments/cancellation-approvals"
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
    paciente_id: str | None = None
    paciente_nombre: str | None = None
    medico_id: str | None = None
    medico_nombre: str | None = None
    medico_registro: str | None = None
    medico_telefono: str | None = None
    tiene_aprobacion: bool = False
    pago_id: str | None = None
    reembolso_estado: str | None = None


def _doctor_fields(row: Row) -> dict:
    """What the store returns for the doctor of a row, as the SQL adapter maps it."""
    return {
        "medico_id": f"mid-{row.doctor_user}",
        "medico_nombre": f"Dr. {row.doctor_user}",
        "medico_registro": f"REG-{row.doctor_user}",
        "medico_telefono": f"TEL-{row.doctor_user}",
    }


@dataclass
class ListingStore(FakeStore):
    """Booking fake plus the listing queries. Ownership is kept on each row."""

    rows: list[Row] = field(default_factory=list)
    doctor_users: set[str] = field(default_factory=set)
    list_calls: list[tuple] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    def has_doctor_profile(self, user_id: str) -> bool:
        return user_id in self.doctor_users

    def list_patient_appointments(self, patient_user_id, estado, limit, offset):
        self.list_calls.append(("patient", patient_user_id, estado, limit, offset))
        rows = [
            replace(row, **_doctor_fields(row))
            for row in self.rows
            if row.patient_user == patient_user_id
        ]
        return self._page(rows, estado, limit, offset)

    def list_agenda_appointments(self, doctor_user_id, estado, limit, offset):
        self.list_calls.append(("agenda", doctor_user_id, estado, limit, offset))
        # The adapter adds the patient on the doctor's agenda rows only.
        rows = [
            replace(
                row,
                paciente_id=f"pid-{row.patient_user}",
                paciente_nombre=f"Name {row.patient_user}",
            )
            for row in self.rows
            if row.doctor_user == doctor_user_id
        ]
        return self._page(rows, estado, limit, offset)

    def list_cancellation_candidates(self, estado, limit, offset, medico_registro=None):
        self.list_calls.append(("candidates", estado, limit, offset, medico_registro))
        # Doctor identity only: no patient, no doctor phone.
        rows = [
            replace(row, **_doctor_fields(row))
            for row in self.rows
            if medico_registro is None or medico_registro == f"REG-{row.doctor_user}"
        ]
        return self._page(rows, estado, limit, offset)

    def record_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

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
    "tok-admin": _profile(
        "admin-user", {"ADMINISTRADOR"}, frozenset({"citas.revisar_cancelaciones"})
    ),
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


# ---------- GET /appointments: access and ownership ----------


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


def test_admin_is_refused_on_the_general_listing(client: FlaskClient, store: ListingStore) -> None:
    store.rows = [_row(PATIENT_A, DOCTOR)]

    response = client.get(PATH, headers=_auth("tok-admin"))

    assert response.status_code == 403
    assert store.list_calls == []


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


def test_the_doctors_agenda_names_the_patient_of_each_own_row(
    client: FlaskClient, store: ListingStore
) -> None:
    store.rows = [_row(PATIENT_A, DOCTOR), _row(PATIENT_B, OTHER_DOCTOR)]

    response = client.get(PATH, headers=_auth("tok-doctor"))

    assert response.status_code == 200
    assert [item["paciente"] for item in response.get_json()["items"]] == [
        {"id": f"pid-{PATIENT_A}", "nombre": f"Name {PATIENT_A}"}
    ]
    assert PATIENT_B not in response.get_data(as_text=True)


# ---------- GET /appointments: patient gets the doctor's contact data (S1) ----------


def test_a_patients_row_names_the_doctor_with_contact_data(
    client: FlaskClient, store: ListingStore
) -> None:
    row = _row(PATIENT_A, DOCTOR)
    store.rows = [row]

    item = client.get(PATH, headers=_auth("tok-a")).get_json()["items"][0]

    assert item["medico"] == {
        "id": f"mid-{DOCTOR}",
        "nombre": f"Dr. {DOCTOR}",
        "registro_profesional": f"REG-{DOCTOR}",
        "telefono": f"TEL-{DOCTOR}",
    }


def test_a_patients_row_never_carries_patient_data(
    client: FlaskClient, store: ListingStore
) -> None:
    store.rows = [_row(PATIENT_A, DOCTOR), _row(PATIENT_B, DOCTOR)]

    body = client.get(PATH, headers=_auth("tok-a")).get_json()

    assert all("paciente" not in item for item in body["items"])
    assert PATIENT_B not in str(body)


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

    assert {
        "id": item["id"],
        "estado": item["estado"],
        "inicio": item["inicio"],
        "fin": item["fin"],
    } == {
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
        {"offset": "2147483648"},
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


# ---------- GET /appointments/cancellation-approvals (S2-S5) ----------


def test_candidates_listing_requires_a_bearer_token(client: FlaskClient) -> None:
    assert client.get(CANDIDATES).status_code == 401


@pytest.mark.parametrize("token", ["tok-a", "tok-doctor", "tok-lab", "tok-medico-sin-perfil"])
def test_only_cancellation_reviewers_can_list_candidates(
    client: FlaskClient, store: ListingStore, token: str
) -> None:
    store.rows = [_row(PATIENT_A, DOCTOR)]

    response = client.get(CANDIDATES, headers=_auth(token))

    assert response.status_code == 403
    assert all(call[0] != "candidates" for call in store.list_calls)


def test_admin_lists_every_appointment_with_only_the_doctor_identity(
    client: FlaskClient, store: ListingStore
) -> None:
    row_1 = _row(PATIENT_A, DOCTOR)
    row_2 = _row(PATIENT_B, OTHER_DOCTOR)
    store.rows = [row_1, row_2]

    response = client.get(CANDIDATES, headers=_auth("tok-admin"))

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert {item["id"] for item in items} == {row_1.id, row_2.id}
    assert all("paciente" not in item for item in items)
    assert all("telefono" not in item["medico"] for item in items)
    body = response.get_data(as_text=True)
    assert PATIENT_A not in body and PATIENT_B not in body
    assert f"TEL-{DOCTOR}" not in body


def test_candidate_rows_have_the_documented_shape(client: FlaskClient, store: ListingStore) -> None:
    row = _row(PATIENT_A, DOCTOR)
    store.rows = [row]

    item = client.get(CANDIDATES, headers=_auth("tok-admin")).get_json()["items"][0]

    assert item == {
        "id": row.id,
        "estado": "CONFIRMADA",
        "inicio": "2026-11-12T10:00:00+00:00",
        "fin": "2026-11-12T10:30:00+00:00",
        "tiene_aprobacion": False,
        "medico": {
            "id": f"mid-{DOCTOR}",
            "nombre": f"Dr. {DOCTOR}",
            "registro_profesional": f"REG-{DOCTOR}",
        },
    }


def test_candidates_filter_by_exact_doctor_registration(
    client: FlaskClient, store: ListingStore
) -> None:
    row_1 = _row(PATIENT_A, DOCTOR)
    store.rows = [row_1, _row(PATIENT_B, OTHER_DOCTOR)]

    items = client.get(
        CANDIDATES, query_string={"medico_registro": f"REG-{DOCTOR}"}, headers=_auth("tok-admin")
    ).get_json()["items"]

    assert [item["id"] for item in items] == [row_1.id]


def test_a_partial_registration_matches_nothing(client: FlaskClient, store: ListingStore) -> None:
    store.rows = [_row(PATIENT_A, DOCTOR)]

    items = client.get(
        CANDIDATES, query_string={"medico_registro": "REG"}, headers=_auth("tok-admin")
    ).get_json()["items"]

    assert items == []


def test_blank_registration_means_no_filter(client: FlaskClient, store: ListingStore) -> None:
    store.rows = [_row(PATIENT_A, DOCTOR), _row(PATIENT_B, OTHER_DOCTOR)]

    items = client.get(
        CANDIDATES, query_string={"medico_registro": ""}, headers=_auth("tok-admin")
    ).get_json()["items"]

    assert len(items) == 2


@pytest.mark.parametrize("estado", ["BORRADOR", "", "confirmada"])
def test_candidates_unknown_estado_answers_422(
    client: FlaskClient, store: ListingStore, estado: str
) -> None:
    response = client.get(CANDIDATES, query_string={"estado": estado}, headers=_auth("tok-admin"))

    assert response.status_code == 422
    assert all(call[0] != "candidates" for call in store.list_calls)


def test_candidates_default_page_and_paging_are_passed_through(
    client: FlaskClient, store: ListingStore
) -> None:
    client.get(CANDIDATES, query_string={"limit": "5", "offset": "10"}, headers=_auth("tok-admin"))

    assert store.list_calls == [("candidates", None, 5, 10, None)]


@pytest.mark.parametrize("query", [{"limit": "0"}, {"offset": "-1"}, {"limit": "201"}])
def test_candidates_invalid_paging_answers_422(
    client: FlaskClient, store: ListingStore, query: dict[str, str]
) -> None:
    response = client.get(CANDIDATES, query_string=query, headers=_auth("tok-admin"))

    assert response.status_code == 422
    assert all(call[0] != "candidates" for call in store.list_calls)


def test_each_candidates_read_is_audited_with_outcome_ok(
    client: FlaskClient, store: ListingStore
) -> None:
    client.get(CANDIDATES, headers=_auth("tok-admin"))

    assert len(store.audits) == 1
    event = store.audits[0]
    assert event.actor_user_id == "admin-user"
    assert event.paciente_id is None
    assert event.entidad == "cita"
    assert event.entidad_id is None
    assert event.accion == "listar_revision_cancelacion"
    assert event.resultado == "OK"


def test_a_denied_candidates_read_is_audited_as_denied(
    client: FlaskClient, store: ListingStore
) -> None:
    response = client.get(CANDIDATES, headers=_auth("tok-a"))

    assert response.status_code == 403
    assert len(store.audits) == 1
    assert store.audits[0].actor_user_id == PATIENT_A
    assert store.audits[0].accion == "listar_revision_cancelacion"
    assert store.audits[0].resultado == "DENEGADO"
