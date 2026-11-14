"""Demo seed checks against the migrated test database.

The seed writes accounts and bookings through the application services, so every account and
every booking must have its outbox event. main() runs for real here, with the same Settings
the suite uses (SQLSERVER_* variables) and a demo database name taken from the test database.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import URL

from medicalbooking.config import Settings
from medicalbooking.modules.identity.infrastructure.security import Argon2PasswordHasher
from tests.conftest import TEST_DATABASE

pytestmark = pytest.mark.integration

_SPEC = importlib.util.spec_from_file_location(
    "seed_demo", Path(__file__).parents[2] / "scripts" / "seed_demo.py"
)
seed = importlib.util.module_from_spec(_SPEC)
# Registered before execution: dataclasses look up their module in sys.modules.
sys.modules[_SPEC.name] = seed
_SPEC.loader.exec_module(seed)

DEMO_LIKE = "%@demo.test"
EXPECTED_ROLES = {
    "administrador@demo.test": "ADMINISTRADOR",
    "laboratorista@demo.test": "LABORATORISTA",
    "medico1@demo.test": "MEDICO",
    "medico2@demo.test": "MEDICO",
    **{f"paciente{n}@demo.test": "PACIENTE" for n in range(1, 7)},
}


def _set_environment(mp: pytest.MonkeyPatch, settings: Settings) -> None:
    """Point the seed at the test database: the same name in the guard and in Settings."""
    mp.delenv("APP_ENV", raising=False)
    mp.setenv("SQLSERVER_DATABASE", TEST_DATABASE)
    url = URL.create(
        "mssql+pyodbc",
        database=TEST_DATABASE,
        query={"odbc_connect": settings.odbc_connection_string},
    )
    mp.setenv("AZURE_DATABASE_URL", url.render_as_string(hide_password=False))


@pytest.fixture(autouse=True)
def seed_environment(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    _set_environment(monkeypatch, test_settings)


def _argv(credentials: Path, confirm: str = TEST_DATABASE) -> list[str]:
    return ["--confirm", confirm, "--credentials-out", str(credentials)]


@pytest.fixture(scope="module")
def seeded(
    migrated_engine: Engine, test_settings: Settings, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Path]:
    """Run the seed once for the module; the credentials file lands outside the repository."""
    credentials = tmp_path_factory.mktemp("demo-seed") / "credentials.json"
    with pytest.MonkeyPatch.context() as mp:
        _set_environment(mp, test_settings)
        assert seed.main(_argv(credentials)) == 0
    yield credentials


def _rows(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(text(sql), params).all())


def _canonical(value: object) -> str:
    return str(uuid.UUID(str(value)))


def _snapshot(engine: Engine) -> dict[str, int]:
    """Row counts of every table the seed can touch, plus the outbox."""
    tables = ("usuarios", "pacientes", "medicos", "citas", "horarios_disponibles", "eventos_outbox")
    with engine.connect() as conn:
        return {
            table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in tables
        }


def test_accounts_have_their_roles_and_a_verifiable_password(
    seeded: Path, engine: Engine
) -> None:
    rows = _rows(
        engine,
        "SELECT u.email, r.nombre, c.password_hash FROM usuarios u "
        "JOIN usuarios_roles ur ON ur.usuario_id = u.id "
        "JOIN roles r ON r.id = ur.rol_id "
        "JOIN credenciales_usuario c ON c.usuario_id = u.id "
        "WHERE u.email LIKE :domain",
        domain=DEMO_LIKE,
    )
    roles_by_email = {row.email: row.nombre for row in rows}
    hash_by_email = {row.email: row.password_hash for row in rows}
    assert roles_by_email == EXPECTED_ROLES

    hasher = Argon2PasswordHasher()
    accounts = json.loads(seeded.read_text(encoding="utf-8"))["accounts"]
    assert {account["email"] for account in accounts} == set(EXPECTED_ROLES)
    for account in accounts:
        assert hasher.verify(hash_by_email[account["email"]], account["password"])


def test_every_account_and_booking_has_an_outbox_event(seeded: Path, engine: Engine) -> None:
    account_ids = {
        _canonical(row.id)
        for row in _rows(engine, "SELECT id FROM usuarios WHERE email LIKE :d", d=DEMO_LIKE)
    }
    booking_ids = {
        _canonical(row.id)
        for row in _rows(
            engine,
            "SELECT c.id FROM citas c JOIN pacientes p ON p.id = c.paciente_id "
            "JOIN usuarios u ON u.id = p.usuario_id WHERE u.email LIKE :d",
            d=DEMO_LIKE,
        )
    }
    created = {
        _canonical(row.agregado_id)
        for row in _rows(
            engine,
            "SELECT agregado_id FROM eventos_outbox WHERE tipo_evento = 'usuario.creado'",
        )
    }
    reserved = {
        _canonical(row.agregado_id)
        for row in _rows(
            engine,
            "SELECT agregado_id FROM eventos_outbox WHERE tipo_evento = 'cita.reservada'",
        )
    }

    assert len(account_ids) == len(EXPECTED_ROLES)
    assert account_ids <= created
    assert len(booking_ids) == 6  # one booking per patient
    assert booking_ids <= reserved


def test_doctors_get_a_professional_profile_through_the_service(
    seeded: Path, engine: Engine
) -> None:
    rows = _rows(
        engine,
        "SELECT u.email, m.registro_profesional, m.duracion_cita_min, m.activo FROM medicos m "
        "JOIN usuarios u ON u.id = m.usuario_id WHERE u.email LIKE :d",
        d=DEMO_LIKE,
    )

    profiles = {
        row.email: (row.registro_profesional, row.duracion_cita_min, bool(row.activo))
        for row in rows
    }
    assert profiles == {
        "medico1@demo.test": ("DEMO-MEDICO1", 30, True),
        "medico2@demo.test": ("DEMO-MEDICO2", 30, True),
    }


def test_demo_patients_pass_the_smoking_check_constraint(seeded: Path, engine: Engine) -> None:
    rows = _rows(
        engine,
        "SELECT p.fumador, p.cigarrillos_dia FROM pacientes p "
        "JOIN usuarios u ON u.id = p.usuario_id WHERE u.email LIKE :d",
        d=DEMO_LIKE,
    )

    assert len(rows) == 6
    assert all(not row.fumador and row.cigarrillos_dia is None for row in rows)


def test_booking_key_is_deterministic_and_a_valid_idempotency_key() -> None:
    from medicalbooking.modules.scheduling.domain.policies import validate_idempotency_key

    patient = seed.Account(role="PACIENTE", local="paciente1", email="x", password="", user_id="")
    slot = seed.Slot(id="s", doctor_local="medico2", start=datetime(2026, 11, 16, 18, 30))

    key = seed._booking_key(patient, slot)

    assert key == seed._booking_key(patient, slot)
    assert key == "demo-paciente1-medico2-202611161830"
    validate_idempotency_key(key)


def test_second_run_adds_nothing(seeded: Path, engine: Engine, tmp_path: Path, capsys) -> None:
    before = _snapshot(engine)

    assert seed.main(_argv(tmp_path / "again.json")) == 0

    assert _snapshot(engine) == before
    assert "Already seeded" in capsys.readouterr().out
    assert not (tmp_path / "again.json").exists()


def test_production_without_allow_flag_is_refused_and_writes_nothing(
    seeded: Path, engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _snapshot(engine)
    monkeypatch.setenv("APP_ENV", "production")

    assert seed.main(_argv(tmp_path / "prod.json")) == 2

    assert _snapshot(engine) == before
    assert not (tmp_path / "prod.json").exists()


def test_wrong_confirm_is_refused_and_writes_nothing(
    seeded: Path, engine: Engine, tmp_path: Path
) -> None:
    before = _snapshot(engine)

    assert seed.main(_argv(tmp_path / "wrong.json", confirm="Not_The_Database")) == 2

    assert _snapshot(engine) == before
    assert not (tmp_path / "wrong.json").exists()
