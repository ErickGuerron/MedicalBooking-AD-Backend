"""Login lockout against real SQL Server: counter, lock window, and reset."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.application.ports import NewUser
from medicalbooking.modules.identity.domain.policies import MAX_FAILED_LOGINS
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.modules.identity.infrastructure.security import Argon2PasswordHasher

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PASSWORD = "synthetic-pass-123"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _account(engine: Engine) -> str:
    user_id = str(uuid.uuid4())
    SqlUserDirectory(engine).create_user(
        NewUser(
            user_id=user_id,
            email=f"lock-{user_id}@synthetic.test",
            first_name="Sintetico",
            last_name="Bloqueo",
            password_hash=Argon2PasswordHasher().hash(PASSWORD),
            roles=("PACIENTE",),
        )
    )
    return user_id


def _email(engine: Engine, user_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT email FROM usuarios WHERE id = :u"), {"u": user_id}
        ).scalar_one()


def _login(app: Flask, email: str, password: str):
    return app.test_client().post("/api/v1/auth/login", json={"email": email, "password": password})


def _state(engine: Engine, user_id: str) -> tuple[int, datetime | None]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.intentos_fallidos, u.bloqueado_hasta FROM credenciales_usuario c "
                "JOIN usuarios u ON u.id = c.usuario_id WHERE c.usuario_id = :u"
            ),
            {"u": user_id},
        ).one()
    return row.intentos_fallidos, row.bloqueado_hasta


def test_repeated_failures_lock_the_account_even_with_the_right_password(app, engine) -> None:
    user = _account(engine)
    email = _email(engine, user)

    for _ in range(MAX_FAILED_LOGINS):
        assert _login(app, email, "wrong-password-9").status_code == 401

    locked = _login(app, email, PASSWORD)
    counter, until = _state(engine, user)

    assert locked.status_code == 401
    assert counter == MAX_FAILED_LOGINS
    assert until is not None and until > datetime.now(UTC).replace(tzinfo=None)


def test_expired_lock_allows_login_and_clears_the_counter(app, engine) -> None:
    user = _account(engine)
    email = _email(engine, user)
    for _ in range(MAX_FAILED_LOGINS):
        _login(app, email, "wrong-password-9")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE usuarios SET bloqueado_hasta = :past WHERE id = :u"),
            {"past": datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1), "u": user},
        )

    response = _login(app, email, PASSWORD)

    assert response.status_code == 200
    assert _state(engine, user) == (0, None)


def test_a_success_before_the_threshold_resets_the_counter(app, engine) -> None:
    user = _account(engine)
    email = _email(engine, user)
    for _ in range(MAX_FAILED_LOGINS - 1):
        _login(app, email, "wrong-password-9")

    assert _login(app, email, PASSWORD).status_code == 200
    for _ in range(MAX_FAILED_LOGINS - 1):
        _login(app, email, "wrong-password-9")

    counter, until = _state(engine, user)
    assert counter == MAX_FAILED_LOGINS - 1
    assert until is None
    assert _login(app, email, PASSWORD).status_code == 200


def test_failure_after_an_expired_lock_starts_a_new_window(app, engine) -> None:
    user = _account(engine)
    email = _email(engine, user)
    for _ in range(MAX_FAILED_LOGINS):
        _login(app, email, "wrong-password-9")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE usuarios SET bloqueado_hasta = :past WHERE id = :u"),
            {"past": datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1), "u": user},
        )

    _login(app, email, "wrong-password-9")

    # The first failure of the new window opens it at one attempt and clears the old lock.
    assert _state(engine, user) == (1, None)

    # Failures keep accumulating: the account locks again on the fifth failure, not earlier.
    for _ in range(MAX_FAILED_LOGINS - 2):
        _login(app, email, "wrong-password-9")
    assert _state(engine, user)[0] == MAX_FAILED_LOGINS - 1

    _login(app, email, "wrong-password-9")
    counter, until = _state(engine, user)
    assert counter == MAX_FAILED_LOGINS
    assert until is not None and until > datetime.now(UTC).replace(tzinfo=None)
    assert _login(app, email, PASSWORD).status_code == 401
