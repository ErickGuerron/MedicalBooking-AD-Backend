"""The known-password test accounts must never be created in production."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_PATH = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0007_test_users.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_0007", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_creates_no_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load()
    monkeypatch.setattr(migration, "Settings", lambda: SimpleNamespace(app_env="production"))

    def forbidden():
        raise AssertionError("the migration touched the database in production")

    monkeypatch.setattr(migration.op, "get_bind", forbidden)

    migration.upgrade()


def test_accounts_follow_the_role_number_format() -> None:
    migration = _load()
    emails = [f"{local}@{migration.EMAIL_DOMAIN}" for local, *_ in migration.ACCOUNTS]

    assert emails == [
        "paciente1@test.com",
        "paciente2@test.com",
        "laboratorista1@test.com",
        "medico1@test.com",
        "medico2@test.com",
        "admin1@test.com",
    ]
