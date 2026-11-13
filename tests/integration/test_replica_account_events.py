"""Account and patient state reach the replica through the outbox (unit 3).

The replica checks the account state (usuarios.estado), the role links and the patient
row without asking SQL Server. Every change to them emits an event in the same
transaction. Credentials and contact data are never projected.
"""

import json
import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.application.use_cases import IdentityService
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
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _register(client, email: str) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "first_name": "Ana", "last_name": "Sintetica"},
    )
    assert response.status_code == 202, response.get_json()


def _email() -> str:
    return f"paciente-{uuid.uuid4()}@synthetic.test"


def _events(engine: Engine, user_id: str) -> list[tuple[str, int, dict]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tipo_evento, version_agregado, payload FROM eventos_outbox "
                "WHERE agregado_tipo = 'usuario' AND agregado_id = :u "
                "ORDER BY version_agregado"
            ),
            {"u": user_id},
        ).all()
    return [(row.tipo_evento, row.version_agregado, json.loads(row.payload)) for row in rows]


def _user_id(engine: Engine, email: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT id FROM usuarios WHERE email = :e"), {"e": email}
            ).scalar_one()
        )


def test_registration_emits_the_account_roles_and_patient_snapshot(app, engine, mailer):
    email = _email()
    _register(app.test_client(), email)
    user_id = _user_id(engine, email)

    events = _events(engine, user_id)

    assert [name for name, _, _ in events] == ["usuario.creado"]
    _, version, payload = events[0]
    assert version == 1
    with engine.connect() as conn:
        estado = conn.execute(
            text("SELECT estado FROM usuarios WHERE id = :u"), {"u": user_id}
        ).scalar_one()
        roles = sorted(
            row[0]
            for row in conn.execute(
                text("SELECT rol_id FROM usuarios_roles WHERE usuario_id = :u"), {"u": user_id}
            ).all()
        )
        paciente_id = conn.execute(
            text("SELECT id FROM pacientes WHERE usuario_id = :u"), {"u": user_id}
        ).scalar_one()
    assert payload["usuario"] == {"id": user_id.lower(), "estado": estado}
    assert sorted(payload["roles"]) == roles
    assert payload["paciente"] == {"id": str(paciente_id).lower(), "usuario_id": user_id.lower()}


def test_activation_emits_the_new_account_state(app, engine, mailer):
    client = app.test_client()
    email = _email()
    _register(client, email)
    user_id = _user_id(engine, email)

    link = mailer.links[email].replace("http://testserver", "")
    response = client.get(link)
    assert response.status_code in (200, 302), response.get_json() if response.is_json else ""

    events = _events(engine, user_id)
    assert [name for name, _, _ in events] == ["usuario.creado", "usuario.activado"]
    assert events[1][1] == 2
    assert events[1][2] == {"usuario": {"id": user_id, "estado": "ACTIVO"}}


def test_no_credential_or_contact_data_is_projected(app, engine, mailer):
    email = _email()
    _register(app.test_client(), email)
    user_id = _user_id(engine, email)

    serialized = json.dumps(_events(engine, user_id))

    assert email not in serialized
    assert PASSWORD not in serialized
    assert "password" not in serialized.lower()
    assert "Sintetica" not in serialized
