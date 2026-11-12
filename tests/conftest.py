"""Shared fixtures.

Integration fixtures use a dedicated ``MedicalBooking_test`` database that is dropped,
recreated and migrated from scratch once per session. The development database is
never touched by the test suite.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, text

from medicalbooking.config import Settings
from medicalbooking.shared.infrastructure.database import create_database_engine

ROOT = Path(__file__).resolve().parents[1]
# Parallel worktrees each set their own name so concurrent sessions never drop each other's database.
TEST_DATABASE = os.environ.get("MEDICALBOOKING_TEST_DATABASE", "MedicalBooking_test")
# The name is interpolated into DROP DATABASE, so only plain identifiers are accepted.
if not re.fullmatch(r"[A-Za-z0-9_]{1,100}", TEST_DATABASE):
    raise RuntimeError("MEDICALBOOKING_TEST_DATABASE must be a plain identifier")


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(sqlserver_database=TEST_DATABASE)


def _master_engine(settings: Settings) -> Engine:
    master = settings.model_copy(update={"sqlserver_database": "master"})
    return create_database_engine(master).execution_options(isolation_level="AUTOCOMMIT")


def _recreate_database(settings: Settings) -> None:
    engine = _master_engine(settings)
    with engine.connect() as conn:
        conn.execute(
            text(
                f"IF DB_ID(N'{TEST_DATABASE}') IS NOT NULL BEGIN "
                f"ALTER DATABASE [{TEST_DATABASE}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                f"DROP DATABASE [{TEST_DATABASE}]; END"
            )
        )
        conn.execute(text(f"CREATE DATABASE [{TEST_DATABASE}]"))
    engine.dispose()


def _migrate(settings: Settings) -> None:
    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(ROOT / "migrations"))
    # Alembic config interpolates '%'; the URL is URL-encoded, so escape it like env.py does.
    alembic_cfg.set_main_option("sqlalchemy.url", settings.sqlalchemy_url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="session")
def migrated_engine(test_settings: Settings) -> Iterator[Engine]:
    _recreate_database(test_settings)
    _migrate(test_settings)
    engine = create_database_engine(test_settings)
    yield engine
    engine.dispose()


@pytest.fixture
def db(migrated_engine: Engine) -> Iterator[Connection]:
    """A connection whose work is rolled back after each test."""
    connection = migrated_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()
