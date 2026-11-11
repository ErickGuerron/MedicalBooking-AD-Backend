"""User listing checks against real SQL Server: permission, ordering, paging, no credentials.

Ordering is compared with the database's own collation (ORDER BY email in SQL) so the test
does not depend on how Python sorts strings. Each test creates its own accounts with unique
emails; expected positions are computed in SQL from the rows that exist at test time.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.application.ports import NewUser
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.modules.identity.infrastructure.security import Argon2PasswordHasher

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PASSWORD = "synthetic-pass-123"
PATH = "/api/v1/users"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _user(engine: Engine, roles: tuple[str, ...], email: str | None = None) -> str:
    user_id = str(uuid.uuid4())
    SqlUserDirectory(engine).create_user(
        NewUser(
            user_id=user_id,
            email=email or f"listing-{user_id}@synthetic.test",
            first_name="Sintetico",
            last_name="Listado",
            password_hash=Argon2PasswordHasher().hash(PASSWORD),
            roles=roles,
        )
    )
    return user_id


def _email(engine: Engine, user_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT email FROM usuarios WHERE id = :u"), {"u": user_id}
        ).scalar_one()


def _headers(app: Flask, user_id: str) -> dict[str, str]:
    response = app.test_client().post(
        "/api/v1/auth/login",
        json={
            "email": _email(app.extensions["medicalbooking.engine"], user_id),
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200, response.get_json()
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def _offset_of(engine: Engine, email: str) -> int:
    """Position of an email in ORDER BY email, using the same collation as the listing."""
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM usuarios WHERE email < :e"), {"e": email}
        ).scalar_one()


def _ids_in_sql_order(engine: Engine, limit: int) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id FROM usuarios ORDER BY email OFFSET 0 ROWS FETCH NEXT :n ROWS ONLY"),
            {"n": limit},
        ).all()
    return [str(uuid.UUID(str(row.id))) for row in rows]


def test_administrator_lists_users_with_public_fields_and_roles(app, engine) -> None:
    admin = _user(engine, ("ADMINISTRADOR",))
    target = _user(engine, ("LABORATORISTA",))
    headers = _headers(app, admin)

    response = app.test_client().get(
        PATH,
        query_string={"limit": 1, "offset": _offset_of(engine, _email(engine, target))},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"items"}
    item = body["items"][0]
    assert set(item) == {"id", "email", "nombres", "apellidos", "activo", "roles"}
    assert item["id"] == target
    assert item["nombres"] == "Sintetico"
    assert item["apellidos"] == "Listado"
    assert item["activo"] is True
    assert item["roles"] == ["LABORATORISTA"]


def test_inactive_account_is_listed_with_activo_false(app, engine) -> None:
    admin = _user(engine, ("ADMINISTRADOR",))
    inactive = _user(engine, ("PACIENTE",))
    with engine.begin() as conn:
        conn.execute(text("UPDATE usuarios SET estado = 'INACTIVO' WHERE id = :u"), {"u": inactive})

    item = (
        app.test_client()
        .get(
            PATH,
            query_string={"limit": 1, "offset": _offset_of(engine, _email(engine, inactive))},
            headers=_headers(app, admin),
        )
        .get_json()["items"][0]
    )

    assert item["id"] == inactive
    assert item["activo"] is False


def test_listing_is_ordered_by_email_as_the_database_orders_it(app, engine) -> None:
    admin = _user(engine, ("ADMINISTRADOR",))
    headers = _headers(app, admin)

    response = app.test_client().get(PATH, query_string={"limit": 5}, headers=headers)

    returned = [item["id"] for item in response.get_json()["items"]]
    assert returned == _ids_in_sql_order(engine, 5)


def test_pages_are_consecutive_slices_of_the_same_order(app, engine) -> None:
    admin = _user(engine, ("ADMINISTRADOR",))
    headers = _headers(app, admin)
    client = app.test_client()

    first = client.get(PATH, query_string={"limit": 2, "offset": 0}, headers=headers)
    second = client.get(PATH, query_string={"limit": 2, "offset": 2}, headers=headers)
    combined = client.get(PATH, query_string={"limit": 4, "offset": 0}, headers=headers)

    ids = [item["id"] for item in first.get_json()["items"] + second.get_json()["items"]]
    assert ids == [item["id"] for item in combined.get_json()["items"]]


def test_default_page_returns_at_most_fifty_users(app, engine) -> None:
    admin = _user(engine, ("ADMINISTRADOR",))

    response = app.test_client().get(PATH, headers=_headers(app, admin))

    assert response.status_code == 200
    assert len(response.get_json()["items"]) <= 50


def test_response_never_contains_credentials(app, engine) -> None:
    admin = _user(engine, ("ADMINISTRADOR",))

    body = (
        app.test_client()
        .get(PATH, query_string={"limit": 10}, headers=_headers(app, admin))
        .get_data(as_text=True)
    )

    assert "$argon2id$" not in body
    assert "password" not in body


def test_patient_cannot_list_users(app, engine) -> None:
    patient = _user(engine, ("PACIENTE",))

    response = app.test_client().get(PATH, headers=_headers(app, patient))

    assert response.status_code == 403


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"limit": "abc"}])
def test_invalid_paging_answers_422(app, engine, query) -> None:
    admin = _user(engine, ("ADMINISTRADOR",))

    response = app.test_client().get(PATH, query_string=query, headers=_headers(app, admin))

    assert response.status_code == 422


def test_post_users_still_creates_an_account(app, engine) -> None:
    admin = _user(engine, ("ADMINISTRADOR",))

    response = app.test_client().post(
        PATH,
        json={
            "email": f"nuevo-{uuid.uuid4()}@synthetic.test",
            "password": PASSWORD,
            "first_name": "Nuevo",
            "last_name": "Usuario",
            "roles": ["PACIENTE"],
        },
        headers=_headers(app, admin),
    )

    assert response.status_code == 201
    assert uuid.UUID(response.get_json()["id"])
