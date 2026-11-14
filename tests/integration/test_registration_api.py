"""Patient self-registration end to end, against real SQL Server, with a fake mailer.

Brevo is never called: the fake mailer records the activation link so the test can
open it the way the patient would.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.application.use_cases import (
    REGISTRATION_ACCEPTED,
    IdentityService,
)
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.modules.identity.infrastructure.security import (
    Argon2PasswordHasher,
    JwtTokenIssuer,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PASSWORD = "synthetic-pass-123"


class RecordingMailer:
    def __init__(self) -> None:
        self.links: dict[str, str] = {}

    def send_activation(self, to_email: str, first_name: str, link: str) -> None:
        self.links[to_email] = link


@pytest.fixture
def mailer() -> RecordingMailer:
    return RecordingMailer()


@pytest.fixture
def app(
    migrated_engine: Engine, test_settings: Settings, mailer: RecordingMailer
) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    flask_app = create_app(settings)
    flask_app.extensions["medicalbooking.identity"] = IdentityService(
        directory=SqlUserDirectory(migrated_engine),
        hasher=Argon2PasswordHasher(),
        tokens=JwtTokenIssuer(KEY, 15),
        mailer=mailer,
        public_base_url="http://testserver",
        verification_hours=24,
    )
    yield flask_app


@pytest.fixture
def client(app: Flask):
    return app.test_client()


def _email(label: str) -> str:
    import uuid

    return f"paciente-{label}-{uuid.uuid4()}@synthetic.test"


def _register(client, email: str, password: str = PASSWORD):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "first_name": "Ana", "last_name": "Sintetica"},
    )


def _activation_path(mailer: RecordingMailer, email: str) -> str:
    link = mailer.links[email]
    return link.replace("http://testserver", "")


def test_registration_is_accepted_with_a_generic_message(client, mailer) -> None:
    email = _email("generic")

    response = _register(client, email)

    assert response.status_code == 202
    assert response.get_json() == {"message": REGISTRATION_ACCEPTED}


def test_duplicate_registration_gets_the_same_answer(client, mailer) -> None:
    email = _email("duplicate")
    first = _register(client, email)
    second = _register(client, email)

    assert first.status_code == second.status_code == 202
    assert first.get_json() == second.get_json()


def test_login_is_refused_until_the_link_is_opened(client, mailer) -> None:
    email = _email("pending")
    _register(client, email)

    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})

    assert response.status_code == 401


def test_opening_the_link_activates_the_patient(client, mailer, engine_of) -> None:
    email = _email("activate")
    _register(client, email)

    verified = client.get(_activation_path(mailer, email))
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    me = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {login.get_json()['access_token']}"}
    )

    assert verified.status_code == 200
    assert login.status_code == 200
    assert me.get_json()["roles"] == ["PACIENTE"]
    with engine_of.connect() as conn:
        patients = conn.execute(
            text(
                "SELECT COUNT(*) FROM pacientes p JOIN usuarios u ON u.id = p.usuario_id "
                "WHERE u.email = :e"
            ),
            {"e": email},
        ).scalar()
    assert patients == 1


def test_a_link_works_only_once(client, mailer) -> None:
    email = _email("reuse")
    _register(client, email)
    path = _activation_path(mailer, email)

    first = client.get(path)
    second = client.get(path)

    assert first.status_code == 200
    assert second.status_code == 422


def test_a_forged_token_is_refused_generically(client) -> None:
    response = client.get("/api/v1/auth/verify-email?token=forged-token-value")

    assert response.status_code == 422  # ValidationFailed, the API's convention
    assert response.get_json()["detail"] == (
        "The activation link is invalid, already used, or expired."
    )


def test_weak_password_is_rejected_before_anything_is_stored(client, mailer, engine_of) -> None:
    email = _email("weak")

    response = _register(client, email, password="short")

    assert response.status_code in (400, 422)
    assert email not in mailer.links
    with engine_of.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM usuarios WHERE email = :e"), {"e": email}
        ).scalar()
    assert count == 0


@pytest.fixture
def engine_of(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]
