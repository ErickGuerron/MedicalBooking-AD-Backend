"""A signed-in account changes its password, proving the current one first.

Against real SQL Server: the new password works at the next sign-in and the old one stops working;
a wrong current password is a 403 that keeps the session alive (the portal signs out on 401).
"""

from __future__ import annotations

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_scheduling_api import PASSWORD, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
NEW = "Nueva-Clave-Segura-2026"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings, tmp_path_factory) -> Flask:
    documents = tmp_path_factory.mktemp("password-change")
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "lab_documents_dir": str(documents)}
    )
    return create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _change(app: Flask, headers, current: str, new: str):
    return app.test_client().post(
        "/api/v1/me/password",
        json={"current_password": current, "new_password": new},
        headers=headers,
    )


def _login(app: Flask, engine: Engine, user: str, password: str):
    with engine.connect() as conn:
        email = conn.execute(
            text("SELECT email FROM usuarios WHERE id = :u"), {"u": user}
        ).scalar_one()
    return app.test_client().post("/api/v1/auth/login", json={"email": email, "password": password})


def test_the_new_password_works_and_the_old_one_stops_working(app, engine) -> None:
    user = _user(engine, "PACIENTE")
    headers = _token(app, user)

    response = _change(app, headers, PASSWORD, NEW)

    assert response.status_code == 204, response.get_json()
    assert _login(app, engine, user, NEW).status_code == 200
    assert _login(app, engine, user, PASSWORD).status_code == 401


def test_a_wrong_current_password_is_refused_and_the_session_stays_alive(app, engine) -> None:
    user = _user(engine, "PACIENTE")
    headers = _token(app, user)

    response = _change(app, headers, "not-the-password-123", NEW)

    assert response.status_code == 403
    assert response.get_json()["detail"] == "La contraseña actual no es correcta"
    assert app.test_client().get("/api/v1/me", headers=headers).status_code == 200
    assert _login(app, engine, user, PASSWORD).status_code == 200


def test_the_new_password_must_differ_from_the_current_one(app, engine) -> None:
    user = _user(engine, "PACIENTE")

    response = _change(app, _token(app, user), PASSWORD, PASSWORD)

    assert response.status_code == 422


def test_the_new_password_needs_twelve_characters(app, engine) -> None:
    user = _user(engine, "PACIENTE")

    response = _change(app, _token(app, user), PASSWORD, "corta-123")

    assert response.status_code == 422


def test_changing_the_password_needs_a_session(app) -> None:
    response = app.test_client().post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": NEW},
    )

    assert response.status_code == 401
