"""End-to-end identity checks against real SQL Server.

Each test creates its own uniquely named accounts, so tests do not depend on order.
The test database is recreated per session by tests/conftest.py.
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


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def client(app: Flask):
    return app.test_client()


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _account(engine: Engine, role: str, *, status: str = "ACTIVO") -> tuple[str, str]:
    user_id = str(uuid.uuid4())
    email = f"{role.lower()}-{user_id}@synthetic.test"
    SqlUserDirectory(engine).create_user(
        NewUser(
            user_id=user_id,
            email=email,
            first_name="Sintetico",
            last_name=role.title(),
            password_hash=Argon2PasswordHasher().hash(PASSWORD),
            roles=(role,),
        )
    )
    if role == "MEDICO":
        # Clinicians need a professional profile; the public API does not create one yet.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO medicos (id, usuario_id, registro_profesional) "
                    "VALUES (NEWID(), :u, :reg)"
                ),
                {"u": user_id, "reg": f"REG-{user_id}"},
            )
    if status != "ACTIVO":
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE usuarios SET estado = :s WHERE id = :u"), {"s": status, "u": user_id}
            )
    return user_id, email


def _patient_id(engine: Engine, user_id: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT id FROM pacientes WHERE usuario_id = :u"), {"u": user_id}
            ).scalar_one()
        )


def _grant(engine: Engine, doctor_user_id: str, patient_id: str, *, expired: bool = False) -> None:
    expiry = "DATEADD(day, -1, SYSUTCDATETIME())" if expired else "NULL"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO accesos_paciente (id, paciente_id, medico_id, alcance, fundamento, "
                "otorgado_en, expira_en, autorizado_por) "
                "SELECT NEWID(), :p, m.id, 'RESULTADOS', 'CONSENTIMIENTO', "
                f"DATEADD(day, -2, SYSUTCDATETIME()), {expiry}, :d "
                "FROM medicos m WHERE m.usuario_id = :d"
            ),
            {"p": patient_id, "d": doctor_user_id},
        )


def _login(client, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _token(client, email: str) -> str:
    response = _login(client, email)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- login and current user ----------


def test_login_returns_bearer_token_and_me_lists_permissions(client, engine) -> None:
    _, email = _account(engine, "PACIENTE")

    response = _login(client, email)
    body = response.get_json()
    me = client.get("/api/v1/me", headers=_auth(body["access_token"]))

    assert response.status_code == 200
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert me.status_code == 200
    assert me.get_json()["roles"] == ["PACIENTE"]
    assert "citas.reservar" in me.get_json()["permissions"]
    assert "usuarios.administrar" not in me.get_json()["permissions"]


def test_wrong_password_and_unknown_email_return_identical_problems(client, engine) -> None:
    _, email = _account(engine, "PACIENTE")

    wrong = _login(client, email, "wrong-password-999")
    unknown = _login(client, f"nobody-{uuid.uuid4()}@synthetic.test")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.mimetype == "application/problem+json"
    assert wrong.get_json()["detail"] == unknown.get_json()["detail"] == "Invalid credentials"


def test_inactive_account_loses_access_even_with_a_valid_token(client, engine) -> None:
    user_id, email = _account(engine, "PACIENTE")
    token = _token(client, email)

    with engine.begin() as conn:
        conn.execute(text("UPDATE usuarios SET estado = 'BLOQUEADO' WHERE id = :u"), {"u": user_id})

    assert client.get("/api/v1/me", headers=_auth(token)).status_code == 401


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer", "Bearer not.a.jwt"])
def test_protected_endpoints_reject_missing_or_malformed_credentials(client, header) -> None:
    headers = {} if header is None else {"Authorization": header}

    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_login_rejects_extra_fields_and_never_echoes_the_password(client) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "a@x.test", "password": "leaked-secret-value", "role": "ADMINISTRADOR"},
    )

    assert response.status_code == 422
    assert "leaked-secret-value" not in response.get_data(as_text=True)


# ---------- user administration ----------


def test_administrator_creates_user_and_duplicate_email_conflicts(client, engine) -> None:
    _, admin_email = _account(engine, "ADMINISTRADOR")
    admin = _token(client, admin_email)
    new_email = f"nuevo-{uuid.uuid4()}@synthetic.test"
    payload = {
        "email": new_email,
        "password": "long-enough-pass-1",
        "first_name": "Nuevo",
        "last_name": "Usuario",
        "roles": ["PACIENTE"],
    }

    created = client.post("/api/v1/users", json=payload, headers=_auth(admin))
    duplicate = client.post("/api/v1/users", json=payload, headers=_auth(admin))

    assert created.status_code == 201
    assert uuid.UUID(created.get_json()["id"])
    assert duplicate.status_code == 409
    # The new patient can log in with the password that was set.
    assert _login(client, new_email, "long-enough-pass-1").status_code == 200


def test_patient_cannot_create_users(client, engine) -> None:
    _, email = _account(engine, "PACIENTE")

    response = client.post(
        "/api/v1/users",
        json={
            "email": f"x-{uuid.uuid4()}@synthetic.test",
            "password": "long-enough-pass-1",
            "first_name": "X",
            "last_name": "Y",
            "roles": ["ADMINISTRADOR"],
        },
        headers=_auth(_token(client, email)),
    )

    assert response.status_code == 403


# ---------- ownership and the role matrix ----------


def test_patient_reads_only_own_record(client, engine) -> None:
    owner_user, owner_email = _account(engine, "PACIENTE")
    _, other_email = _account(engine, "PACIENTE")
    own_patient = _patient_id(engine, owner_user)
    other_patient = _patient_id(engine, _account(engine, "PACIENTE")[0])
    owner_token = _token(client, owner_email)
    other_token = _token(client, other_email)

    own = client.get(f"/api/v1/patients/{own_patient}", headers=_auth(owner_token))
    cross = client.get(f"/api/v1/patients/{other_patient}", headers=_auth(other_token))
    missing = client.get(f"/api/v1/patients/{uuid.uuid4()}", headers=_auth(owner_token))

    assert own.status_code == 200
    assert own.get_json()["usuario_id"] == owner_user
    assert cross.status_code == 403
    assert missing.status_code == 403
    assert cross.get_json()["detail"] == missing.get_json()["detail"]


def test_clinician_reads_only_patients_with_an_active_grant(client, engine) -> None:
    doctor, doctor_email = _account(engine, "MEDICO")
    patient = _patient_id(engine, _account(engine, "PACIENTE")[0])
    token = _token(client, doctor_email)

    before_grant = client.get(f"/api/v1/patients/{patient}", headers=_auth(token))
    _grant(engine, doctor, patient)
    after_grant = client.get(f"/api/v1/patients/{patient}", headers=_auth(token))

    assert before_grant.status_code == 403
    assert after_grant.status_code == 200


def test_expired_grant_does_not_authorize_access(client, engine) -> None:
    doctor, doctor_email = _account(engine, "MEDICO")
    patient = _patient_id(engine, _account(engine, "PACIENTE")[0])
    _grant(engine, doctor, patient, expired=True)

    response = client.get(
        f"/api/v1/patients/{patient}", headers=_auth(_token(client, doctor_email))
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [
        ("PACIENTE", 403),  # owns no grant over another patient
        ("LABORATORISTA", 403),  # no patient-read capability
        ("ADMINISTRADOR", 200),  # administrator reads any record
    ],
)
def test_role_matrix_for_patient_read(client, engine, role: str, expected_status: int) -> None:
    patient = _patient_id(engine, _account(engine, "PACIENTE")[0])
    _, email = _account(engine, role)

    response = client.get(f"/api/v1/patients/{patient}", headers=_auth(_token(client, email)))

    assert response.status_code == expected_status


def test_permission_matrix_is_loaded_from_the_database(client, engine) -> None:
    _, email = _account(engine, "LABORATORISTA")

    permissions = client.get("/api/v1/me", headers=_auth(_token(client, email))).get_json()[
        "permissions"
    ]

    # E5 (migration 0006) grants laboratory staff read access to validated knowledge.
    assert set(permissions) == {"resultados.registrar", "conocimiento.leer"}
