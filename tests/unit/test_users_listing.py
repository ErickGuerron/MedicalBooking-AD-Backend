"""HTTP contract of GET /api/v1/users (S2): permission, paging, response shape.

Runs the real identity service behind the real blueprint, with an in-memory directory.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from flask import Flask, jsonify
from flask.testing import FlaskClient

from medicalbooking.modules.identity.application.ports import AccessProfile, NewUser
from medicalbooking.modules.identity.application.use_cases import IdentityService
from medicalbooking.modules.identity.presentation.routes import identity_bp
from medicalbooking.shared.domain.errors import AppError, Unauthenticated

PATH = "/api/v1/users"
ADMIN_HEADERS = {"Authorization": "Bearer tok-admin"}
PATIENT_HEADERS = {"Authorization": "Bearer tok-patient"}
VALID_CREATE_BODY = {
    "email": "nueva.persona@synthetic.test",
    "password": "synthetic-pass-123",
    "first_name": "Nueva",
    "last_name": "Persona",
    "roles": ["PACIENTE"],
}


def _id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class StoredUser:
    user_id: str
    email: str
    first_name: str
    last_name: str
    status: str
    roles: tuple[str, ...]


@dataclass
class FakeDirectory:
    profiles: dict[str, AccessProfile] = field(default_factory=dict)
    users: list[StoredUser] = field(default_factory=list)
    created: list[NewUser] = field(default_factory=list)
    list_calls: list[tuple[int, int]] = field(default_factory=list)

    def load_access(self, user_id: str) -> AccessProfile | None:
        return self.profiles.get(user_id)

    def create_user(self, new_user: NewUser) -> None:
        self.created.append(new_user)

    def list_users(self, limit: int, offset: int) -> list[StoredUser]:
        self.list_calls.append((limit, offset))
        return self.users[offset : offset + limit]


class FakeTokens:
    def __init__(self, subjects: dict[str, str]) -> None:
        self._subjects = subjects

    def issue(self, user_id: str) -> tuple[str, int]:
        return "unused", 900

    def subject_of(self, token: str) -> str:
        if token not in self._subjects:
            raise Unauthenticated("Authentication required")
        return self._subjects[token]


class FakeHasher:
    def hash(self, password: str) -> str:
        return "hash:" + password

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == "hash:" + password


def _client(directory: FakeDirectory) -> FlaskClient:
    service = IdentityService(
        directory=directory,
        hasher=FakeHasher(),
        tokens=FakeTokens({"tok-admin": "admin-user", "tok-patient": "patient-user"}),
    )
    app = Flask("test-users-listing")
    app.register_blueprint(identity_bp)
    app.extensions["medicalbooking.identity"] = service

    @app.errorhandler(AppError)
    def _domain_error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status, "title": error.title}), error.status

    return app.test_client()


@pytest.fixture
def directory() -> FakeDirectory:
    return FakeDirectory(
        profiles={
            "admin-user": AccessProfile(
                user_id="admin-user",
                status="ACTIVO",
                roles=frozenset({"ADMINISTRADOR"}),
                permissions=frozenset({"usuarios.administrar"}),
            ),
            "patient-user": AccessProfile(
                user_id="patient-user",
                status="ACTIVO",
                roles=frozenset({"PACIENTE"}),
                permissions=frozenset({"citas.reservar"}),
            ),
        }
    )


@pytest.fixture
def client(directory: FakeDirectory) -> FlaskClient:
    return _client(directory)


# ---------- access ----------


def test_listing_requires_a_bearer_token(client: FlaskClient) -> None:
    assert client.get(PATH).status_code == 401


def test_listing_is_forbidden_without_usuarios_administrar(client: FlaskClient) -> None:
    assert client.get(PATH, headers=PATIENT_HEADERS).status_code == 403


def test_permission_is_checked_before_paging_is_validated(
    client: FlaskClient, directory: FakeDirectory
) -> None:
    response = client.get(PATH, query_string={"limit": "0"}, headers=PATIENT_HEADERS)

    assert response.status_code == 403
    assert directory.list_calls == []


# ---------- response shape ----------


def test_administrator_gets_exactly_the_public_fields(
    client: FlaskClient, directory: FakeDirectory
) -> None:
    user_id = _id()
    directory.users = [
        StoredUser(
            user_id=user_id,
            email="ana@synthetic.test",
            first_name="Ana",
            last_name="Lopez",
            status="ACTIVO",
            roles=("PACIENTE",),
        )
    ]

    response = client.get(PATH, headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.get_json() == {
        "items": [
            {
                "id": user_id,
                "email": "ana@synthetic.test",
                "nombres": "Ana",
                "apellidos": "Lopez",
                "activo": True,
                "roles": ["PACIENTE"],
            }
        ]
    }


@pytest.mark.parametrize(
    ("status", "activo"),
    [("ACTIVO", True), ("INACTIVO", False), ("BLOQUEADO", False)],
)
def test_activo_is_true_only_for_active_accounts(
    client: FlaskClient, directory: FakeDirectory, status: str, activo: bool
) -> None:
    directory.users = [
        StoredUser(_id(), "x@synthetic.test", "X", "Y", status, ("PACIENTE",)),
    ]

    item = client.get(PATH, headers=ADMIN_HEADERS).get_json()["items"][0]

    assert item["activo"] is activo


def test_no_password_material_appears_in_the_listing(
    client: FlaskClient, directory: FakeDirectory
) -> None:
    directory.users = [StoredUser(_id(), "x@synthetic.test", "X", "Y", "ACTIVO", ("PACIENTE",))]

    response = client.get(PATH, headers=ADMIN_HEADERS)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "password" not in body
    assert "hash" not in body


# ---------- paging ----------


def test_default_page_is_fifty_rows_from_the_start(
    client: FlaskClient, directory: FakeDirectory
) -> None:
    client.get(PATH, headers=ADMIN_HEADERS)

    assert directory.list_calls == [(50, 0)]


def test_limit_and_offset_are_passed_to_the_directory(
    client: FlaskClient, directory: FakeDirectory
) -> None:
    client.get(PATH, query_string={"limit": "10", "offset": "20"}, headers=ADMIN_HEADERS)

    assert directory.list_calls == [(10, 20)]


def test_limit_of_exactly_two_hundred_is_accepted(
    client: FlaskClient, directory: FakeDirectory
) -> None:
    response = client.get(PATH, query_string={"limit": "200"}, headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert directory.list_calls == [(200, 0)]


@pytest.mark.parametrize(
    "query",
    [
        {"limit": "0"},
        {"limit": "201"},
        {"limit": "-1"},
        {"limit": "abc"},
        {"limit": ""},
        {"limit": "1.5"},
        {"offset": "-1"},
        {"offset": "x"},
    ],
)
def test_invalid_paging_answers_422_without_querying(
    client: FlaskClient, directory: FakeDirectory, query: dict[str, str]
) -> None:
    response = client.get(PATH, query_string=query, headers=ADMIN_HEADERS)

    assert response.status_code == 422
    assert directory.list_calls == []


# ---------- preserved behavior ----------


def test_post_users_still_creates_an_account_with_201(
    client: FlaskClient, directory: FakeDirectory
) -> None:
    response = client.post("/api/v1/users", json=VALID_CREATE_BODY, headers=ADMIN_HEADERS)

    assert response.status_code == 201
    assert uuid.UUID(response.get_json()["id"])
    assert [created.roles for created in directory.created] == [("PACIENTE",)]
