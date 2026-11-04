"""The shared test accounts (migration 0007) can sign in and carry the right roles."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
ACCOUNTS = {
    "paciente1@test.com": ("PACIENTE", "Password1"),
    "paciente2@test.com": ("PACIENTE", "Password1"),
    "laboratorista1@test.com": ("LABORATORISTA", "Password1"),
    "medico1@test.com": ("MEDICO", "Password123"),
    "medico2@test.com": ("MEDICO", "Password1"),
    "administrador1@test.com": ("ADMINISTRADOR", "Password123"),
}


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    yield create_app(test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)}))


@pytest.mark.parametrize(("email", "role_and_password"), ACCOUNTS.items())
def test_each_account_signs_in_with_its_role(
    app: Flask, email: str, role_and_password: tuple[str, str]
) -> None:
    role, password = role_and_password
    client = app.test_client()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert token.status_code == 200

    me = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {token.get_json()['access_token']}"}
    )
    assert me.get_json()["roles"] == [role]


def test_the_first_doctor_has_access_to_the_first_patient(app: Flask) -> None:
    engine = app.extensions["medicalbooking.engine"]
    with engine.connect() as conn:
        grants = conn.execute(
            text(
                "SELECT COUNT(*) FROM accesos_paciente ap "
                "JOIN medicos m ON m.id = ap.medico_id "
                "JOIN usuarios du ON du.id = m.usuario_id "
                "JOIN pacientes p ON p.id = ap.paciente_id "
                "JOIN usuarios pu ON pu.id = p.usuario_id "
                "WHERE du.email = 'medico1@test.com' AND pu.email = 'paciente1@test.com' "
                "AND ap.revocado_en IS NULL"
            )
        ).scalar_one()
    assert grants == 1
